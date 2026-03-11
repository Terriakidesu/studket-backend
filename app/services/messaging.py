from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Account, Conversation, Message, Notification


def serialize_message(message: Message, *, sender_username: str | None = None) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "sender_username": sender_username,
        "message_text": message.message_text,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "is_read": bool(message.is_read),
    }


def serialize_notification(notification: Notification) -> dict[str, Any]:
    return {
        "notification_id": notification.notification_id,
        "user_id": notification.user_id,
        "notification_type": notification.notification_type,
        "title": notification.title,
        "body": notification.body,
        "related_entity_type": notification.related_entity_type,
        "related_entity_id": notification.related_entity_id,
        "is_read": bool(notification.is_read),
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
    }


def get_conversation_or_error(db: Session, conversation_id: int) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter(Conversation.conversation_id == conversation_id)
        .first()
    )
    if conversation is None:
        raise ValueError("Conversation not found")
    return conversation


def ensure_conversation_member(conversation: Conversation, account_id: int) -> None:
    if account_id not in {conversation.participant1_id, conversation.participant2_id}:
        raise ValueError("Account is not a participant in this conversation")


def create_message_record(
    db: Session,
    *,
    conversation_id: int,
    sender_id: int,
    message_text: str,
) -> tuple[Message, Conversation, Account, Account | None]:
    trimmed_message = message_text.strip()
    if not trimmed_message:
        raise ValueError("Message text is required")

    conversation = get_conversation_or_error(db, conversation_id)
    ensure_conversation_member(conversation, sender_id)

    sender = db.query(Account).filter(Account.account_id == sender_id).first()
    if sender is None:
        raise ValueError("Sender account not found")

    recipient_id = (
        conversation.participant2_id
        if conversation.participant1_id == sender_id
        else conversation.participant1_id
    )
    recipient = db.query(Account).filter(Account.account_id == recipient_id).first()

    message = Message(
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_text=trimmed_message,
        is_read=False,
    )
    db.add(message)
    db.flush()
    db.refresh(message)
    return message, conversation, sender, recipient


def create_user_notification(
    db: Session,
    *,
    user_id: int,
    notification_type: str,
    title: str,
    body: str,
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        body=body,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        is_read=False,
    )
    db.add(notification)
    db.flush()
    db.refresh(notification)
    return notification
