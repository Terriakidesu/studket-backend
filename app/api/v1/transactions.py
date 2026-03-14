from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import or_, func
from sqlalchemy.orm import Session, aliased

from app.api.v1.common import create_crud_router, serialize_model
from app.db.models import Account, Listing, ListingInquiry, Notification, Transaction, TransactionQR, UserProfile
from app.db.session import get_db

router = APIRouter(prefix="/transactions", tags=["transactions"])


class CancelTransactionPayload(BaseModel):
    account_id: int
    reason: str | None = None


class CreateTransactionPayload(BaseModel):
    listing_id: int
    buyer_id: int
    seller_id: int
    quantity: int = 1
    agreed_price: Decimal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_transaction_or_404(transaction_id: int, db: Session) -> Transaction:
    transaction = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transaction_id)
        .first()
    )
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Transaction not found"},
        )
    return transaction


def _get_user_account_or_404(account_id: int, db: Session) -> Account:
    account = (
        db.query(Account)
        .filter(Account.account_id == account_id, Account.account_type == "user")
        .first()
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "User account not found"},
        )
    profile = db.query(UserProfile).filter(UserProfile.user_id == account_id).first()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "User profile not found"},
        )
    return account


def _serialize_transaction(transaction: Transaction) -> dict[str, Any]:
    payload = serialize_model(transaction)
    if transaction.agreed_price is not None:
        payload["agreed_price"] = float(transaction.agreed_price)
    return payload


def _get_listing_or_404(listing_id: int, db: Session) -> Listing:
    listing = (
        db.query(Listing)
        .filter(Listing.listing_id == listing_id)
        .first()
    )
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Listing not found"},
        )
    return listing


def _ensure_transaction_price_is_valid(
    *,
    listing: Listing,
    agreed_price: Decimal,
    buyer_id: int,
    seller_id: int,
    db: Session,
) -> None:
    if agreed_price <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "agreed_price must be greater than 0"},
        )

    if listing.listing_type == "looking_for":
        if listing.seller_id != buyer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "For looking-for listings, buyer_id must be the listing owner"},
            )
        if seller_id == listing.seller_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "For looking-for listings, seller_id must be the user fulfilling the request"},
            )

        if listing.budget_min is not None and listing.budget_max is not None:
            concat_candidate = f"{int(listing.budget_min)}{int(listing.budget_max)}"
            try:
                if agreed_price == Decimal(concat_candidate):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"error": "agreed_price looks like a concatenated budget range, not a real price"},
                    )
            except ArithmeticError:
                pass

        return

    if listing.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "seller_id must match the listing owner"},
        )
    if buyer_id == seller_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "buyer_id and seller_id must be different"},
        )


def _get_related_inquiry(
    *,
    listing: Listing,
    buyer_id: int,
    seller_id: int,
    db: Session,
) -> ListingInquiry | None:
    if listing.listing_type == "looking_for":
        owner_id = buyer_id
        inquirer_id = seller_id
    else:
        owner_id = seller_id
        inquirer_id = buyer_id

    return (
        db.query(ListingInquiry)
        .filter(
            ListingInquiry.listing_id == listing.listing_id,
            ListingInquiry.owner_id == owner_id,
            ListingInquiry.inquirer_id == inquirer_id,
        )
        .order_by(ListingInquiry.responded_at.desc(), ListingInquiry.inquiry_id.desc())
        .first()
    )


def _ensure_inquiry_ready_for_transaction(
    *,
    listing: Listing,
    inquiry: ListingInquiry | None,
) -> None:
    if inquiry is None:
        if listing.listing_type == "looking_for":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "A matching inquiry is required before creating a transaction for this looking-for listing"},
            )
        return

    if inquiry.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Rejected inquiries cannot create transactions"},
        )

    if inquiry.status == "accepted":
        return

    if inquiry.responded_by is None or inquiry.responded_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Inquiry must be accepted before creating a transaction"},
        )


def _finalize_inquiry_acceptance(
    *,
    inquiry: ListingInquiry | None,
    listing: Listing,
    db: Session,
) -> None:
    if inquiry is None or inquiry.status == "accepted":
        return

    inquiry.status = "accepted"
    db.add(
        Notification(
            user_id=inquiry.inquirer_id,
            notification_type="listing_inquiry_accepted",
            title="Inquiry accepted",
            body=f"Your inquiry for {listing.title} was accepted.",
            related_entity_type="conversation",
            related_entity_id=inquiry.conversation_id,
            is_read=False,
        )
    )


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: CreateTransactionPayload = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _get_user_account_or_404(payload.buyer_id, db)
    _get_user_account_or_404(payload.seller_id, db)
    listing = _get_listing_or_404(payload.listing_id, db)
    inquiry = _get_related_inquiry(
        listing=listing,
        buyer_id=payload.buyer_id,
        seller_id=payload.seller_id,
        db=db,
    )

    _ensure_transaction_price_is_valid(
        listing=listing,
        agreed_price=payload.agreed_price,
        buyer_id=payload.buyer_id,
        seller_id=payload.seller_id,
        db=db,
    )
    _ensure_inquiry_ready_for_transaction(listing=listing, inquiry=inquiry)

    transaction = Transaction(
        listing_id=payload.listing_id,
        buyer_id=payload.buyer_id,
        seller_id=payload.seller_id,
        quantity=payload.quantity,
        agreed_price=payload.agreed_price,
        transaction_status="pending",
        completed_at=None,
    )
    db.add(transaction)
    _finalize_inquiry_acceptance(inquiry=inquiry, listing=listing, db=db)
    db.commit()
    db.refresh(transaction)
    return jsonable_encoder(_serialize_transaction(transaction))


