from app.api.v1.common import create_crud_router
from app.db.models import Conversation

router = create_crud_router(
    model=Conversation,
    prefix="/conversations",
    tags=["conversations"],
    pk_field="conversation_id",
)
