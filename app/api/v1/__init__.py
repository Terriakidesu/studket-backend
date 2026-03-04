from fastapi import APIRouter
from . import auth, products

router = APIRouter(prefix="/v1")

router.include_router(products.router)
router.include_router(auth.router)
