from datetime import datetime, timezone
from secrets import token_urlsafe

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.common import create_crud_router
from app.db.models import Account, Notification, Transaction, TransactionQR, UserProfile
from app.db.session import get_db

router = APIRouter(prefix="/transaction-qr", tags=["transaction-qr"])

crud_router = create_crud_router(
    model=TransactionQR,
    prefix="/transaction-qr",
    tags=["transaction-qr"],
    pk_field="transaction_qr_id",
)


class GenerateTransactionQrPayload(BaseModel):
    transaction_id: int
    account_id: int


class ConfirmTransactionQrPayload(BaseModel):
    qr_token: str
    account_id: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_transaction_or_404(transaction_id: int, db: Session) -> Transaction:
    transaction = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transaction_id)
        .first()
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail={"error": "Transaction not found"})
    return transaction


def _get_user_account_or_404(account_id: int, db: Session) -> Account:
    account = (
        db.query(Account)
        .filter(Account.account_id == account_id, Account.account_type == "user")
        .first()
    )
    if account is None:
        raise HTTPException(status_code=404, detail={"error": "User account not found"})

    profile = db.query(UserProfile).filter(UserProfile.user_id == account_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail={"error": "User profile not found"})
    return account


def _participant_ids(transaction: Transaction) -> set[int]:
    return {participant_id for participant_id in (transaction.buyer_id, transaction.seller_id) if participant_id is not None}


def _serialize_qr(transaction_qr: TransactionQR) -> dict:
    return {
        "transaction_qr_id": transaction_qr.transaction_qr_id,
        "transaction_id": transaction_qr.transaction_id,
        "qr_token": transaction_qr.qr_token,
        "expires_at": transaction_qr.expires_at.isoformat() + "Z" if transaction_qr.expires_at else None,
        "is_used": bool(transaction_qr.is_used),
        "generated_by": transaction_qr.generated_by,
        "scanned_by": transaction_qr.scanned_by,
        "scanned_at": transaction_qr.scanned_at.isoformat() + "Z" if transaction_qr.scanned_at else None,
        "created_at": transaction_qr.created_at.isoformat() + "Z" if transaction_qr.created_at else None,
    }


def _serialize_transaction(transaction: Transaction) -> dict:
    return {
        "transaction_id": transaction.transaction_id,
        "listing_id": transaction.listing_id,
        "buyer_id": transaction.buyer_id,
        "seller_id": transaction.seller_id,
        "quantity": transaction.quantity,
        "agreed_price": float(transaction.agreed_price) if transaction.agreed_price is not None else None,
        "transaction_status": transaction.transaction_status,
        "completed_at": transaction.completed_at.isoformat() + "Z" if transaction.completed_at else None,
    }


def _ensure_transaction_confirmable(transaction: Transaction) -> None:
    if transaction.transaction_status == "completed":
        raise HTTPException(status_code=400, detail={"error": "Transaction is already completed"})


@router.post("/generate")
def generate_transaction_qr(
    payload: GenerateTransactionQrPayload,
    db: Session = Depends(get_db),
):
    _get_user_account_or_404(payload.account_id, db)
    transaction = _get_transaction_or_404(payload.transaction_id, db)
    _ensure_transaction_confirmable(transaction)

    participant_ids = _participant_ids(transaction)
    if payload.account_id not in participant_ids:
        raise HTTPException(status_code=403, detail={"error": "Only transaction participants can generate a QR code"})

    active_qr = (
        db.query(TransactionQR)
        .filter(
            TransactionQR.transaction_id == transaction.transaction_id,
            TransactionQR.is_used.is_(False),
        )
        .order_by(TransactionQR.created_at.desc(), TransactionQR.transaction_qr_id.desc())
        .first()
    )
    if active_qr is not None:
        return {
            "message": "Active transaction QR already exists",
            "transaction": _serialize_transaction(transaction),
            "transaction_qr": _serialize_qr(active_qr),
        }

    transaction_qr = TransactionQR(
        transaction_id=transaction.transaction_id,
        qr_token=token_urlsafe(24),
        expires_at=None,
        is_used=False,
        generated_by=payload.account_id,
    )
    db.add(transaction_qr)
    db.commit()
    db.refresh(transaction_qr)

    return {
        "message": "Transaction QR generated",
        "transaction": _serialize_transaction(transaction),
        "transaction_qr": _serialize_qr(transaction_qr),
    }


@router.get("/token/{qr_token}")
def get_transaction_qr_by_token(
    qr_token: str,
    db: Session = Depends(get_db),
):
    transaction_qr = (
        db.query(TransactionQR)
        .filter(TransactionQR.qr_token == qr_token)
        .first()
    )
    if transaction_qr is None:
        raise HTTPException(status_code=404, detail={"error": "Transaction QR not found"})

    transaction = _get_transaction_or_404(transaction_qr.transaction_id, db)
    return {
        "transaction": _serialize_transaction(transaction),
        "transaction_qr": _serialize_qr(transaction_qr),
        "is_expired": False,
    }


@router.post("/confirm")
def confirm_transaction_qr(
    payload: ConfirmTransactionQrPayload,
    db: Session = Depends(get_db),
):
    _get_user_account_or_404(payload.account_id, db)
    transaction_qr = (
        db.query(TransactionQR)
        .filter(TransactionQR.qr_token == payload.qr_token)
        .first()
    )
    if transaction_qr is None:
        raise HTTPException(status_code=404, detail={"error": "Transaction QR not found"})

    transaction = _get_transaction_or_404(transaction_qr.transaction_id, db)
    _ensure_transaction_confirmable(transaction)

    if transaction_qr.is_used:
        raise HTTPException(status_code=400, detail={"error": "Transaction QR has already been used"})

    participant_ids = _participant_ids(transaction)
    if payload.account_id not in participant_ids:
        raise HTTPException(status_code=403, detail={"error": "Only transaction participants can confirm this QR"})
    if transaction_qr.generated_by == payload.account_id:
        raise HTTPException(status_code=400, detail={"error": "The QR generator cannot confirm their own QR"})

    scanned_at = _utcnow()
    transaction_qr.is_used = True
    transaction_qr.scanned_by = payload.account_id
    transaction_qr.scanned_at = scanned_at

    transaction.transaction_status = "completed"
    transaction.completed_at = scanned_at

    for user_id in participant_ids:
        db.add(
            Notification(
                user_id=user_id,
                notification_type="transaction_completed",
                title="Transaction confirmed",
                body=f"Transaction #{transaction.transaction_id} was confirmed by QR scan.",
                related_entity_type="transaction",
                related_entity_id=transaction.transaction_id,
                is_read=False,
            )
        )

    db.commit()
    db.refresh(transaction_qr)
    db.refresh(transaction)

    return {
        "message": "Transaction confirmed by QR",
        "transaction": _serialize_transaction(transaction),
        "transaction_qr": _serialize_qr(transaction_qr),
    }


router.include_router(crud_router)
