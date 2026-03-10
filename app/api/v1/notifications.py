from app.api.v1.common import create_crud_router
from app.db.models import Notification

router = create_crud_router(
    model=Notification,
    prefix="/notifications",
    tags=["notifications"],
    pk_field="notification_id",
)
