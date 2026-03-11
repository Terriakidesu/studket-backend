from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_dashboard_api_session
from app.db.models import Account, ManagementAccount, UserProfile
from app.db.session import get_db
from app.services.audit import create_audit_log

router = APIRouter(prefix="/profile-pictures", tags=["profile-pictures"])

PROFILE_PICTURE_ROOT = Path("app/static/profile-pictures")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}


def _get_or_create_staff_profile(account: Account, db: Session) -> ManagementAccount:
    profile = (
        db.query(ManagementAccount)
        .filter(ManagementAccount.manager_id == account.account_id)
        .first()
    )
    if profile is not None:
        return profile

    profile = ManagementAccount(
        manager_id=account.account_id,
        role_name="superadmin" if account.account_type == "superadmin" else "manager",
    )
    db.add(profile)
    db.flush()
    return profile


def _get_account_and_profile(
    account_id: int,
    db: Session,
    *,
    create_staff_profile: bool = False,
) -> tuple[Account, UserProfile | ManagementAccount]:
    account = (
        db.query(Account)
        .filter(Account.account_id == account_id)
        .first()
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    if account.account_type == "user":
        profile = (
            db.query(UserProfile)
            .filter(UserProfile.user_id == account_id)
            .first()
        )
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found",
            )
        return account, profile

    if account.account_type not in {"management", "superadmin"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile picture not available for this account type",
        )

    profile = (
        db.query(ManagementAccount)
        .filter(ManagementAccount.manager_id == account_id)
        .first()
    )
    if profile is None:
        if not create_staff_profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Staff profile not found",
            )
        profile = _get_or_create_staff_profile(account, db)
    return account, profile


