from fastapi import APIRouter
from .pages import auth, home

router = APIRouter()

router.include_router(home.router)
router.include_router(auth.router)
