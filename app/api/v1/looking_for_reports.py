from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.common import create_crud_router, serialize_model
from app.db.models import Account, Listing, LookingForReport, UserProfile
from app.db.session import get_db

user_router = APIRouter(prefix="/looking-for-reports", tags=["looking-for-reports"])


class CreateLookingForReportPayload(BaseModel):
    listing_id: int
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


@user_router.post("/", status_code=status.HTTP_201_CREATED)
def create_looking_for_report(
    payload: CreateLookingForReportPayload = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _get_user_account_or_404(payload.reporter_id, db)
    listing = _get_listing_or_404(payload.listing_id, db)
    if listing.listing_type != "looking_for":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Only looking-for listings can be reported here"},
        )

    report = LookingForReport(
        listing_id=payload.listing_id,
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
    model=LookingForReport,
    prefix="/looking-for-reports",
    tags=["looking-for-reports"],
    pk_field="report_id",
    enable_create=False,
)