def _profile_directory(account_id: int) -> Path:
    directory = PROFILE_PICTURE_ROOT / str(account_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _public_path(path: Path) -> str:
    return "/" + path.as_posix().replace("app/", "", 1)


def _avatar_colors(seed: int) -> tuple[str, str]:
    palettes = [
        ("#1d4ed8", "#eff6ff"),
        ("#0f766e", "#f0fdfa"),
        ("#b45309", "#fffbeb"),
        ("#be123c", "#fff1f2"),
        ("#4338ca", "#eef2ff"),
        ("#166534", "#f0fdf4"),
    ]
    return palettes[seed % len(palettes)]


def _profile_photo(profile: UserProfile | ManagementAccount) -> str | None:
    return (getattr(profile, "profile_photo", None) or "").strip() or None


def _set_profile_photo(profile: UserProfile | ManagementAccount, value: str | None) -> None:
    setattr(profile, "profile_photo", value)


def _initials(account: Account, profile: UserProfile | ManagementAccount) -> str:
    parts = [
        (profile.first_name or "").strip(),
        (profile.last_name or "").strip(),
    ]
    letters = "".join(part[:1].upper() for part in parts if part)
    if letters:
        return letters[:2]
    username = (account.username or "").strip()
    return (username[:2] or "U").upper()


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    color = value.lstrip("#")
    return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + chunk_type
        + data
        + struct.pack("!I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _generated_avatar_png(account: Account, profile: UserProfile | ManagementAccount) -> bytes:
    size = 256
    cells = 5
    padding = 20
    usable = size - (padding * 2)
    cell_size = usable // cells
    bg_color, fg_color = _avatar_colors(account.account_id)
    background = _hex_to_rgb(bg_color)
    foreground = _hex_to_rgb(fg_color)
    initials = _initials(account, profile)
    digest = hashlib.sha256(f"{account.account_id}:{initials}:{account.username}".encode("utf-8")).digest()

    pixels = [[background for _ in range(size)] for _ in range(size)]

    bit_index = 0
    for row in range(cells):
        row_pattern: list[bool] = []
        for col in range((cells + 1) // 2):
            byte_index = bit_index // 8
            mask = 1 << (bit_index % 8)
            row_pattern.append(bool(digest[byte_index] & mask))
            bit_index += 1
        mirrored = row_pattern + row_pattern[-2::-1]
        for col, fill in enumerate(mirrored):
            if not fill:
                continue
            start_x = padding + (col * cell_size)
            start_y = padding + (row * cell_size)
            for y in range(start_y, min(start_y + cell_size, size)):
                for x in range(start_x, min(start_x + cell_size, size)):
                    pixels[y][x] = foreground

    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for red, green, blue in row:
            raw.extend((red, green, blue))

    header = struct.pack("!2I5B", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + _png_chunk(b"IEND", b"")
    )


def _ensure_generated_profile_picture(
    account: Account,
    profile: UserProfile | ManagementAccount,
    db: Session,
) -> None:
    current_photo = _profile_photo(profile) or ""
    if current_photo:
        relative_path = current_photo.lstrip("/")
        if relative_path.startswith("static/"):
            existing_path = Path("app") / relative_path
        else:
            existing_path = Path("app/static") / relative_path
        if existing_path.exists():
            return

    directory = _profile_directory(account.account_id)
    target = directory / "generated-avatar.png"
    target.write_bytes(_generated_avatar_png(account, profile))
    _set_profile_photo(profile, _public_path(target))
    db.commit()
    db.refresh(profile)


def _profile_photo_filesystem_path(profile_photo: str | None) -> Path | None:
    current_photo = (profile_photo or "").strip()
    if not current_photo:
        return None
    relative_path = current_photo.lstrip("/")
    if relative_path.startswith("static/"):
        return Path("app") / relative_path
    return Path("app/static") / relative_path


def _delete_local_profile_picture(profile: UserProfile | ManagementAccount) -> None:
    existing_path = _profile_photo_filesystem_path(_profile_photo(profile))
    if existing_path is None or not existing_path.exists():
        return
    if PROFILE_PICTURE_ROOT not in existing_path.parents:
        return
    if existing_path.is_file():
        existing_path.unlink()


def _serialize_profile_picture(
    account: Account,
    profile: UserProfile | ManagementAccount,
) -> dict[str, object]:
    payload = {
        "account_id": account.account_id,
        "account_type": account.account_type,
        "profile_photo": _profile_photo(profile),
        "file_url": _profile_photo(profile),
        "generated": str(_profile_photo(profile) or "").endswith("/generated-avatar.png"),
    }
    if isinstance(profile, UserProfile):
        payload["user_id"] = profile.user_id
    else:
        payload["manager_id"] = profile.manager_id
    return payload


def ensure_account_profile_picture(account_id: int, db: Session) -> dict[str, object]:
    account, profile = _get_account_and_profile(account_id, db, create_staff_profile=True)
    _ensure_generated_profile_picture(account, profile, db)
    return _serialize_profile_picture(account, profile)


def _require_staff_profile_session(request: Request, account: Account) -> None:
    if account.account_type not in {"management", "superadmin"}:
        return

    session_account = request.session.get("account") or {}
    session_type = session_account.get("account_type")
    session_account_id = session_account.get("account_id")
    if session_type not in {"management", "superadmin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Management login required for staff profile pictures",
        )
    if session_account_id != account.account_id and session_type != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage your own staff profile picture",
        )


@router.get("/{account_id}")
def get_profile_picture(account_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    return jsonable_encoder(ensure_account_profile_picture(account_id, db))


@router.post("/upload")
async def upload_profile_picture(
    request: Request,
    account_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    account, profile = _get_account_and_profile(account_id, db, create_staff_profile=True)
    _require_staff_profile_session(request, account)

    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported profile picture file type",
        )

    directory = _profile_directory(account_id)
    target = directory / f"{uuid4().hex}{extension}"
    content = await file.read()
    target.write_bytes(content)

    _set_profile_photo(profile, _public_path(target))
    db.commit()
    db.refresh(profile)
    return jsonable_encoder(_serialize_profile_picture(account, profile))


@router.post("/generate")
def generate_profile_picture(
    request: Request,
    account_id: int = Form(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    account, profile = _get_account_and_profile(account_id, db, create_staff_profile=True)
    _require_staff_profile_session(request, account)
    _delete_local_profile_picture(profile)
    _set_profile_photo(profile, None)
    db.commit()
    db.refresh(profile)
    _ensure_generated_profile_picture(account, profile, db)
    return jsonable_encoder(_serialize_profile_picture(account, profile))


@router.post("/{account_id}/replace")
def replace_inappropriate_profile_picture(
    account_id: int,
    request: Request,
    reason: str = Form("Inappropriate profile picture"),
    staff_account: dict = Depends(require_dashboard_api_session),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    account, profile = _get_account_and_profile(account_id, db, create_staff_profile=True)
    _delete_local_profile_picture(profile)
    _set_profile_photo(profile, None)
    db.flush()
    _ensure_generated_profile_picture(account, profile, db)
    create_audit_log(
        db,
        actor_account_id=staff_account.get("account_id"),
        actor_username=staff_account.get("username"),
        actor_role=staff_account.get("account_type"),
        action="replace_profile_picture",
        target_type="user_profile" if isinstance(profile, UserProfile) else "management_account",
        target_id=str(profile.user_id if isinstance(profile, UserProfile) else profile.manager_id),
        target_label=account.username,
        details=(reason or "").strip() or "Inappropriate profile picture",
    )
    db.commit()
    db.refresh(profile)
    return jsonable_encoder(_serialize_profile_picture(account, profile))
