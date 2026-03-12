from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import Numeric

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


def _validate_numeric_payload(model: Any, payload: dict[str, Any]) -> None:
    mapper = inspect(model)
    for column in mapper.columns:
        if column.key not in payload:
            continue
        value = payload[column.key]
        if value is None or not isinstance(column.type, Numeric):
            continue

        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{column.key} must be a valid numeric value",
            )

        precision = column.type.precision
        scale = column.type.scale
        if precision is None or scale is None:
            continue

        max_abs_value = Decimal(10) ** (precision - scale)
        if abs(decimal_value) >= max_abs_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{column.key} is too large. "
                    f"Maximum absolute value is less than {max_abs_value}."
                ),
            )

        exponent = decimal_value.as_tuple().exponent
        fractional_digits = -exponent if exponent < 0 else 0
        if fractional_digits > scale:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{column.key} supports at most {scale} decimal places",
            )


def create_crud_router(
    *,
    model: Any,
    prefix: str,
    tags: list[str],
    pk_field: str,
    enable_create: bool = True,
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

    if enable_create:
        @router.post("/", status_code=status.HTTP_201_CREATED)
        def create_item(
            payload: dict[str, Any] = Body(...),
            db: Session = Depends(get_db),
        ) -> dict[str, Any]:
            _validate_numeric_payload(model, payload)
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
        _validate_numeric_payload(model, payload)
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
