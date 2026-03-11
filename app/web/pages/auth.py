from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, aliased

from app.core.security import SUPERADMIN_INVITE_CODE, generate_csrf_token
from app.db.models import (
    AuditLog,
    Account,
    Conversation,
    ConversationReport,
    Listing,
    ListingReport,
    LookingForReport,
    ManagementAccount,
    Message,
    Review,
    SellerReport,
    SellerVerificationRequest,
    Transaction,
    UserProfile,
)
from app.db.session import get_db
from app.services.audit import create_audit_log
from app.services.auth import (
    AuthServiceError,
    RegistrationData,
    authenticate_account,
    get_management_session_timeout_minutes,
    register_account,
    set_management_session_timeout_minutes,
)
from app.services.messaging import (
    create_message_record,
    create_user_notification,
    serialize_message,
    serialize_notification,
)
from app.services.realtime import realtime_hub, run_async_from_sync

router = APIRouter(tags=["web-auth"])
templates = Jinja2Templates(directory="app/templates")
WEB_ALLOWED_ACCOUNT_TYPES = {"management", "superadmin"}


def _ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = generate_csrf_token()
        request.session["csrf_token"] = token
    return token


def _verify_csrf(request: Request, submitted_token: str) -> None:
    session_token = request.session.get("csrf_token")
    if not session_token or session_token != submitted_token:
        raise AuthServiceError("Invalid form token")


def _clear_account_session(request: Request) -> None:
    request.session.pop("account", None)
    request.session.pop("account_expires_at", None)


def _get_session_account(request: Request) -> dict | None:
    account = request.session.get("account")
    if not account:
        return None

    expires_at = request.session.get("account_expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            _clear_account_session(request)
            return None
        if expiry <= datetime.now(timezone.utc):
            _clear_account_session(request)
            return None

    return account


def _require_web_session(request: Request) -> dict:
    account = _get_session_account(request)
    if not account or account.get("account_type") not in WEB_ALLOWED_ACCOUNT_TYPES:
        _clear_account_session(request)
        raise AuthServiceError("Please sign in again")
    return account


def _find_staff_user_conversation(
    db: Session,
    *,
    staff_account_id: int,
    user_account_id: int,
) -> Conversation | None:
    return (
        db.query(Conversation)
        .filter(
            or_(
                and_(
                    Conversation.participant1_id == staff_account_id,
                    Conversation.participant2_id == user_account_id,
                ),
                and_(
                    Conversation.participant1_id == user_account_id,
                    Conversation.participant2_id == staff_account_id,
                ),
            )
        )
        .first()
    )


def _get_or_create_staff_user_conversation(
    db: Session,
    *,
    staff_account_id: int,
    user_account_id: int,
    conversation_type: str = "staff_support",
) -> Conversation:
    conversation = _find_staff_user_conversation(
        db,
        staff_account_id=staff_account_id,
        user_account_id=user_account_id,
    )
    if conversation is not None:
        return conversation

    conversation = Conversation(
        participant1_id=staff_account_id,
        participant2_id=user_account_id,
        conversation_type=conversation_type,
    )
    db.add(conversation)
    db.flush()
    return conversation


def _render_auth_page(
    request: Request,
    *,
    template_name: str,
    active_role: str = "management",
    error: str | None = None,
    success: str | None = None,
    form_data: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
):
    csrf_token = _ensure_csrf_token(request)
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "title": "Secure Access",
            "active_role": active_role,
            "error": error,
            "success": success,
            "form_data": form_data or {},
            "csrf_token": csrf_token,
            "superadmin_enabled": bool(SUPERADMIN_INVITE_CODE),
            "session_account": _get_session_account(request),
        },
        status_code=status_code,
    )


def _dispatch_account_event(account_id: int, payload: dict) -> None:
    try:
        run_async_from_sync(realtime_hub.send_account_event, account_id, payload)
    except RuntimeError:
        pass


def _dispatch_conversation_event(conversation_id: int, payload: dict) -> None:
    try:
        run_async_from_sync(realtime_hub.broadcast_conversation, conversation_id, payload)
    except RuntimeError:
        pass


def _dispatch_management_event(payload: dict) -> None:
    try:
        run_async_from_sync(realtime_hub.broadcast_management_event, payload)
    except RuntimeError:
        pass


def _create_user_notification_payload(
    db: Session,
    *,
    user_id: int,
    notification_type: str,
    title: str,
    body: str,
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
) -> dict:
    notification = create_user_notification(
        db,
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        body=body,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )
    return serialize_notification(notification)


def _emit_user_notification(account_id: int, payload: dict) -> None:
    _dispatch_account_event(
        account_id,
        {
            "type": "notification.created",
            "notification": payload,
        },
    )


