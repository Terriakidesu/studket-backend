from app.api.v1.common import create_crud_router
from app.db.models import ManagementAccount

router = create_crud_router(
    model=ManagementAccount,
    prefix="/management-accounts",
    tags=["management-accounts"],
    pk_field="manager_id",
)
