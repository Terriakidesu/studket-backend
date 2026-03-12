from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

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


crud_router = create_crud_router(
    model=Transaction,
    prefix="",
    tags=["transactions"],
    pk_field="transaction_id",
    enable_create=False,
    enable_update=False,
)

router.include_router(crud_router)
