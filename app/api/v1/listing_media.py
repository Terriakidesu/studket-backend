from app.api.v1.common import create_crud_router
from app.db.models import ListingMedia

router = create_crud_router(
    model=ListingMedia,
    prefix="/listing-media",
    tags=["listing-media"],
    pk_field="media_id",
)
