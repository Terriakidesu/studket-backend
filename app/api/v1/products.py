from fastapi import APIRouter, Request

router = APIRouter(prefix="/products")


@router.get("/")
async def products_list(request: Request):
    return []  # list of products
