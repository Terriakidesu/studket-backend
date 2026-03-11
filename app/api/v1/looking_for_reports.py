from app.api.v1.common import create_crud_router
from app.db.models import LookingForReport

router = create_crud_router(
    model=LookingForReport,
    prefix="/looking-for-reports",
    tags=["looking-for-reports"],
    pk_field="report_id",
)