def _build_dashboard_context(request: Request, db: Session) -> dict:
    account = _require_web_session(request)
    csrf_token = _ensure_csrf_token(request)
    total_users = (
        db.query(func.count(Account.account_id))
        .filter(Account.account_type == "user")
        .scalar()
        or 0
    )
    sellers_count = (
        db.query(func.count(func.distinct(Listing.seller_id)))
        .filter(Listing.seller_id.isnot(None))
        .scalar()
        or 0
    )
    buyers_count = (
        db.query(func.count(func.distinct(Transaction.buyer_id)))
        .filter(Transaction.buyer_id.isnot(None))
        .scalar()
        or 0
    )
    listings_count = db.query(func.count(Listing.listing_id)).scalar() or 0
    open_listing_reports = (
        db.query(func.count(ListingReport.report_id))
        .filter(ListingReport.status == "open")
        .scalar()
        or 0
    )
    open_looking_for_reports = (
        db.query(func.count(LookingForReport.report_id))
        .filter(LookingForReport.status == "open")
        .scalar()
        or 0
    )
    open_chat_reports = (
        db.query(func.count(ConversationReport.report_id))
        .filter(ConversationReport.status == "open")
        .scalar()
        or 0
    )
    open_seller_reports = (
        db.query(func.count(SellerReport.report_id))
        .filter(SellerReport.status == "open")
        .scalar()
        or 0
    )
    reported_sellers_count = (
        db.query(func.count(func.distinct(SellerReport.seller_id)))
        .filter(SellerReport.status == "open")
        .scalar()
        or 0
    )

    verification_requests = (
        db.query(
            SellerVerificationRequest.request_id,
            SellerVerificationRequest.user_id,
            SellerVerificationRequest.status,
            SellerVerificationRequest.submission_note,
            SellerVerificationRequest.created_at,
            SellerVerificationRequest.review_note,
            Account.username,
            Account.email,
        )
        .join(Account, Account.account_id == SellerVerificationRequest.user_id)
        .order_by(
            SellerVerificationRequest.status.asc(),
            SellerVerificationRequest.created_at.desc(),
        )
        .all()
    )

    listings = (
        db.query(
            Listing.listing_id,
            Listing.title,
            Listing.listing_type,
            Listing.status,
            Listing.price,
            Listing.created_at,
            Account.username.label("seller_username"),
        )
        .outerjoin(Account, Account.account_id == Listing.seller_id)
        .order_by(Listing.created_at.desc())
        .limit(12)
        .all()
    )

    participant1 = aliased(Account)
    participant2 = aliased(Account)
    conversations = (
        db.query(
            Conversation.conversation_id,
            Conversation.conversation_type,
            Conversation.created_at,
            participant1.username.label("participant1_username"),
            participant2.username.label("participant2_username"),
            participant1.account_type.label("participant1_type"),
            participant2.account_type.label("participant2_type"),
            func.max(Message.sent_at).label("last_message_at"),
            func.count(Message.message_id).label("message_count"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.conversation_id)
        .outerjoin(participant1, participant1.account_id == Conversation.participant1_id)
        .outerjoin(participant2, participant2.account_id == Conversation.participant2_id)
        .filter(
            or_(
                and_(participant1.account_type.in_(WEB_ALLOWED_ACCOUNT_TYPES), participant2.account_type == "user"),
                and_(participant2.account_type.in_(WEB_ALLOWED_ACCOUNT_TYPES), participant1.account_type == "user"),
            )
        )
        .group_by(
            Conversation.conversation_id,
            Conversation.conversation_type,
            Conversation.created_at,
            participant1.username,
            participant2.username,
            participant1.account_type,
            participant2.account_type,
        )
        .order_by(func.max(Message.sent_at).desc().nullslast(), Conversation.created_at.desc())
        .limit(20)
        .all()
    )

    listing_reports = (
        db.query(
            Listing.listing_id,
            Listing.seller_id,
            Listing.title,
            Listing.listing_type,
            Listing.status,
            Account.username.label("seller_username"),
            func.count(ListingReport.report_id).label("report_count"),
            func.max(ListingReport.created_at).label("last_reported_at"),
        )
        .join(Listing, Listing.listing_id == ListingReport.listing_id)
        .outerjoin(Account, Account.account_id == Listing.seller_id)
        .filter(ListingReport.status == "open")
        .filter(or_(Listing.listing_type.is_(None), Listing.listing_type != "looking_for"))
        .group_by(
            Listing.listing_id,
            Listing.title,
            Listing.listing_type,
            Listing.status,
            Account.username,
        )
        .order_by(
            func.count(ListingReport.report_id).desc(),
            func.max(ListingReport.created_at).desc(),
        )
        .limit(12)
        .all()
    )

    looking_for_reports = (
        db.query(
            Listing.listing_id,
            Listing.seller_id.label("requester_id"),
            Listing.title,
            Listing.status,
            Account.username.label("requester_username"),
            func.count(LookingForReport.report_id).label("report_count"),
            func.max(LookingForReport.created_at).label("last_reported_at"),
        )
        .join(Listing, Listing.listing_id == LookingForReport.listing_id)
        .outerjoin(Account, Account.account_id == Listing.seller_id)
        .filter(LookingForReport.status == "open")
        .filter(Listing.listing_type == "looking_for")
        .group_by(
            Listing.listing_id,
            Listing.title,
            Listing.status,
            Account.username,
        )
        .order_by(
            func.count(LookingForReport.report_id).desc(),
            func.max(LookingForReport.created_at).desc(),
        )
        .limit(12)
        .all()
    )

    reported_account = aliased(Account)
    conversation_reports = (
        db.query(
            Conversation.conversation_id,
            participant1.username.label("participant1_username"),
            participant2.username.label("participant2_username"),
            reported_account.username.label("reported_username"),
            func.count(ConversationReport.report_id).label("report_count"),
            func.max(ConversationReport.created_at).label("last_reported_at"),
        )
        .join(Conversation, Conversation.conversation_id == ConversationReport.conversation_id)
        .outerjoin(participant1, participant1.account_id == Conversation.participant1_id)
        .outerjoin(participant2, participant2.account_id == Conversation.participant2_id)
        .outerjoin(reported_account, reported_account.account_id == ConversationReport.reported_account_id)
        .filter(ConversationReport.status == "open")
        .group_by(
            Conversation.conversation_id,
            participant1.username,
            participant2.username,
            reported_account.username,
        )
        .order_by(
            func.count(ConversationReport.report_id).desc(),
            func.max(ConversationReport.created_at).desc(),
        )
        .limit(12)
        .all()
    )

    seller_reports = (
        db.query(
            Account.account_id.label("seller_id"),
            Account.username,
            Account.account_status,
            Account.warning_count,
            Account.last_warned_at,
            func.count(SellerReport.report_id).label("report_count"),
            func.max(SellerReport.created_at).label("last_reported_at"),
        )
        .join(SellerReport, SellerReport.seller_id == Account.account_id)
        .filter(SellerReport.status == "open")
        .group_by(
            Account.account_id,
            Account.username,
            Account.account_status,
            Account.warning_count,
            Account.last_warned_at,
        )
        .order_by(
            func.count(SellerReport.report_id).desc(),
            func.max(SellerReport.created_at).desc(),
        )
        .limit(12)
        .all()
    )

    lowest_rated_seller = (
        db.query(
            Review.reviewee_id,
            Account.username,
            func.avg(Review.rating).label("average_rating"),
            func.count(Review.review_id).label("review_count"),
        )
        .join(Account, Account.account_id == Review.reviewee_id)
        .group_by(Review.reviewee_id, Account.username)
        .order_by(func.avg(Review.rating).asc(), func.count(Review.review_id).desc())
        .first()
    )

    timeout_minutes = get_management_session_timeout_minutes(db)
    recent_audit_logs = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )
    conversation_count = db.query(func.count(Conversation.conversation_id)).scalar() or 0
    management_users = (
        db.query(
            Account.account_id,
            Account.email,
            Account.username,
            Account.account_status,
            Account.created_at,
            ManagementAccount.first_name,
            ManagementAccount.last_name,
            ManagementAccount.role_name,
        )
        .join(ManagementAccount, ManagementAccount.manager_id == Account.account_id)
        .filter(Account.account_type == "management")
        .order_by(Account.created_at.desc())
        .all()
    )
    verification_chart = {
        "pending": sum(1 for row in verification_requests if row.status == "pending"),
        "approved": sum(1 for row in verification_requests if row.status == "approved"),
        "rejected": sum(1 for row in verification_requests if row.status == "rejected"),
    }
    total_open_reports = (
        open_listing_reports
        + open_looking_for_reports
        + open_chat_reports
        + open_seller_reports
    )
    listing_type_chart = {
        "single_item": sum(1 for row in listings if row.listing_type == "single_item"),
        "stock_item": sum(1 for row in listings if row.listing_type == "stock_item"),
        "looking_for": sum(1 for row in listings if row.listing_type == "looking_for"),
        "other": sum(
            1
            for row in listings
            if row.listing_type not in {"single_item", "stock_item", "looking_for"}
        ),
    }

    return {
        "request": request,
        "title": "Dashboard",
        "account": account,
        "csrf_token": csrf_token,
        "metrics": {
            "total_users": total_users,
            "buyers_count": buyers_count,
            "sellers_count": sellers_count,
            "listings_count": listings_count,
            "open_reports_count": total_open_reports,
            "reported_sellers_count": reported_sellers_count,
        },
        "verification_requests": verification_requests,
        "pending_verification_count": verification_chart["pending"],
        "pending_moderation_count": total_open_reports,
        "listings": listings,
        "listing_reports": listing_reports,
        "looking_for_reports": looking_for_reports,
        "conversation_reports": conversation_reports,
        "seller_reports": seller_reports,
        "conversations": conversations,
        "report_counts": {
            "listing": open_listing_reports,
            "looking_for": open_looking_for_reports,
            "chat": open_chat_reports,
            "seller": open_seller_reports,
        },
        "lowest_rated_seller": lowest_rated_seller,
        "management_timeout_minutes": timeout_minutes,
        "is_superadmin": account.get("account_type") == "superadmin",
        "recent_audit_logs": recent_audit_logs,
        "management_users": management_users,
        "conversation_count": conversation_count,
        "chart_data": {
            "marketplace_mix": {
                "users": total_users,
                "buyers": buyers_count,
                "sellers": sellers_count,
                "listings": listings_count,
            },
            "verification_status": verification_chart,
            "listing_types": listing_type_chart,
            "report_categories": {
                "listing": open_listing_reports,
                "looking_for": open_looking_for_reports,
                "chat": open_chat_reports,
                "seller": open_seller_reports,
            },
        },
    }


