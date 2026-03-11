from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.api.v1.common import serialize_model
from app.db.models import Listing
from app.db.session import get_db
from app.services.listing_discovery import get_recommended_feed, search_listings

router = APIRouter(prefix="/listings", tags=["listings"])


def _get_listing(item_id: int, db: Session) -> Listing:
    instance = db.query(Listing).filter(Listing.listing_id == item_id).first()
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )
    return instance


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
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return jsonable_encoder(
        search_listings(
            db,
            query_text=q,
            listing_type=listing_type,
            min_price=min_price,
            max_price=max_price,
            tag=tag,
            seller_id=seller_id,
            limit=limit,
        )
    )


@router.get("/")
def list_items(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    items = db.query(Listing).all()
    return jsonable_encoder([serialize_model(item) for item in items])


@router.get("/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    instance = _get_listing(item_id, db)
    return jsonable_encoder(serialize_model(instance))


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    instance = Listing(**payload)
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(serialize_model(instance))


@router.patch("/{item_id}")
def update_item(
    item_id: int,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    instance = _get_listing(item_id, db)
    for field, value in payload.items():
        if hasattr(instance, field):
            setattr(instance, field, value)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(serialize_model(instance))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> Response:
    instance = _get_listing(item_id, db)
    db.delete(instance)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
