from app.api.v1.common import create_crud_router
from app.db.models import Tag

router = create_crud_router(
    model=Tag,
    prefix="/tags",
    tags=["tags"],
    pk_field="tag_id",
)
