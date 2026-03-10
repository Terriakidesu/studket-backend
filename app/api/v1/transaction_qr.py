from app.api.v1.common import create_crud_router
from app.db.models import TransactionQR

router = create_crud_router(
    model=TransactionQR,
    prefix="/transaction-qr",
    tags=["transaction-qr"],
    pk_field="transaction_qr_id",
)
