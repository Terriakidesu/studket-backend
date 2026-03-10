from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.security import (
    SUPERADMIN_INVITE_CODE,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.db.models import Account, ManagementAccount, UserProfile


ALLOWED_ACCOUNT_TYPES = {"user", "management", "superadmin"}


@dataclass
class RegistrationData:
    email: str
    username: str
    password: str
    account_type: str
    first_name: str | None = None
    last_name: str | None = None
    campus: str | None = None
    role_name: str | None = None
    superadmin_code: str | None = None


class AuthServiceError(ValueError):
    pass


def _normalize_account_type(account_type: str) -> str:
    normalized = account_type.strip().lower()
    if normalized not in ALLOWED_ACCOUNT_TYPES:
        raise AuthServiceError("Invalid account type")
    return normalized


def register_account(db: Session, payload: RegistrationData) -> Account:
    email = payload.email.strip().lower()
    username = payload.username.strip()
    account_type = _normalize_account_type(payload.account_type)

    if not email or not username:
        raise AuthServiceError("Email and username are required")
    if not payload.password:
        raise AuthServiceError("Password is required")

    try:
        validate_password_strength(payload.password)
    except ValueError as exc:
        raise AuthServiceError(str(exc)) from exc

    existing_account = (
        db.query(Account)
        .filter(or_(Account.email == email, Account.username == username))
        .first()
    )
    if existing_account:
        raise AuthServiceError("Email or username already registered")

    if account_type == "superadmin":
        if not SUPERADMIN_INVITE_CODE:
            raise AuthServiceError("Superadmin registration is disabled")
        if payload.superadmin_code != SUPERADMIN_INVITE_CODE:
            raise AuthServiceError("Invalid superadmin invite code")

    account = Account(
        email=email,
        username=username,
        password_hash=hash_password(payload.password),
        account_type=account_type,
        account_status="active",
    )
    db.add(account)
    db.flush()

    if account_type == "user":
        db.add(
            UserProfile(
                user_id=account.account_id,
                first_name=(payload.first_name or "").strip() or None,
                last_name=(payload.last_name or "").strip() or None,
                campus=(payload.campus or "").strip() or None,
            )
        )
    elif account_type == "management":
        db.add(
            ManagementAccount(
                manager_id=account.account_id,
                first_name=(payload.first_name or "").strip() or None,
                last_name=(payload.last_name or "").strip() or None,
                role_name=(payload.role_name or "").strip() or "manager",
            )
        )

    db.commit()
    db.refresh(account)
    return account


def authenticate_account(
    db: Session, *, identity: str, password: str, account_type: str | None = None
) -> Account:
    normalized_identity = identity.strip()
    if not normalized_identity or not password:
        raise AuthServiceError("Credentials are required")

    query = db.query(Account).filter(
        or_(
            Account.email == normalized_identity.lower(),
            Account.username == normalized_identity,
        )
    )
    if account_type:
        query = query.filter(Account.account_type == _normalize_account_type(account_type))

    account = query.first()
    if account is None or not verify_password(password, account.password_hash):
        raise AuthServiceError("Invalid credentials")
    if account.account_status != "active":
        raise AuthServiceError("Account is not active")

    return account
