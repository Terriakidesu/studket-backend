from fastapi import APIRouter
from .pages import home

router = APIRouter()

router.include_router(home.router)
