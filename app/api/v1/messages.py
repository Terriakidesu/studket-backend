from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.api.v1.common import serialize_model
from app.db.models import Account, Message
from app.db.session import get_db
from app.services.messaging import create_message_record, serialize_message

router = APIRouter(prefix="/messages", tags=["messages"])


def _get_message(message_id: int, db: Session) -> Message:
    instance = db.query(Message).filter(Message.message_id == message_id).first()
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    return instance


def _serialize_message_with_sender(message: Message, db: Session) -> dict[str, Any]:
    sender_username = (
        db.query(Account.username)
        .filter(Account.account_id == message.sender_id)
        .scalar()
    )
    return serialize_message(message, sender_username=sender_username)


@router.get("/")
def list_items(
    conversation_id: int | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(Message)
    if conversation_id is not None:
        query = query.filter(Message.conversation_id == conversation_id)

    query = query.order_by(
        Message.message_id.asc(),
        Message.sent_at.asc().nullslast(),
    )

    if limit is not None:
        query = query.limit(limit)

    items = query.all()
    return jsonable_encoder([_serialize_message_with_sender(item, db) for item in items])


@router.get("/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    instance = _get_message(item_id, db)
    return jsonable_encoder(_serialize_message_with_sender(instance, db))


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conversation_id = payload.get("conversation_id")
    sender_id = payload.get("sender_id")
    message_text = payload.get("message_text")

    if conversation_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="conversation_id is required",
        )
    if sender_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sender_id is required",
        )
    if message_text is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message_text is required",
        )

    try:
        message, _, sender, _ = create_message_record(
            db,
            conversation_id=int(conversation_id),
            sender_id=int(sender_id),
            message_text=str(message_text),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    db.commit()
    db.refresh(message)
    return jsonable_encoder(serialize_message(message, sender_username=sender.username))


@router.patch("/{item_id}")
def update_item(
    item_id: int,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    instance = _get_message(item_id, db)
    immutable_fields = {"message_id", "conversation_id", "sender_id", "sent_at"}
    blocked_fields = [field for field in payload if field in immutable_fields]
    if blocked_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Immutable message fields cannot be updated: {', '.join(sorted(blocked_fields))}",
        )
    for field, value in payload.items():
        if hasattr(instance, field):
            setattr(instance, field, value)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_serialize_message_with_sender(instance, db))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> Response:
    instance = _get_message(item_id, db)
    db.delete(instance)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
