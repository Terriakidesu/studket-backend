from app.api.v1.common import create_crud_router
from app.db.models import Account

router = create_crud_router(
    model=Account,
    prefix="/accounts",
    tags=["accounts"],
    pk_field="account_id",
)
