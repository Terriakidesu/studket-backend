from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import Account, UserProfile
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    account_type: str = "user"
    first_name: str | None = None
    last_name: str | None = None
    campus: str | None = None


class LoginRequest(BaseModel):
    email_or_username: str
    password: str


def auth_error(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": message})


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing_account = (
        db.query(Account)
        .filter(or_(Account.email == payload.email, Account.username == payload.username))
        .first()
    )
    if existing_account:
        raise auth_error(status.HTTP_400_BAD_REQUEST, "Email or username already registered")

    account = Account(
        email=payload.email.strip().lower(),
        username=payload.username.strip(),
        password_hash=payload.password,
        account_type=payload.account_type,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    if payload.account_type == "user":
        profile = UserProfile(
            user_id=account.account_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            campus=payload.campus,
        )
        db.add(profile)
        db.commit()

    return {
        "message": "Registered successfully",
        "account_id": account.account_id,
        "email": account.email,
        "username": account.username,
        "account_type": account.account_type,
    }


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    identity = payload.email_or_username.strip()
    if not identity or not payload.password:
        raise auth_error(status.HTTP_400_BAD_REQUEST, "Credentials are required")

    account = (
        db.query(Account)
        .filter(or_(Account.email == identity.lower(), Account.username == identity))
        .first()
    )
    if account is None or account.password_hash != payload.password:
        raise auth_error(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    return {
        "message": "Login successful",
        "account": {
            "account_id": account.account_id,
            "email": account.email,
            "username": account.username,
            "account_type": account.account_type,
            "account_status": account.account_status,
        },
    }
