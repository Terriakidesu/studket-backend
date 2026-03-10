from fastapi import APIRouter
from . import auth, listing

router = APIRouter(prefix="/v1")

router.include_router(listing.router)
router.include_router(auth.router)
