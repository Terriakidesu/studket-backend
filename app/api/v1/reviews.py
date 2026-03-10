from app.api.v1.common import create_crud_router
from app.db.models import Review

router = create_crud_router(
    model=Review,
    prefix="/reviews",
    tags=["reviews"],
    pk_field="review_id",
)
