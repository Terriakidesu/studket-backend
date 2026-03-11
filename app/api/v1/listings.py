from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.api.v1.common import serialize_model
from app.db.models import Listing, UserProfile
from app.db.session import get_db
from app.services.listing_discovery import get_recommended_feed, search_listings

router = APIRouter(prefix="/listings", tags=["listings"])


def _normalize_listing_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    owner_id = normalized.pop("owner_id", None)
    if owner_id is not None and "seller_id" not in normalized:
        normalized["seller_id"] = owner_id
    return normalized


def _serialize_listing(instance: Listing) -> dict[str, Any]:
    payload = serialize_model(instance)
    payload["owner_id"] = instance.seller_id
    if payload.get("listing_type") == "looking_for":
        payload["poster_id"] = instance.seller_id
    return payload


def _get_listing(item_id: int, db: Session) -> Listing:
    instance = db.query(Listing).filter(Listing.listing_id == item_id).first()
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )
    return instance


def _require_user_profile(user_id: int, db: Session) -> None:
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == user_id)
        .first()
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )


def _validate_listing_creator(payload: dict[str, Any], db: Session) -> None:
    seller_id = payload.get("seller_id")
    if seller_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="seller_id is required",
        )
    _require_user_profile(seller_id, db)


@router.get("/feed")
def feed(
    user_id: int | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return jsonable_encoder(
        get_recommended_feed(
            db,
            user_id=user_id,
            limit=limit,
            tags=tags,
        )
    )


@router.get("/search")
def search(
    q: str | None = Query(default=None),
    listing_type: str | None = Query(default=None),
    min_price: Decimal | None = Query(default=None),
    max_price: Decimal | None = Query(default=None),
    tag: str | None = Query(default=None),
    seller_id: int | None = Query(default=None),
    owner_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    effective_seller_id = owner_id if owner_id is not None else seller_id
    return jsonable_encoder(
        search_listings(
            db,
            query_text=q,
            listing_type=listing_type,
            min_price=min_price,
            max_price=max_price,
            tag=tag,
            seller_id=effective_seller_id,
            limit=limit,
        )
    )


@router.get("/")
def list_items(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    items = db.query(Listing).all()
    return jsonable_encoder([_serialize_listing(item) for item in items])


@router.get("/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    instance = _get_listing(item_id, db)
    return jsonable_encoder(_serialize_listing(instance))


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = _normalize_listing_payload(payload)
    _validate_listing_creator(payload, db)
    instance = Listing(**payload)
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_serialize_listing(instance))


@router.patch("/{item_id}")
def update_item(
    item_id: int,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = _normalize_listing_payload(payload)
    instance = _get_listing(item_id, db)
    if "seller_id" in payload or "listing_type" in payload:
        next_payload = {
            "seller_id": payload.get("seller_id", instance.seller_id),
            "listing_type": payload.get("listing_type", instance.listing_type),
        }
        _validate_listing_creator(next_payload, db)
    for field, value in payload.items():
        if hasattr(instance, field):
            setattr(instance, field, value)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_serialize_listing(instance))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> Response:
    instance = _get_listing(item_id, db)
    db.delete(instance)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
