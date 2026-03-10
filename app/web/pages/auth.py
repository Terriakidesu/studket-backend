from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import SUPERADMIN_INVITE_CODE, generate_csrf_token
from app.db.models import AuditLog, Account, Listing, Review, SellerVerificationRequest, Transaction, UserProfile
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


@router.get("/auth", response_class=HTMLResponse)
def auth_portal(request: Request):
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _render_auth_page(request, template_name="login.html")


@router.get("/auth/register", response_class=HTMLResponse)
def register_page(request: Request):
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
        account = _require_web_session(request)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

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

    verification_requests = (
        db.query(
            SellerVerificationRequest.request_id,
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

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "Dashboard",
            "account": account,
            "csrf_token": csrf_token,
            "metrics": {
                "total_users": total_users,
                "buyers_count": buyers_count,
                "sellers_count": sellers_count,
                "listings_count": listings_count,
            },
            "verification_requests": verification_requests,
            "listings": listings,
            "lowest_rated_seller": lowest_rated_seller,
            "management_timeout_minutes": timeout_minutes,
            "is_superadmin": account.get("account_type") == "superadmin",
            "recent_audit_logs": recent_audit_logs,
        },
    )


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

    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="approve_seller_verification",
        target_type="seller_verification_request",
        target_id=str(verification_request.request_id),
        target_label=str(verification_request.user_id),
        details=review_note.strip() or "Approved seller verification request",
    )
    db.commit()
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
    create_audit_log(
        db,
        actor_account_id=account["account_id"],
        actor_username=account["username"],
        actor_role=account["account_type"],
        action="reject_seller_verification",
        target_type="seller_verification_request",
        target_id=str(verification_request.request_id),
        target_label=str(verification_request.user_id),
        details=review_note.strip() or "Rejected seller verification request",
    )
    db.commit()
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
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


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
