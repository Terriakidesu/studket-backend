from app.api.v1.common import create_crud_router
from app.db.models import Transaction

router = create_crud_router(
    model=Transaction,
    prefix="/transactions",
    tags=["transactions"],
    pk_field="transaction_id",
)
