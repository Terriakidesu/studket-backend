from app.api.v1.common import create_crud_router
from app.db.models import UserProfile

router = create_crud_router(
    model=UserProfile,
    prefix="/user-profiles",
    tags=["user-profiles"],
    pk_field="user_id",
)