@router.post("/{item_id}/cancel")
def cancel_transaction(
    item_id: int,
    payload: CancelTransactionPayload = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    seller = _get_user_account_or_404(payload.account_id, db)
    transaction = _get_transaction_or_404(item_id, db)

    if transaction.seller_id != payload.account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Only the seller can cancel this transaction"},
        )
    if transaction.transaction_status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Completed transactions cannot be cancelled"},
        )
    if transaction.transaction_status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Transaction is already cancelled"},
        )

    transaction.transaction_status = "cancelled"
    transaction.completed_at = None

    (
        db.query(TransactionQR)
        .filter(
            TransactionQR.transaction_id == transaction.transaction_id,
            TransactionQR.is_used.is_(False),
        )
        .update(
            {
                TransactionQR.is_used: True,
                TransactionQR.scanned_by: payload.account_id,
                TransactionQR.scanned_at: _utcnow(),
            },
            synchronize_session=False,
        )
    )

    recipient_ids = {
        participant_id
        for participant_id in (transaction.buyer_id, transaction.seller_id)
        if participant_id is not None and participant_id != payload.account_id
    }
    reason_text = (payload.reason or "").strip()
    body = (
        f"{seller.username} cancelled transaction #{transaction.transaction_id}."
        if not reason_text
        else f"{seller.username} cancelled transaction #{transaction.transaction_id}: {reason_text}"
    )
    for user_id in recipient_ids:
        db.add(
            Notification(
                user_id=user_id,
                notification_type="transaction_cancelled",
                title="Transaction cancelled",
                body=body,
                related_entity_type="transaction",
                related_entity_id=transaction.transaction_id,
                is_read=False,
            )
        )

    db.commit()
    db.refresh(transaction)
    return jsonable_encoder(
        {
            "message": "Transaction cancelled",
            "transaction": _serialize_transaction(transaction),
        }
    )


