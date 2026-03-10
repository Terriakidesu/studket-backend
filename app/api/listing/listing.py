from fastapi import APIRouter, Request

router = APIRouter(prefix="/listings")

@router.get("/")
async def products_list(request: Request):
    return []
