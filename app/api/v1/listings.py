from app.api.v1.common import create_crud_router
from app.db.models import Listing

router = create_crud_router(
    model=Listing,
    prefix="/listings",
    tags=["listings"],
    pk_field="listing_id",
)
