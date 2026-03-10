from app.api.v1.common import create_crud_router
from app.db.models import Message

router = create_crud_router(
    model=Message,
    prefix="/messages",
    tags=["messages"],
    pk_field="message_id",
)
