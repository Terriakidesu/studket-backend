from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session

from app.db.session import get_db


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def serialize_model(instance: Any) -> dict[str, Any]:
    return {
        column.key: _serialize_value(getattr(instance, column.key))
        for column in inspect(instance.__class__).columns
    }


def create_crud_router(
    *,
    model: Any,
    prefix: str,
    tags: list[str],
    pk_field: str,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags)

    def _get_instance(item_id: int, db: Session) -> Any:
        instance = db.query(model).filter(getattr(model, pk_field) == item_id).first()
        if instance is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{model.__name__} not found",
            )
        return instance

    @router.get("/")
    def list_items(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
        items = db.query(model).all()
        return jsonable_encoder([serialize_model(item) for item in items])

    @router.get("/{item_id}")
    def get_item(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
        instance = _get_instance(item_id, db)
        return jsonable_encoder(serialize_model(instance))

    @router.post("/", status_code=status.HTTP_201_CREATED)
    def create_item(
        payload: dict[str, Any] = Body(...),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        instance = model(**payload)
        db.add(instance)
        db.commit()
        db.refresh(instance)
        return jsonable_encoder(serialize_model(instance))

    @router.patch("/{item_id}")
    def update_item(
        item_id: int,
        payload: dict[str, Any] = Body(...),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        instance = _get_instance(item_id, db)
        for field, value in payload.items():
            if hasattr(instance, field):
                setattr(instance, field, value)
        db.commit()
        db.refresh(instance)
        return jsonable_encoder(serialize_model(instance))

    @router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_item(item_id: int, db: Session = Depends(get_db)) -> Response:
        instance = _get_instance(item_id, db)
        db.delete(instance)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
