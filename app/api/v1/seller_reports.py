from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.common import create_crud_router, serialize_model
from app.db.models import Account, SellerReport, UserProfile
from app.db.session import get_db

user_router = APIRouter(prefix="/seller-reports", tags=["seller-reports"])


class CreateSellerReportPayload(BaseModel):
    seller_id: int
    reporter_id: int
    reason: str
    details: str | None = None


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


def _get_seller_or_404(seller_id: int, db: Session) -> UserProfile:
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == seller_id)
        .first()
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Seller profile not found"},
        )
    if not profile.is_seller:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Reported user is not a seller"},
        )
    return profile


@user_router.post("/", status_code=status.HTTP_201_CREATED)
def create_seller_report(
    payload: CreateSellerReportPayload = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _get_user_account_or_404(payload.reporter_id, db)
    _get_seller_or_404(payload.seller_id, db)

    report = SellerReport(
        seller_id=payload.seller_id,
        reporter_id=payload.reporter_id,
        reason=payload.reason,
        details=(payload.details or "").strip() or None,
        status="open",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return jsonable_encoder(serialize_model(report))


admin_router = create_crud_router(
    model=SellerReport,
    prefix="/seller-reports",
    tags=["seller-reports"],
    pk_field="report_id",
    enable_create=False,
)
