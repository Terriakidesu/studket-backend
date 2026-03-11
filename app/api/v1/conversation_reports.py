from app.api.v1.common import create_crud_router
from app.db.models import ConversationReport

router = create_crud_router(
    model=ConversationReport,
    prefix="/conversation-reports",
    tags=["conversation-reports"],
    pk_field="report_id",
)