def _build_banned_users_context(
    db: Session,
    *,
    query_text: str = "",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    normalized_query = query_text.strip()
    current_page = max(page, 1)

    banned_users_query = (
        db.query(
            Account.account_id,
            Account.username,
            Account.email,
            Account.account_status,
            Account.warning_count,
            Account.last_warned_at,
            Account.created_at,
            UserProfile.campus,
            UserProfile.first_name,
            UserProfile.last_name,
        )
        .outerjoin(UserProfile, UserProfile.user_id == Account.account_id)
        .filter(Account.account_type == "user", Account.account_status == "banned")
    )

    if normalized_query:
        like_value = f"%{normalized_query.lower()}%"
        banned_users_query = banned_users_query.filter(
            or_(
                func.lower(Account.username).like(like_value),
                func.lower(Account.email).like(like_value),
                func.lower(UserProfile.first_name).like(like_value),
                func.lower(UserProfile.last_name).like(like_value),
                func.lower(UserProfile.campus).like(like_value),
            )
        )

    total_count = banned_users_query.count()
    total_pages = max((total_count + per_page - 1) // per_page, 1)
    current_page = min(current_page, total_pages)
    offset = (current_page - 1) * per_page

    rows = (
        banned_users_query.order_by(
            Account.last_warned_at.desc().nullslast(),
            Account.created_at.desc(),
            Account.account_id.desc(),
        )
        .offset(offset)
        .limit(per_page)
        .all()
    )

    pagination = {
        "page": current_page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
    }
    pagination["prev_url"] = (
        _build_moderation_redirect_url(
            banned_query=normalized_query,
            banned_page=current_page - 1,
        )
        if pagination["has_prev"]
        else None
    )
    pagination["next_url"] = (
        _build_moderation_redirect_url(
            banned_query=normalized_query,
            banned_page=current_page + 1,
        )
        if pagination["has_next"]
        else None
    )

    return {
        "query": normalized_query,
        "results": rows,
        "count": total_count,
        "pagination": pagination,
    }


def _build_moderation_redirect_url(*, banned_query: str = "", banned_page: int = 1) -> str:
    params: dict[str, str | int] = {}
    if banned_query.strip():
        params["banned_q"] = banned_query.strip()
    if banned_page > 1:
        params["banned_page"] = banned_page
    if not params:
        return "/dashboard/moderation"
    return f"/dashboard/moderation?{urlencode(params)}"


def _build_user_management_redirect_url(
    *,
    query: str = "",
    status_filter: str = "all",
    role_filter: str = "all",
    page: int = 1,
) -> str:
    params: dict[str, str | int] = {}
    if query.strip():
        params["q"] = query.strip()
    if status_filter != "all":
        params["status"] = status_filter
    if role_filter != "all":
        params["role"] = role_filter
    if page > 1:
        params["page"] = page
    if not params:
        return "/dashboard/users"
    return f"/dashboard/users?{urlencode(params)}"


def _build_user_management_context(
    db: Session,
    *,
    query_text: str = "",
    status_filter: str = "all",
    role_filter: str = "all",
    page: int = 1,
    per_page: int = 12,
) -> dict:
    normalized_query = query_text.strip()
    normalized_status = status_filter.strip().lower() if status_filter else "all"
    if normalized_status not in {"all", "active", "warned", "banned"}:
        normalized_status = "all"

    normalized_role = role_filter.strip().lower() if role_filter else "all"
    if normalized_role not in {"all", "buyers", "sellers", "both", "new"}:
        normalized_role = "all"

    current_page = max(page, 1)

    listing_counts = (
        db.query(
            Listing.seller_id.label("user_id"),
            func.count(Listing.listing_id).label("listing_count"),
        )
        .filter(Listing.seller_id.isnot(None))
        .group_by(Listing.seller_id)
        .subquery()
    )
    purchase_counts = (
        db.query(
            Transaction.buyer_id.label("user_id"),
            func.count(Transaction.transaction_id).label("purchase_count"),
        )
        .filter(Transaction.buyer_id.isnot(None))
        .group_by(Transaction.buyer_id)
        .subquery()
    )

    users_query = (
        db.query(
            Account.account_id,
            Account.username,
            Account.email,
            Account.account_status,
            Account.warning_count,
            Account.last_warned_at,
            Account.created_at,
            UserProfile.first_name,
            UserProfile.last_name,
            UserProfile.campus,
            UserProfile.is_verified,
            func.coalesce(listing_counts.c.listing_count, 0).label("listing_count"),
            func.coalesce(purchase_counts.c.purchase_count, 0).label("purchase_count"),
        )
        .outerjoin(UserProfile, UserProfile.user_id == Account.account_id)
        .outerjoin(listing_counts, listing_counts.c.user_id == Account.account_id)
        .outerjoin(purchase_counts, purchase_counts.c.user_id == Account.account_id)
        .filter(Account.account_type == "user")
    )

    if normalized_query:
        like_value = f"%{normalized_query.lower()}%"
        users_query = users_query.filter(
            or_(
                func.lower(Account.username).like(like_value),
                func.lower(Account.email).like(like_value),
                func.lower(UserProfile.first_name).like(like_value),
                func.lower(UserProfile.last_name).like(like_value),
                func.lower(UserProfile.campus).like(like_value),
                func.lower(Account.account_status).like(like_value),
            )
        )

    if normalized_status != "all":
        users_query = users_query.filter(Account.account_status == normalized_status)

    listing_count_expr = func.coalesce(listing_counts.c.listing_count, 0)
    purchase_count_expr = func.coalesce(purchase_counts.c.purchase_count, 0)
    if normalized_role == "buyers":
        users_query = users_query.filter(purchase_count_expr > 0, listing_count_expr == 0)
    elif normalized_role == "sellers":
        users_query = users_query.filter(listing_count_expr > 0, purchase_count_expr == 0)
    elif normalized_role == "both":
        users_query = users_query.filter(listing_count_expr > 0, purchase_count_expr > 0)
    elif normalized_role == "new":
        users_query = users_query.filter(listing_count_expr == 0, purchase_count_expr == 0)

    counts_source = users_query.with_entities(
        listing_count_expr.label("listing_count"),
        purchase_count_expr.label("purchase_count"),
        Account.account_status.label("account_status"),
    ).subquery()

    role_and_status_counts = db.query(
        func.count().label("total_count"),
        func.sum(
            case(
                (
                    and_(
                        counts_source.c.purchase_count > 0,
                        counts_source.c.listing_count == 0,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("buyers"),
        func.sum(
            case(
                (
                    and_(
                        counts_source.c.listing_count > 0,
                        counts_source.c.purchase_count == 0,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("sellers"),
        func.sum(
            case(
                (
                    and_(
                        counts_source.c.listing_count > 0,
                        counts_source.c.purchase_count > 0,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("both"),
        func.sum(
            case(
                (
                    and_(
                        counts_source.c.listing_count == 0,
                        counts_source.c.purchase_count == 0,
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("new"),
        func.sum(case((counts_source.c.account_status == "active", 1), else_=0)).label("active"),
        func.sum(case((counts_source.c.account_status == "warned", 1), else_=0)).label("warned"),
        func.sum(case((counts_source.c.account_status == "banned", 1), else_=0)).label("banned"),
    ).one()

    total_count = role_and_status_counts.total_count or 0
    total_pages = max((total_count + per_page - 1) // per_page, 1)
    current_page = min(current_page, total_pages)
    offset = (current_page - 1) * per_page

    rows = (
        users_query.order_by(Account.created_at.desc(), Account.account_id.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )

    role_counts = {
        "buyers": role_and_status_counts.buyers or 0,
        "sellers": role_and_status_counts.sellers or 0,
        "both": role_and_status_counts.both or 0,
        "new": role_and_status_counts.new or 0,
    }
    status_counts = {
        "active": role_and_status_counts.active or 0,
        "warned": role_and_status_counts.warned or 0,
        "banned": role_and_status_counts.banned or 0,
    }
    serialized_rows = []
    for row in rows:
        is_seller = (row.listing_count or 0) > 0
        is_buyer = (row.purchase_count or 0) > 0
        if is_seller and is_buyer:
            role_key = "both"
            role_label = "buyer + seller"
        elif is_seller:
            role_key = "sellers"
            role_label = "seller"
        elif is_buyer:
            role_key = "buyers"
            role_label = "buyer"
        else:
            role_key = "new"
            role_label = "new user"

        serialized_rows.append(
            {
                "account_id": row.account_id,
                "username": row.username,
                "email": row.email,
                "account_status": row.account_status or "active",
                "warning_count": row.warning_count or 0,
                "last_warned_at": row.last_warned_at,
                "created_at": row.created_at,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "campus": row.campus,
                "is_verified": bool(row.is_verified),
                "listing_count": row.listing_count or 0,
                "purchase_count": row.purchase_count or 0,
                "role_key": role_key,
                "role_label": role_label,
            }
        )

    pagination = {
        "page": current_page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
    }
    pagination["prev_url"] = (
        _build_user_management_redirect_url(
            query=normalized_query,
            status_filter=normalized_status,
            role_filter=normalized_role,
            page=current_page - 1,
        )
        if pagination["has_prev"]
        else None
    )
    pagination["next_url"] = (
        _build_user_management_redirect_url(
            query=normalized_query,
            status_filter=normalized_status,
            role_filter=normalized_role,
            page=current_page + 1,
        )
        if pagination["has_next"]
        else None
    )

    user_totals = (
        db.query(
            func.count(Account.account_id).label("total_users"),
            func.sum(case((Account.account_status == "active", 1), else_=0)).label("active_users"),
            func.sum(case((Account.account_status == "banned", 1), else_=0)).label("banned_users"),
        )
        .filter(Account.account_type == "user")
        .one()
    )

    return {
        "query": normalized_query,
        "status_filter": normalized_status,
        "role_filter": normalized_role,
        "results": serialized_rows,
        "count": total_count,
        "pagination": pagination,
        "summary": {
            "total_users": user_totals.total_users or 0,
            "active_users": user_totals.active_users or 0,
            "banned_users": user_totals.banned_users or 0,
        },
        "page_role_counts": role_counts,
        "page_status_counts": status_counts,
    }


@router.get("/auth", response_class=HTMLResponse)
def auth_portal(request: Request):
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request):
    if _get_session_account(request):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return _render_auth_page(request, template_name="login.html")


@router.get("/auth/register", response_class=HTMLResponse)
def register_page(request: Request):
    if _get_session_account(request):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return _render_auth_page(request, template_name="register.html")


def _validate_web_account_type(account_type: str) -> str:
    normalized = account_type.strip().lower()
    if normalized not in WEB_ALLOWED_ACCOUNT_TYPES:
        raise AuthServiceError("User accounts can only sign in through the app")
    return normalized


@router.post("/auth/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    identity: str = Form(...),
    password: str = Form(...),
    account_type: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account_type = _validate_web_account_type(account_type)
    except AuthServiceError as exc:
        create_audit_log(
            db,
            actor_account_id=None,
            actor_username=identity.strip() or None,
            actor_role=account_type,
            action="failed_login",
            target_type="account",
            target_label=identity.strip() or None,
            details=str(exc),
        )
        db.commit()
        return _render_auth_page(
            request,
            template_name="login.html",
            active_role="management",
            error=str(exc),
            form_data={"identity": identity},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        _verify_csrf(request, csrf_token)
        account = authenticate_account(
            db,
            identity=identity,
            password=password,
            account_type=account_type,
        )
    except AuthServiceError as exc:
        return _render_auth_page(
            request,
            template_name="login.html",
            active_role=account_type,
            error=str(exc),
            form_data={"identity": identity},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    request.session["account"] = {
        "account_id": account.account_id,
        "email": account.email,
        "username": account.username,
        "account_type": account.account_type,
    }
    if account.account_type == "management":
        management_minutes = get_management_session_timeout_minutes(db)
        request.session["account_expires_at"] = (
            datetime.now(timezone.utc) + timedelta(minutes=management_minutes)
        ).isoformat()
    else:
        request.session.pop("account_expires_at", None)

    create_audit_log(
        db,
        actor_account_id=account.account_id,
        actor_username=account.username,
        actor_role=account.account_type,
        action="login",
        target_type="account",
        target_id=str(account.account_id),
        target_label=account.username,
        details=f"Web login as {account.account_type}",
    )
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    account_type: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    campus: str = Form(""),
    role_name: str = Form(""),
    superadmin_code: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account_type = _validate_web_account_type(account_type)
    except AuthServiceError as exc:
        return _render_auth_page(
            request,
            template_name="register.html",
            active_role="management",
            error=str(exc),
            form_data={
                "email": email,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "role_name": role_name,
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if password != confirm_password:
        return _render_auth_page(
            request,
            template_name="register.html",
            active_role=account_type,
            error="Passwords do not match",
            form_data={
                "email": email,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "role_name": role_name,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        _verify_csrf(request, csrf_token)
        created_account = register_account(
            db,
            RegistrationData(
                email=email,
                username=username,
                password=password,
                account_type=account_type,
                first_name=first_name,
                last_name=last_name,
                campus=campus,
                role_name=role_name,
                superadmin_code=superadmin_code or None,
            ),
        )
    except AuthServiceError as exc:
        return _render_auth_page(
            request,
            template_name="register.html",
            active_role=account_type,
            error=str(exc),
            form_data={
                "email": email,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "role_name": role_name,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    create_audit_log(
        db,
        actor_account_id=created_account.account_id,
        actor_username=created_account.username,
        actor_role=created_account.account_type,
        action="register",
        target_type="account",
        target_id=str(created_account.account_id),
        target_label=created_account.username,
        details=f"Registered web account as {created_account.account_type}",
    )
    db.commit()

    return _render_auth_page(
        request,
        template_name="login.html",
        active_role=account_type,
        success="Registration complete. You can sign in now.",
        form_data={"identity": email},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    context.update(
        {
            "active_page": "overview",
            "page_title": "Marketplace Operations Dashboard",
            "page_description": "Moderation, verification, user metrics, and audit visibility in one place.",
        }
    )
    return templates.TemplateResponse("dashboard/overview.html", context)


@router.get("/dashboard/verifications", response_class=HTMLResponse)
def dashboard_verifications(request: Request, db: Session = Depends(get_db)):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    context.update(
        {
            "active_page": "verification",
            "page_title": "Seller Verification",
            "page_description": "Review, approve, and reject seller verification requests.",
        }
    )
    return templates.TemplateResponse("dashboard/verifications.html", context)


@router.get("/dashboard/moderation", response_class=HTMLResponse)
def dashboard_moderation(
    request: Request,
    banned_q: str = "",
    banned_page: int = 1,
    db: Session = Depends(get_db),
):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    banned_users = _build_banned_users_context(
        db,
        query_text=banned_q,
        page=banned_page,
    )
    context.update(
        {
            "active_page": "moderation",
            "page_title": "Trust and Safety",
            "page_description": "Review reported listings, looking-for posts, chats, and sellers from one moderation queue.",
            "banned_users_query": banned_users["query"],
            "banned_users": banned_users["results"],
            "banned_users_count": banned_users["count"],
            "banned_users_pagination": banned_users["pagination"],
        }
    )
    return templates.TemplateResponse("dashboard/moderation.html", context)


@router.get("/dashboard/messages", response_class=HTMLResponse)
def dashboard_messages(
    request: Request,
    conversation_id: int | None = None,
    db: Session = Depends(get_db),
):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    conversation_rows = context.get("conversations", [])

    selected_conversation = None
    if conversation_rows:
        selected_conversation = next(
            (row for row in conversation_rows if row.conversation_id == conversation_id),
            conversation_rows[0],
        )

    messages = []
    if selected_conversation is not None:
        messages = (
            db.query(
                Message.message_id,
                Message.message_text,
                Message.sent_at,
                Message.is_read,
                Message.sender_id,
                Account.username.label("sender_username"),
            )
            .outerjoin(Account, Account.account_id == Message.sender_id)
            .filter(Message.conversation_id == selected_conversation.conversation_id)
            .order_by(Message.sent_at.asc(), Message.message_id.asc())
            .all()
        )

    context.update(
        {
            "active_page": "messages",
            "page_title": "Messages",
            "page_description": "Review staff-to-user conversations and send dashboard replies inside the same chat thread.",
            "conversations": conversation_rows,
            "selected_conversation": selected_conversation,
            "conversation_messages": messages,
            "compose_text": "",
        }
    )
    return templates.TemplateResponse("dashboard/messages.html", context)


@router.get("/dashboard/search", response_class=HTMLResponse)
def dashboard_search(
    request: Request,
    q: str = "",
    tab: str = "listings",
    page: int = 1,
    db: Session = Depends(get_db),
):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    query_text = q.strip()
    selected_tab = tab if tab in {"listings", "accounts", "audit"} else "listings"
    current_page = max(page, 1)
    per_page = 10
    listings_results = []
    account_results = []
    audit_results = []
    listings_count = 0
    accounts_count = 0
    audit_count = 0

    if query_text:
        like_value = f"%{query_text.lower()}%"
        listings_query = (
            db.query(
                Listing.listing_id,
                Listing.seller_id,
                Listing.title,
                Listing.listing_type,
                Listing.status,
                Listing.created_at,
                Account.username.label("seller_username"),
            )
            .outerjoin(Account, Account.account_id == Listing.seller_id)
            .filter(
                or_(
                    func.lower(Listing.title).like(like_value),
                    func.lower(Listing.description).like(like_value),
                    func.lower(Listing.listing_type).like(like_value),
                    func.lower(Account.username).like(like_value),
                )
            )
        )
        accounts_query = (
            db.query(
                Account.account_id,
                Account.username,
                Account.email,
                Account.account_type,
                Account.account_status,
                Account.created_at,
            )
            .filter(
                or_(
                    func.lower(Account.username).like(like_value),
                    func.lower(Account.email).like(like_value),
                    func.lower(Account.account_type).like(like_value),
                    func.lower(Account.account_status).like(like_value),
                )
            )
        )
        audit_query = (
            db.query(AuditLog)
            .filter(
                or_(
                    func.lower(AuditLog.actor_username).like(like_value),
                    func.lower(AuditLog.actor_role).like(like_value),
                    func.lower(AuditLog.action).like(like_value),
                    func.lower(AuditLog.target_type).like(like_value),
                    func.lower(AuditLog.target_label).like(like_value),
                    func.lower(AuditLog.target_id).like(like_value),
                    func.lower(AuditLog.details).like(like_value),
                )
            )
        )

        listings_count = listings_query.count()
        accounts_count = accounts_query.count()
        audit_count = audit_query.count()

        active_count = {
            "listings": listings_count,
            "accounts": accounts_count,
            "audit": audit_count,
        }[selected_tab]
        total_pages = max((active_count + per_page - 1) // per_page, 1)
        current_page = min(current_page, total_pages)
        offset = (current_page - 1) * per_page
        if selected_tab == "listings":
            listings_results = (
                listings_query.order_by(Listing.created_at.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )
        elif selected_tab == "accounts":
            account_results = (
                accounts_query.order_by(Account.created_at.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )
        else:
            audit_results = (
                audit_query.order_by(AuditLog.created_at.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )

    active_count = {
        "listings": listings_count,
        "accounts": accounts_count,
        "audit": audit_count,
    }[selected_tab]
    total_pages = max((active_count + per_page - 1) // per_page, 1)

    context.update(
        {
            "active_page": "search",
            "page_title": "Search",
            "page_description": "Search listings and posts, users and sellers, and audit logs from one dashboard page.",
            "search_query": query_text,
            "search_tab": selected_tab,
            "search_results": {
                "listings": listings_results,
                "accounts": account_results,
                "audit_logs": audit_results,
            },
            "search_counts": {
                "listings": listings_count,
                "accounts": accounts_count,
                "audit_logs": audit_count,
            },
            "search_pagination": {
                "page": current_page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_prev": current_page > 1,
                "has_next": current_page < total_pages,
            },
        }
    )
    return templates.TemplateResponse("dashboard/search.html", context)


@router.get("/dashboard/quality", response_class=HTMLResponse)
def dashboard_quality(request: Request, db: Session = Depends(get_db)):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    context.update(
        {
            "active_page": "quality",
            "page_title": "Seller Quality",
            "page_description": "See the current lowest-rated seller and monitor quality signals.",
        }
    )
    return templates.TemplateResponse("dashboard/quality.html", context)


@router.get("/dashboard/settings", response_class=HTMLResponse)
def dashboard_settings(request: Request, db: Session = Depends(get_db)):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    context.update(
        {
            "active_page": "settings",
            "page_title": "Settings",
            "page_description": "Manage operational settings, including the management session timeout.",
        }
    )
    return templates.TemplateResponse("dashboard/settings.html", context)


@router.get("/dashboard/management-users", response_class=HTMLResponse)
def dashboard_management_users(request: Request, db: Session = Depends(get_db)):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    if not context.get("is_superadmin"):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    context.update(
        {
            "active_page": "management_users",
            "page_title": "Management Users",
            "page_description": "Review, update, suspend, and reactivate management staff accounts.",
        }
    )
    return templates.TemplateResponse("dashboard/management_users.html", context)


@router.get("/dashboard/users", response_class=HTMLResponse)
def dashboard_users(
    request: Request,
    q: str = "",
    status: str = "all",
    role: str = "all",
    page: int = 1,
    db: Session = Depends(get_db),
):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    user_management = _build_user_management_context(
        db,
        query_text=q,
        status_filter=status,
        role_filter=role,
        page=page,
    )
    context.update(
        {
            "active_page": "user_management",
            "page_title": "User Management",
            "page_description": "Search, review, and update buyer and seller accounts from one dashboard page.",
            "user_management_query": user_management["query"],
            "user_management_status": user_management["status_filter"],
            "user_management_role": user_management["role_filter"],
            "managed_users": user_management["results"],
            "managed_users_count": user_management["count"],
            "managed_users_pagination": user_management["pagination"],
            "managed_users_summary": user_management["summary"],
            "managed_users_role_counts": user_management["page_role_counts"],
            "managed_users_status_counts": user_management["page_status_counts"],
        }
    )
    return templates.TemplateResponse("dashboard/users.html", context)


@router.get("/dashboard/account", response_class=HTMLResponse)
def dashboard_account(request: Request, db: Session = Depends(get_db)):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    context.update(
        {
            "active_page": "account",
            "page_title": "Account Snapshot",
            "page_description": "Review the current signed-in web account details.",
        }
    )
    return templates.TemplateResponse("dashboard/account.html", context)


@router.get("/dashboard/audit", response_class=HTMLResponse)
def dashboard_audit(request: Request, db: Session = Depends(get_db)):
    try:
        context = _build_dashboard_context(request, db)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    context.update(
        {
            "active_page": "audit",
            "page_title": "Audit Logs",
            "page_description": "Track important management and superadmin activity across the web console.",
        }
    )
    return templates.TemplateResponse("dashboard/audit.html", context)


@router.post("/dashboard/verifications/{request_id}/approve")
def approve_verification(
    request_id: int,
    request: Request,
    csrf_token: str = Form(...),
    review_note: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    verification_request = (
        db.query(SellerVerificationRequest)
        .filter(SellerVerificationRequest.request_id == request_id)
        .first()
    )
    if verification_request is None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    notification_payload = None
    verification_request.status = "approved"
    verification_request.review_note = review_note.strip() or None
    verification_request.reviewed_by = account["account_id"]
    verification_request.reviewed_at = datetime.now(timezone.utc)

    user_profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == verification_request.user_id)
        .first()
    )
    if user_profile:
        user_profile.is_verified = True
    notification_payload = _create_user_notification_payload(
        db,
        user_id=verification_request.user_id,
        notification_type="seller_verification",
        title="Trusted seller status approved",
        body="Your trusted seller verification request was approved.",
        related_entity_type="seller_verification_request",
        related_entity_id=verification_request.request_id,
    )

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="approve_seller_verification",
        target_type="seller_verification_request",
        target_id=str(verification_request.request_id),
        target_label=str(verification_request.user_id),
        details=review_note.strip() or "Approved trusted seller verification request",
    )
    db.commit()
    if notification_payload is not None:
        _emit_user_notification(verification_request.user_id, notification_payload)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/verifications/{request_id}/reject")
def reject_verification(
    request_id: int,
    request: Request,
    csrf_token: str = Form(...),
    review_note: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    verification_request = (
        db.query(SellerVerificationRequest)
        .filter(SellerVerificationRequest.request_id == request_id)
        .first()
    )
    if verification_request is None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    notification_payload = None
    verification_request.status = "rejected"
    verification_request.review_note = review_note.strip() or None
    verification_request.reviewed_by = account["account_id"]
    verification_request.reviewed_at = datetime.now(timezone.utc)
    user_profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == verification_request.user_id)
        .first()
    )
    if user_profile:
        user_profile.is_verified = False
    notification_payload = _create_user_notification_payload(
        db,
        user_id=verification_request.user_id,
        notification_type="seller_verification",
        title="Trusted seller status removed",
        body="Your trusted seller verification status was rejected or removed.",
        related_entity_type="seller_verification_request",
        related_entity_id=verification_request.request_id,
    )
    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="reject_seller_verification",
        target_type="seller_verification_request",
        target_id=str(verification_request.request_id),
        target_label=str(verification_request.user_id),
        details=review_note.strip() or "Rejected or removed trusted seller verification",
    )
    db.commit()
    if notification_payload is not None:
        _emit_user_notification(verification_request.user_id, notification_payload)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/listings/{listing_id}/delete")
def delete_listing(
    listing_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    listing = db.query(Listing).filter(Listing.listing_id == listing_id).first()
    if listing is not None:
        session_account = _get_session_account(request)
        notification_payload = None
        seller_account_id = listing.seller_id
        if seller_account_id is not None:
            notification_payload = _create_user_notification_payload(
                db,
                user_id=seller_account_id,
                notification_type="listing_removed",
                title="Listing removed",
                body=f"Your listing \"{listing.title}\" was removed by management.",
                related_entity_type="listing",
                related_entity_id=listing.listing_id,
            )
        if session_account:
            create_audit_log(
                db,
                actor_account_id=session_account["account_id"],
                actor_username=session_account["username"],
                actor_role=session_account["account_type"],
                action="delete_listing",
                target_type="listing",
                target_id=str(listing.listing_id),
                target_label=listing.title,
                details=f"Deleted {listing.listing_type or 'listing'} with status {listing.status or 'unknown'}",
            )
        db.delete(listing)
        db.commit()
        if seller_account_id is not None and notification_payload is not None:
            _emit_user_notification(seller_account_id, notification_payload)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/sellers/{seller_id}/warn")
def warn_seller(
    seller_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    seller = (
        db.query(Account)
        .filter(Account.account_id == seller_id, Account.account_type == "user")
        .first()
    )
    if seller is None:
        return RedirectResponse(url="/dashboard/moderation", status_code=status.HTTP_303_SEE_OTHER)

    notification_payload = None
    seller.warning_count = (seller.warning_count or 0) + 1
    seller.last_warned_at = datetime.now(timezone.utc)
    if seller.account_status != "banned":
        seller.account_status = "warned"
    notification_payload = _create_user_notification_payload(
        db,
        user_id=seller.account_id,
        notification_type="account_warning",
        title="Account warning issued",
        body=f"Management issued warning #{seller.warning_count} on your account.",
        related_entity_type="account",
        related_entity_id=seller.account_id,
    )

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="warn_seller",
        target_type="account",
        target_id=str(seller.account_id),
        target_label=seller.username,
        details=f"Issued warning #{seller.warning_count}",
    )
    db.commit()
    if notification_payload is not None:
        _emit_user_notification(seller.account_id, notification_payload)
    return RedirectResponse(url="/dashboard/moderation", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/sellers/{seller_id}/ban")
def ban_seller(
    seller_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    seller = (
        db.query(Account)
        .filter(Account.account_id == seller_id, Account.account_type == "user")
        .first()
    )
    if seller is None:
        return RedirectResponse(url="/dashboard/moderation", status_code=status.HTTP_303_SEE_OTHER)

    seller.account_status = "banned"
    notification_payload = _create_user_notification_payload(
        db,
        user_id=seller.account_id,
        notification_type="account_status",
        title="Account banned",
        body="Your account was banned by management.",
        related_entity_type="account",
        related_entity_id=seller.account_id,
    )

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="ban_seller",
        target_type="account",
        target_id=str(seller.account_id),
        target_label=seller.username,
        details="Banned seller from dashboard moderation",
    )
    db.commit()
    _emit_user_notification(seller.account_id, notification_payload)
    return RedirectResponse(url="/dashboard/moderation", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/messages/{conversation_id}/send")
def send_dashboard_message(
    conversation_id: int,
    request: Request,
    csrf_token: str = Form(...),
    message_text: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    try:
        message, conversation, sender, recipient = create_message_record(
            db,
            conversation_id=conversation_id,
            sender_id=account["account_id"],
            message_text=message_text,
        )
    except ValueError:
        return RedirectResponse(url="/dashboard/messages", status_code=status.HTTP_303_SEE_OTHER)
    notification_payload = None
    if recipient is not None and recipient.account_type == "user":
        notification_payload = _create_user_notification_payload(
            db,
            user_id=recipient.account_id,
            notification_type="chat_message",
            title=f"New message from {sender.username}",
            body=message.message_text,
            related_entity_type="conversation",
            related_entity_id=conversation_id,
        )
    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="send_dashboard_message",
        target_type="conversation",
        target_id=str(conversation_id),
        target_label=str(conversation_id),
        details="Sent message from dashboard chat UI",
    )
    db.commit()
    message_payload = serialize_message(message, sender_username=sender.username)
    chat_event = {
        "type": "chat.message",
        "conversation_id": conversation_id,
        "message": message_payload,
    }
    _dispatch_conversation_event(conversation_id, chat_event)
    _dispatch_account_event(account["account_id"], chat_event)
    if recipient is not None:
        _dispatch_account_event(recipient.account_id, chat_event)
        if notification_payload is not None:
            _emit_user_notification(recipient.account_id, notification_payload)
    return RedirectResponse(
        url=f"/dashboard/messages?conversation_id={conversation_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/dashboard/messages/start")
def start_dashboard_message(
    request: Request,
    csrf_token: str = Form(...),
    target_user_id: int = Form(...),
    initial_message: str = Form(""),
    conversation_type: str = Form("staff_support"),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    target_account = (
        db.query(Account)
        .filter(Account.account_id == target_user_id, Account.account_type == "user")
        .first()
    )
    if target_account is None:
        return RedirectResponse(url="/dashboard/messages", status_code=status.HTTP_303_SEE_OTHER)

    conversation = _get_or_create_staff_user_conversation(
        db,
        staff_account_id=account["account_id"],
        user_account_id=target_user_id,
        conversation_type=conversation_type.strip() or "staff_support",
    )

    message_payload = None
    notification_payload = None
    trimmed_message = initial_message.strip()
    if trimmed_message:
        message, _, sender, recipient = create_message_record(
            db,
            conversation_id=conversation.conversation_id,
            sender_id=account["account_id"],
            message_text=trimmed_message,
        )
        message_payload = serialize_message(message, sender_username=sender.username)
        if recipient is not None and recipient.account_type == "user":
            notification_payload = _create_user_notification_payload(
                db,
                user_id=recipient.account_id,
                notification_type="chat_message",
                title=f"New message from {sender.username}",
                body=message.message_text,
                related_entity_type="conversation",
                related_entity_id=conversation.conversation_id,
            )

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="start_dashboard_message_thread",
        target_type="conversation",
        target_id=str(conversation.conversation_id),
        target_label=str(conversation.conversation_id),
        details=f"Opened staff conversation with user {target_user_id}",
    )
    db.commit()
    if message_payload is not None:
        chat_event = {
            "type": "chat.message",
            "conversation_id": conversation.conversation_id,
            "message": message_payload,
        }
        _dispatch_conversation_event(conversation.conversation_id, chat_event)
        _dispatch_account_event(account["account_id"], chat_event)
        _dispatch_account_event(target_user_id, chat_event)
    if notification_payload is not None:
        _emit_user_notification(target_user_id, notification_payload)
    return RedirectResponse(
        url=f"/dashboard/messages?conversation_id={conversation.conversation_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/dashboard/sellers/{seller_id}/unban")
def unban_seller(
    seller_id: int,
    request: Request,
    csrf_token: str = Form(...),
    banned_q: str = Form(""),
    banned_page: int = Form(1),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    seller = (
        db.query(Account)
        .filter(Account.account_id == seller_id, Account.account_type == "user")
        .first()
    )
    if seller is None:
        return RedirectResponse(
            url=_build_moderation_redirect_url(
                banned_query=banned_q,
                banned_page=max(banned_page, 1),
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    seller.account_status = "active"
    notification_payload = _create_user_notification_payload(
        db,
        user_id=seller.account_id,
        notification_type="account_status",
        title="Account restored",
        body="Your account was restored by management.",
        related_entity_type="account",
        related_entity_id=seller.account_id,
    )

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="unban_seller",
        target_type="account",
        target_id=str(seller.account_id),
        target_label=seller.username,
        details="Restored seller access from dashboard moderation",
    )
    db.commit()
    _emit_user_notification(seller.account_id, notification_payload)
    return RedirectResponse(
        url=_build_moderation_redirect_url(
            banned_query=banned_q,
            banned_page=max(banned_page, 1),
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/dashboard/settings/session-timeout")
def update_session_timeout(
    request: Request,
    csrf_token: str = Form(...),
    minutes: int = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    if account.get("account_type") != "superadmin":
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    updated_minutes = set_management_session_timeout_minutes(db, minutes)
    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="update_management_session_timeout",
        target_type="app_setting",
        target_id="management_session_timeout_minutes",
        target_label="management_session_timeout_minutes",
        details=f"Updated management session timeout to {updated_minutes} minutes",
    )
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/management-users/{manager_id}/update")
def update_management_user(
    manager_id: int,
    request: Request,
    csrf_token: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    role_name: str = Form(""),
    account_status: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    if account.get("account_type") != "superadmin":
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    management_row = (
        db.query(Account, ManagementAccount)
        .join(ManagementAccount, ManagementAccount.manager_id == Account.account_id)
        .filter(Account.account_id == manager_id, Account.account_type == "management")
        .first()
    )
    if management_row is None:
        return RedirectResponse(url="/dashboard/management-users", status_code=status.HTTP_303_SEE_OTHER)

    management_account, management_profile = management_row
    normalized_status = account_status.strip().lower()
    if normalized_status != "active":
        normalized_status = management_account.account_status or "active"

    management_account.account_status = normalized_status
    management_profile.first_name = first_name.strip() or None
    management_profile.last_name = last_name.strip() or None
    management_profile.role_name = role_name.strip() or "manager"

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="update_management_user",
        target_type="management_account",
        target_id=str(management_account.account_id),
        target_label=management_account.username,
        details=f"Updated management user with status {management_account.account_status} and role {management_profile.role_name}",
    )
    db.commit()
    return RedirectResponse(url="/dashboard/management-users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/users/{user_id}/update")
def update_user_management_account(
    user_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account_status: str = Form(...),
    q: str = Form(""),
    status: str = Form("all"),
    role: str = Form("all"),
    page: int = Form(1),
    db: Session = Depends(get_db),
):
    try:
        account = _require_web_session(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    managed_account = (
        db.query(Account)
        .filter(Account.account_id == user_id, Account.account_type == "user")
        .first()
    )
    if managed_account is None:
        return RedirectResponse(
            url=_build_user_management_redirect_url(
                query=q,
                status_filter=status,
                role_filter=role,
                page=max(page, 1),
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    normalized_status = account_status.strip().lower()
    if normalized_status not in {"active", "warned", "banned"}:
        normalized_status = managed_account.account_status or "active"

    previous_status = managed_account.account_status or "active"
    managed_account.account_status = normalized_status
    notification_payload = None
    if previous_status != managed_account.account_status:
        notification_payload = _create_user_notification_payload(
            db,
            user_id=managed_account.account_id,
            notification_type="account_status",
            title="Account status updated",
            body=f"Your account status is now {managed_account.account_status}.",
            related_entity_type="account",
            related_entity_id=managed_account.account_id,
        )

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="update_user_account_status",
        target_type="account",
        target_id=str(managed_account.account_id),
        target_label=managed_account.username,
        details=f"Updated user account status to {managed_account.account_status}",
    )
    db.commit()
    if notification_payload is not None:
        _emit_user_notification(managed_account.account_id, notification_payload)
    return RedirectResponse(
        url=_build_user_management_redirect_url(
            query=q,
            status_filter=status,
            role_filter=role,
            page=max(page, 1),
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    try:
        session_account = _get_session_account(request)
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    if session_account:
        create_audit_log(
            db,
            actor_account_id=session_account["account_id"],
            actor_username=session_account["username"],
            actor_role=session_account["account_type"],
            action="logout",
            target_type="account",
            target_id=str(session_account["account_id"]),
            target_label=session_account["username"],
            details="Web logout",
        )
        db.commit()
    _clear_account_session(request)
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
