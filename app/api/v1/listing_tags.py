from app.api.v1.common import create_crud_router
from app.db.models import ListingTag

router = create_crud_router(
    model=ListingTag,
    prefix="/listing-tags",
    tags=["listing-tags"],
    pk_field="listing_id",
)