@router.get("/users/{account_id}")
def get_user_transactions(
    account_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _get_user_account_or_404(account_id, db)
    rows = (
        db.query(Transaction, Listing.listing_type, Listing.title)
        .outerjoin(Listing, Listing.listing_id == Transaction.listing_id)
        .filter(
            or_(
                Transaction.buyer_id == account_id,
                Transaction.seller_id == account_id,
            )
        )
        .order_by(Transaction.transaction_id.desc())
        .all()
    )
    items = [
        {
            "role": "buyer" if transaction.buyer_id == account_id else "seller",
            "listing_type": listing_type,
            "listing_title": listing_title,
            "is_looking_for": listing_type == "looking_for",
            **_serialize_transaction(transaction),
        }
        for transaction, listing_type, listing_title in rows
    ]
    return jsonable_encoder(
        {
            "account_id": account_id,
            "count": len(items),
            "items": items,
        }
    )


@router.get("/users/{account_id}/{transaction_id}")
def get_user_transaction_detail(
    account_id: int,
    transaction_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _get_user_account_or_404(account_id, db)

    buyer_account = aliased(Account)
    seller_account = aliased(Account)
    buyer_profile = aliased(UserProfile)
    seller_profile = aliased(UserProfile)
    listing = aliased(Listing)
    transaction_qr = aliased(TransactionQR)

    latest_qr_subq = (
        db.query(
            TransactionQR.transaction_id.label("tq_transaction_id"),
            func.max(TransactionQR.transaction_qr_id).label("latest_qr_id"),
        )
        .group_by(TransactionQR.transaction_id)
        .subquery()
    )

    row = (
        db.query(
            Transaction.transaction_id.label("transaction_id"),
            Transaction.listing_id.label("transaction_listing_id"),
            Transaction.buyer_id.label("transaction_buyer_id"),
            Transaction.seller_id.label("transaction_seller_id"),
            Transaction.quantity.label("transaction_quantity"),
            Transaction.agreed_price.label("transaction_agreed_price"),
            Transaction.transaction_status.label("transaction_status"),
            Transaction.completed_at.label("transaction_completed_at"),
            listing.listing_id.label("listing_id"),
            listing.seller_id.label("listing_seller_id"),
            listing.share_token.label("listing_share_token"),
            listing.title.label("listing_title"),
            listing.description.label("listing_description"),
            listing.price.label("listing_price"),
            listing.budget_min.label("listing_budget_min"),
            listing.budget_max.label("listing_budget_max"),
            listing.listing_type.label("listing_type"),
            listing.condition.label("listing_condition"),
            listing.status.label("listing_status"),
            listing.created_at.label("listing_created_at"),
            buyer_account.account_id.label("buyer_account_id"),
            buyer_account.email.label("buyer_email"),
            buyer_account.username.label("buyer_username"),
            buyer_account.account_type.label("buyer_account_type"),
            buyer_account.account_status.label("buyer_account_status"),
            buyer_account.warning_count.label("buyer_warning_count"),
            buyer_account.last_warned_at.label("buyer_last_warned_at"),
            buyer_account.created_at.label("buyer_account_created_at"),
            buyer_profile.user_id.label("buyer_profile_user_id"),
            buyer_profile.first_name.label("buyer_first_name"),
            buyer_profile.last_name.label("buyer_last_name"),
            buyer_profile.campus.label("buyer_campus"),
            buyer_profile.profile_photo.label("buyer_profile_photo"),
            buyer_profile.is_seller.label("buyer_is_seller"),
            buyer_profile.is_verified.label("buyer_is_verified"),
            buyer_profile.created_at.label("buyer_profile_created_at"),
            seller_account.account_id.label("seller_account_id"),
            seller_account.email.label("seller_email"),
            seller_account.username.label("seller_username"),
            seller_account.account_type.label("seller_account_type"),
            seller_account.account_status.label("seller_account_status"),
            seller_account.warning_count.label("seller_warning_count"),
            seller_account.last_warned_at.label("seller_last_warned_at"),
            seller_account.created_at.label("seller_account_created_at"),
            seller_profile.user_id.label("seller_profile_user_id"),
            seller_profile.first_name.label("seller_first_name"),
            seller_profile.last_name.label("seller_last_name"),
            seller_profile.campus.label("seller_campus"),
            seller_profile.profile_photo.label("seller_profile_photo"),
            seller_profile.is_seller.label("seller_is_seller"),
            seller_profile.is_verified.label("seller_is_verified"),
            seller_profile.created_at.label("seller_profile_created_at"),
            transaction_qr.transaction_qr_id.label("transaction_qr_id"),
            transaction_qr.qr_token.label("transaction_qr_token"),
            transaction_qr.expires_at.label("transaction_qr_expires_at"),
            transaction_qr.is_used.label("transaction_qr_is_used"),
            transaction_qr.generated_by.label("transaction_qr_generated_by"),
            transaction_qr.scanned_by.label("transaction_qr_scanned_by"),
            transaction_qr.scanned_at.label("transaction_qr_scanned_at"),
            transaction_qr.created_at.label("transaction_qr_created_at"),
        )
        .select_from(Transaction)
        .outerjoin(listing, listing.listing_id == Transaction.listing_id)
        .outerjoin(buyer_account, buyer_account.account_id == Transaction.buyer_id)
        .outerjoin(buyer_profile, buyer_profile.user_id == Transaction.buyer_id)
        .outerjoin(seller_account, seller_account.account_id == Transaction.seller_id)
        .outerjoin(seller_profile, seller_profile.user_id == Transaction.seller_id)
        .outerjoin(latest_qr_subq, latest_qr_subq.c.tq_transaction_id == Transaction.transaction_id)
        .outerjoin(transaction_qr, transaction_qr.transaction_qr_id == latest_qr_subq.c.latest_qr_id)
        .filter(Transaction.transaction_id == transaction_id)
        .first()
    )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Transaction not found"},
        )

    buyer_id = row.transaction_buyer_id
    seller_id = row.transaction_seller_id
    if buyer_id != account_id and seller_id != account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "You do not have access to this transaction"},
        )

    payload = dict(row._mapping)
    agreed_price = payload.get("transaction_agreed_price")
    if agreed_price is not None:
        payload["transaction_agreed_price"] = float(agreed_price)
    listing_price = payload.get("listing_price")
    if listing_price is not None:
        payload["listing_price"] = float(listing_price)
    budget_min = payload.get("listing_budget_min")
    if budget_min is not None:
        payload["listing_budget_min"] = float(budget_min)
    budget_max = payload.get("listing_budget_max")
    if budget_max is not None:
        payload["listing_budget_max"] = float(budget_max)

    payload["role"] = "buyer" if buyer_id == account_id else "seller"
    return jsonable_encoder(payload)


crud_router = create_crud_router(
    model=Transaction,
    prefix="",
    tags=["transactions"],
    pk_field="transaction_id",
    enable_create=False,
    enable_update=False,
)

router.include_router(crud_router)
