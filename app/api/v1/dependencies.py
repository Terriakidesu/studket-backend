from datetime import datetime, timezone

from fastapi import HTTPException, Request, status


DASHBOARD_API_ALLOWED_ACCOUNT_TYPES = {"management", "superadmin"}


def require_dashboard_api_session(request: Request) -> dict:
    account = request.session.get("account")
    if not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Management or superadmin login required"},
        )

    expires_at = request.session.get("account_expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            request.session.pop("account", None)
            request.session.pop("account_expires_at", None)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Session expired. Please sign in again"},
            ) from exc

        if expiry <= datetime.now(timezone.utc):
            request.session.pop("account", None)
            request.session.pop("account_expires_at", None)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Session expired. Please sign in again"},
            )

    if account.get("account_type") not in DASHBOARD_API_ALLOWED_ACCOUNT_TYPES:
        request.session.pop("account", None)
        request.session.pop("account_expires_at", None)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Dashboard API access is restricted to management and superadmin accounts"},
        )

    return account
