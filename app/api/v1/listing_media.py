from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.api.v1.common import serialize_model
from app.db.models import Listing, ListingMedia
from app.db.session import get_db

router = APIRouter(prefix="/listing-media", tags=["listing-media"])

STATIC_ROOT = Path("app/static")
LISTING_MEDIA_ROOT = STATIC_ROOT / "listing-media"
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _serialize_listing_media(instance: ListingMedia) -> dict:
    payload = serialize_model(instance)
    payload["file_url"] = instance.file_path
    return payload


def _get_listing_media(media_id: int, db: Session) -> ListingMedia:
    instance = db.query(ListingMedia).filter(ListingMedia.media_id == media_id).first()
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ListingMedia not found",
        )
    return instance


def _ensure_listing_exists(listing_id: int, db: Session) -> None:
    listing = db.query(Listing.listing_id).filter(Listing.listing_id == listing_id).first()
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )


def _normalize_public_path(path_value: str) -> str:
    normalized = path_value.strip().replace("\\", "/")
    if normalized.startswith("/static/"):
        return normalized
    if normalized.startswith("static/"):
        return f"/{normalized}"
    if normalized.startswith("listing-media/"):
        return f"/static/{normalized}"
    return normalized


def _local_path_from_public_path(path_value: str) -> Path | None:
    normalized = _normalize_public_path(path_value)
    static_prefix = "/static/"
    if not normalized.startswith(static_prefix):
        return None
    relative_path = normalized[len(static_prefix):]
    return STATIC_ROOT / Path(relative_path)


def _store_uploaded_file(listing_id: int, file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported media type. Allowed extensions: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
        )

    listing_directory = LISTING_MEDIA_ROOT / str(listing_id)
    listing_directory.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid4().hex}{suffix}"
    destination = listing_directory / stored_name

    with destination.open("wb") as output:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)

    return f"/static/listing-media/{listing_id}/{stored_name}"


@router.get("/")
def list_items(db: Session = Depends(get_db)) -> list[dict]:
    items = db.query(ListingMedia).order_by(ListingMedia.listing_id.asc(), ListingMedia.sort_order.asc(), ListingMedia.media_id.asc()).all()
    return jsonable_encoder([_serialize_listing_media(item) for item in items])


@router.get("/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)) -> dict:
    instance = _get_listing_media(item_id, db)
    return jsonable_encoder(_serialize_listing_media(instance))


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    listing_id = payload.get("listing_id")
    file_path = payload.get("file_path")
    if listing_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="listing_id is required")
    if not file_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file_path is required")

    _ensure_listing_exists(int(listing_id), db)
    payload = dict(payload)
    payload["file_path"] = _normalize_public_path(str(file_path))

    instance = ListingMedia(**payload)
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_serialize_listing_media(instance))


@router.post("/upload", status_code=status.HTTP_201_CREATED)
def upload_listing_media(
    listing_id: int = Form(...),
    sort_order: int = Form(0),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    _ensure_listing_exists(listing_id, db)
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file is required")

    public_path = _store_uploaded_file(listing_id, file)
    instance = ListingMedia(
        listing_id=listing_id,
        file_path=public_path,
        sort_order=sort_order,
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_serialize_listing_media(instance))


@router.patch("/{item_id}")
def update_item(
    item_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    instance = _get_listing_media(item_id, db)
    for field, value in payload.items():
        if not hasattr(instance, field):
            continue
        if field == "listing_id" and value is not None:
            _ensure_listing_exists(int(value), db)
        if field == "file_path" and value is not None:
            value = _normalize_public_path(str(value))
        setattr(instance, field, value)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_serialize_listing_media(instance))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> Response:
    instance = _get_listing_media(item_id, db)
    local_path = _local_path_from_public_path(instance.file_path or "")
    db.delete(instance)
    db.commit()

    if local_path is not None and local_path.exists():
        local_path.unlink()
        parent = local_path.parent
        if parent.exists() and parent != LISTING_MEDIA_ROOT:
            try:
                next(parent.iterdir())
            except StopIteration:
                parent.rmdir()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
