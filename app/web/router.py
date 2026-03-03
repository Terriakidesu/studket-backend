from fastapi import APIRouter
from .pages.home import router

web = APIRouter()

web.include_router(router)
