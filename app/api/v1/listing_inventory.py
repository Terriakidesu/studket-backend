from app.api.v1.common import create_crud_router
from app.db.models import ListingInventory

router = create_crud_router(
    model=ListingInventory,
    prefix="/listing-inventory",
    tags=["listing-inventory"],
    pk_field="inventory_id",
)
