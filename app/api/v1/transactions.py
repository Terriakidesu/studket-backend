from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.common import create_crud_router, serialize_model
from app.db.models import Account, Notification, Transaction, TransactionQR, UserProfile
from app.db.session import get_db

router = APIRouter(prefix="/transactions", tags=["transactions"])

crud_router = create_crud_router(
    model=Transaction,
    prefix="",
    tags=["transactions"],
    pk_field="transaction_id",
)


class CancelTransactionPayload(BaseModel):
    account_id: int
    reason: str | None = None


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


router.include_router(crud_router)
