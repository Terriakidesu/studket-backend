from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import SellerVerificationRequest, UserProfile
from app.db.session import get_db
from app.services.auth import (
    AuthServiceError,
    RegistrationData,
    authenticate_account,
    elevate_buyer_to_seller,
    get_marketplace_role,
    register_account,
    request_seller_status,
)
from app.services.realtime import realtime_hub, run_async_from_sync

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    account_type: str = "user"
    first_name: str | None = None
    last_name: str | None = None
    campus: str | None = None
    role_name: str | None = None
    superadmin_code: str | None = None


class LoginRequest(BaseModel):
    email_or_username: str
    password: str
    account_type: str | None = None


class SellerStatusRequest(BaseModel):
    account_id: int
    submission_note: str | None = None


class SellerElevationRequest(BaseModel):
    account_id: int


def _emit_verification_summary_update(db: Session) -> None:
    pending_verifications = (
        db.query(func.count(SellerVerificationRequest.request_id))
        .filter(SellerVerificationRequest.status == "pending")
        .scalar()
        or 0
    )
    try:
        run_async_from_sync(
            realtime_hub.broadcast_management_event,
            {
                "type": "management.summary",
                "summary": {
                    "pending_verifications": pending_verifications,
                },
            },
        )
    except RuntimeError:
        pass


def auth_error(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": message})


def _is_trusted_seller(account_id: int, db: Session) -> bool:
    profile = db.query(UserProfile).filter(UserProfile.user_id == account_id).first()
    return bool(profile and profile.is_verified)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        account = register_account(
            db,
            RegistrationData(
                email=payload.email,
                username=payload.username,
                password=payload.password,
                account_type=payload.account_type,
                first_name=payload.first_name,
                last_name=payload.last_name,
                campus=payload.campus,
                role_name=payload.role_name,
                superadmin_code=payload.superadmin_code,
            ),
        )
    except AuthServiceError as exc:
        raise auth_error(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {
        "message": "Registered successfully",
        "account_id": account.account_id,
        "email": account.email,
        "username": account.username,
        "account_type": account.account_type,
        "marketplace_role": get_marketplace_role(account, db),
        "trusted_seller": _is_trusted_seller(account.account_id, db),
    }


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    try:
        account = authenticate_account(
            db,
            identity=payload.email_or_username,
            password=payload.password,
            account_type=payload.account_type,
        )
    except AuthServiceError as exc:
        raise auth_error(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    return {
        "message": "Login successful",
        "account": {
            "account_id": account.account_id,
            "email": account.email,
            "username": account.username,
            "account_type": account.account_type,
            "account_status": account.account_status,
            "marketplace_role": get_marketplace_role(account, db),
            "trusted_seller": _is_trusted_seller(account.account_id, db),
        },
    }


@router.post("/seller-status/request", status_code=status.HTTP_201_CREATED)
def request_seller_access(payload: SellerStatusRequest, db: Session = Depends(get_db)):
    try:
        verification_request = request_seller_status(
            db,
            account_id=payload.account_id,
            submission_note=payload.submission_note,
        )
    except AuthServiceError as exc:
        raise auth_error(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    _emit_verification_summary_update(db)

    return {
        "message": "Trusted seller verification request submitted",
        "request_id": verification_request.request_id,
        "account_id": verification_request.user_id,
        "status": verification_request.status,
    }


@router.post("/seller-status/elevate")
def elevate_seller_status(payload: SellerElevationRequest, db: Session = Depends(get_db)):
    try:
        profile = elevate_buyer_to_seller(
            db,
            account_id=payload.account_id,
        )
    except AuthServiceError as exc:
        raise auth_error(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {
        "message": "Seller access enabled",
        "account_id": profile.user_id,
        "marketplace_role": "seller",
        "trusted_seller": bool(profile.is_verified),
    }
