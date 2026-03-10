from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.security import SUPERADMIN_INVITE_CODE, generate_csrf_token
from app.db.session import get_db
from app.services.auth import (
    AuthServiceError,
    RegistrationData,
    authenticate_account,
    register_account,
)

router = APIRouter(tags=["web-auth"])
templates = Jinja2Templates(directory="app/templates")
WEB_ALLOWED_ACCOUNT_TYPES = {"management", "superadmin"}


def _ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = generate_csrf_token()
        request.session["csrf_token"] = token
    return token


def _verify_csrf(request: Request, submitted_token: str) -> None:
    session_token = request.session.get("csrf_token")
    if not session_token or session_token != submitted_token:
        raise AuthServiceError("Invalid form token")


def _render_auth_page(
    request: Request,
    *,
    template_name: str,
    active_role: str = "management",
    error: str | None = None,
    success: str | None = None,
    form_data: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
):
    csrf_token = _ensure_csrf_token(request)
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "title": "Secure Access",
            "active_role": active_role,
            "error": error,
            "success": success,
            "form_data": form_data or {},
            "csrf_token": csrf_token,
            "superadmin_enabled": bool(SUPERADMIN_INVITE_CODE),
            "session_account": request.session.get("account"),
        },
        status_code=status_code,
    )


@router.get("/auth", response_class=HTMLResponse)
def auth_portal(request: Request):
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _render_auth_page(request, template_name="login.html")


@router.get("/auth/register", response_class=HTMLResponse)
def register_page(request: Request):
    return _render_auth_page(request, template_name="register.html")


def _validate_web_account_type(account_type: str) -> str:
    normalized = account_type.strip().lower()
    if normalized not in WEB_ALLOWED_ACCOUNT_TYPES:
        raise AuthServiceError("User accounts can only sign in through the app")
    return normalized


@router.post("/auth/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    identity: str = Form(...),
    password: str = Form(...),
    account_type: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account_type = _validate_web_account_type(account_type)
    except AuthServiceError as exc:
        return _render_auth_page(
            request,
            template_name="login.html",
            active_role="management",
            error=str(exc),
            form_data={"identity": identity},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        _verify_csrf(request, csrf_token)
        account = authenticate_account(
            db,
            identity=identity,
            password=password,
            account_type=account_type,
        )
    except AuthServiceError as exc:
        return _render_auth_page(
            request,
            template_name="login.html",
            active_role=account_type,
            error=str(exc),
            form_data={"identity": identity},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    request.session["account"] = {
        "account_id": account.account_id,
        "email": account.email,
        "username": account.username,
        "account_type": account.account_type,
    }
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    account_type: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    campus: str = Form(""),
    role_name: str = Form(""),
    superadmin_code: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        account_type = _validate_web_account_type(account_type)
    except AuthServiceError as exc:
        return _render_auth_page(
            request,
            template_name="register.html",
            active_role="management",
            error=str(exc),
            form_data={
                "email": email,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "role_name": role_name,
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if password != confirm_password:
        return _render_auth_page(
            request,
            template_name="register.html",
            active_role=account_type,
            error="Passwords do not match",
            form_data={
                "email": email,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "role_name": role_name,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        _verify_csrf(request, csrf_token)
        register_account(
            db,
            RegistrationData(
                email=email,
                username=username,
                password=password,
                account_type=account_type,
                first_name=first_name,
                last_name=last_name,
                campus=campus,
                role_name=role_name,
                superadmin_code=superadmin_code or None,
            ),
        )
    except AuthServiceError as exc:
        return _render_auth_page(
            request,
            template_name="register.html",
            active_role=account_type,
            error=str(exc),
            form_data={
                "email": email,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "role_name": role_name,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return _render_auth_page(
        request,
        template_name="login.html",
        active_role=account_type,
        success="Registration complete. You can sign in now.",
        form_data={"identity": email},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    account = request.session.get("account")
    if not account:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    if account.get("account_type") not in WEB_ALLOWED_ACCOUNT_TYPES:
        request.session.clear()
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    csrf_token = _ensure_csrf_token(request)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "Dashboard",
            "account": account,
            "csrf_token": csrf_token,
        },
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    try:
        _verify_csrf(request, csrf_token)
    except AuthServiceError:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
