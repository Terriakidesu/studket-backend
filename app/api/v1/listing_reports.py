from app.api.v1.common import create_crud_router
from app.db.models import ListingReport

router = create_crud_router(
    model=ListingReport,
    prefix="/listing-reports",
    tags=["listing-reports"],
    pk_field="report_id",
)
