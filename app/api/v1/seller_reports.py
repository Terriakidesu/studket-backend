from app.api.v1.common import create_crud_router
from app.db.models import SellerReport

router = create_crud_router(
    model=SellerReport,
    prefix="/seller-reports",
    tags=["seller-reports"],
    pk_field="report_id",
)
