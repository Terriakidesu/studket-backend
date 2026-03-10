import binascii
import hashlib
import hmac
import os
import secrets
import string


PASSWORD_MIN_LENGTH = 8
PBKDF2_ITERATIONS = 310000
SESSION_SECRET = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
SUPERADMIN_INVITE_CODE = os.getenv("SUPERADMIN_INVITE_CODE")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}$"
        f"{binascii.hexlify(salt).decode()}$"
        f"{binascii.hexlify(digest).decode()}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = binascii.unhexlify(salt_hex.encode())
        expected_digest = binascii.unhexlify(digest_hex.encode())
    except (ValueError, binascii.Error):
        return False

    candidate_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        int(iterations),
    )
    return hmac.compare_digest(candidate_digest, expected_digest)


def validate_password_strength(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters long"
        )
    if not any(char.islower() for char in password):
        raise ValueError("Password must include a lowercase letter")
    if not any(char.isupper() for char in password):
        raise ValueError("Password must include an uppercase letter")
    if not any(char.isdigit() for char in password):
        raise ValueError("Password must include a number")
    if not any(char in string.punctuation for char in password):
        raise ValueError("Password must include a special character")


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)
