from fastapi import APIRouter
from . import products

router = APIRouter(prefix="/v1")

router.include_router(products.router)
