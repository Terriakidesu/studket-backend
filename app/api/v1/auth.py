from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple in-memory user store for now.
USERS: dict[str, dict[str, str]] = {}


def auth_error(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": message})


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(payload: RegisterRequest):
    email = payload.email.strip().lower()
    if not email:
        raise auth_error(400, "Email is required")
    if not payload.password:
        raise auth_error(400, "Password is required")

    if email in USERS:
        raise auth_error(400, "Email already registered")

    USERS[email] = {
        "email": email,
        "password": payload.password,
        "full_name": payload.full_name or "",
    }
    return {"message": "Registered successfully", "email": email}


@router.post("/login")
async def login(payload: LoginRequest):
    email = payload.email.strip().lower()
    if not email or not payload.password:
        raise auth_error(400, "Email and password are required")

    user = USERS.get(email)
    if not user or user["password"] != payload.password:
        raise auth_error(401, "Invalid email or password")

    return {
        "message": "Login successful",
        "user": {"email": user["email"], "full_name": user["full_name"]},
    }
