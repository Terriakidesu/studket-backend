from fastapi import APIRouter
from . import listing

router = APIRouter(prefix="/api")

router.include_router(listing.router)
