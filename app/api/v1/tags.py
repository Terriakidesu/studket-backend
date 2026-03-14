from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.common import create_crud_router
from app.db.models import Listing, ListingTag, Tag
from app.db.session import get_db

router = APIRouter(prefix="/tags", tags=["tags"])

crud_router = create_crud_router(
    model=Tag,
    prefix="/tags",
    tags=["tags"],
    pk_field="tag_id",
)


@router.get("/list")
def list_tags(
    limit: int = Query(default=100, ge=1, le=1000),
    include_unavailable: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = (
        db.query(
            Tag.tag_id,
            Tag.tag_name,
            func.count(func.distinct(ListingTag.listing_id)).label("listing_count"),
        )
        .join(ListingTag, ListingTag.tag_id == Tag.tag_id)
        .join(Listing, Listing.listing_id == ListingTag.listing_id)
    )
    if not include_unavailable:
        query = query.filter(Listing.status == "available")

    rows = (
        query.group_by(Tag.tag_id, Tag.tag_name)
        .order_by(
            func.count(func.distinct(ListingTag.listing_id)).desc(),
            Tag.tag_name.asc(),
        )
        .limit(limit)
        .all()
    )
    items = [
        {
            "tag_id": row.tag_id,
            "tag_name": row.tag_name,
            "listing_count": int(row.listing_count or 0),
        }
        for row in rows
    ]
    return jsonable_encoder(
        {
            "count": len(items),
            "limit": limit,
            "include_unavailable": include_unavailable,
            "items": items,
        }
    )


@router.get("/popular")
def popular_tags(
    limit: int = Query(default=20, ge=1, le=100),
    include_unavailable: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = (
        db.query(
            Tag.tag_id,
            Tag.tag_name,
            func.count(func.distinct(ListingTag.listing_id)).label("listing_count"),
        )
        .join(ListingTag, ListingTag.tag_id == Tag.tag_id)
        .join(Listing, Listing.listing_id == ListingTag.listing_id)
    )
    if not include_unavailable:
        query = query.filter(Listing.status == "available")

    rows = (
        query.group_by(Tag.tag_id, Tag.tag_name)
        .order_by(
            func.count(func.distinct(ListingTag.listing_id)).desc(),
            Tag.tag_name.asc(),
        )
        .limit(limit)
        .all()
    )

    items = [
        {
            "tag_id": row.tag_id,
            "tag_name": row.tag_name,
            "listing_count": int(row.listing_count or 0),
        }
        for row in rows
    ]
    return jsonable_encoder(
        {
            "count": len(items),
            "limit": limit,
            "include_unavailable": include_unavailable,
            "items": items,
        }
    )


router.include_router(crud_router)
