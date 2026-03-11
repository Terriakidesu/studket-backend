from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import UserProfile
from app.db.session import get_db
from app.services.auth import (
    AuthServiceError,
    RegistrationData,
    authenticate_account,
    get_marketplace_role,
    register_account,
    request_seller_status,
)

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

    return {
        "message": "Trusted seller verification request submitted",
        "request_id": verification_request.request_id,
        "account_id": verification_request.user_id,
        "status": verification_request.status,
    }
