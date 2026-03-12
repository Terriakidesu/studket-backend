from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import aliased

from app.db.models import (
    Account,
    Conversation,
    ListingReport,
    LookingForReport,
    Message,
    Notification,
    SellerReport,
    SellerVerificationRequest,
)
from app.db.session import SessionLocal
from app.services.messaging import (
    create_message_record,
    create_user_notification,
    serialize_message,
    serialize_notification,
)
from app.services.realtime import realtime_hub

router = APIRouter(tags=["realtime"])
MANAGEMENT_ACCOUNT_TYPES = {"management", "superadmin"}


def _management_socket_account(websocket: WebSocket) -> dict | None:
    account = websocket.session.get("account")
    if not account:
        return None

    expires_at = websocket.session.get("account_expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            websocket.session.pop("account", None)
            websocket.session.pop("account_expires_at", None)
            return None
        if expiry <= datetime.now(timezone.utc):
            websocket.session.pop("account", None)
            websocket.session.pop("account_expires_at", None)
            return None

    if account.get("account_type") not in MANAGEMENT_ACCOUNT_TYPES:
        return None
    return account


def _subscribe_existing_conversations(db, websocket: WebSocket, *, account_id: int) -> list[int]:
    conversation_ids = [
        row.conversation_id
        for row in db.query(Conversation.conversation_id)
        .filter(
            or_(
                Conversation.participant1_id == account_id,
                Conversation.participant2_id == account_id,
            )
        )
        .all()
    ]
    for conversation_id in conversation_ids:
        realtime_hub.subscribe_conversation(websocket, conversation_id=conversation_id)
    return conversation_ids


def _load_conversation_summaries(db, *, account_id: int) -> list[dict]:
    other = aliased(Account)
    rows = (
        db.query(
            Conversation.conversation_id,
            Conversation.conversation_type,
            func.max(Message.sent_at).label("last_message_at"),
            func.count(Message.message_id).label("message_count"),
            other.account_id.label("other_account_id"),
            other.username.label("other_username"),
            other.account_type.label("other_account_type"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.conversation_id)
        .join(
            other,
            or_(
                and_(
                    Conversation.participant1_id == account_id,
                    other.account_id == Conversation.participant2_id,
                ),
                and_(
                    Conversation.participant2_id == account_id,
                    other.account_id == Conversation.participant1_id,
                ),
            ),
        )
        .filter(
            or_(
                Conversation.participant1_id == account_id,
                Conversation.participant2_id == account_id,
            )
        )
        .group_by(
            Conversation.conversation_id,
            Conversation.conversation_type,
            other.account_id,
            other.username,
            other.account_type,
        )
        .order_by(func.max(Message.sent_at).desc().nullslast(), Conversation.conversation_id.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "conversation_id": row.conversation_id,
            "conversation_type": row.conversation_type,
            "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
            "message_count": row.message_count,
            "other_account_id": row.other_account_id,
            "other_username": row.other_username,
            "other_account_type": row.other_account_type,
        }
        for row in rows
    ]


def _load_user_notifications(db, *, account_id: int) -> list[dict]:
    rows = (
        db.query(Notification)
        .filter(Notification.user_id == account_id)
        .order_by(Notification.created_at.desc(), Notification.notification_id.desc())
        .limit(20)
        .all()
    )
    return [serialize_notification(row) for row in rows]


def _management_unread_message_total(db) -> int:
    participant1 = aliased(Account)
    participant2 = aliased(Account)
    sender = aliased(Account)
    return (
        db.query(func.count(Message.message_id))
        .join(Conversation, Conversation.conversation_id == Message.conversation_id)
        .join(sender, sender.account_id == Message.sender_id)
        .join(participant1, participant1.account_id == Conversation.participant1_id)
        .join(participant2, participant2.account_id == Conversation.participant2_id)
        .filter(
            Message.is_read.is_(False),
            sender.account_type == "user",
            or_(
                and_(
                    participant1.account_type.in_(MANAGEMENT_ACCOUNT_TYPES),
                    participant2.account_type == "user",
                ),
                and_(
                    participant2.account_type.in_(MANAGEMENT_ACCOUNT_TYPES),
                    participant1.account_type == "user",
                ),
            ),
        )
        .scalar()
        or 0
    )


async def _broadcast_management_summary(db) -> None:
    await realtime_hub.broadcast_management_event(
        {
            "type": "management.summary",
            "summary": {
                "unread_messages": _management_unread_message_total(db),
                "pending_verifications": (
                    db.query(func.count(SellerVerificationRequest.request_id))
                    .filter(SellerVerificationRequest.status == "pending")
                    .scalar()
                    or 0
                ),
            },
        }
    )


def _mark_conversation_messages_read(
    db,
    *,
    conversation_id: int,
    reader_account_id: int,
) -> int:
    rows = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.sender_id != reader_account_id,
            Message.is_read.is_(False),
        )
        .all()
    )
    for row in rows:
        row.is_read = True
    return len(rows)


async def _broadcast_message_events(
    db,
    *,
    conversation_id: int,
    payload: dict,
    sender_account_id: int,
    recipient_account: Account | None,
    notification_payload: dict | None = None,
) -> None:
    chat_event = {
        "type": "chat.message",
        "conversation_id": conversation_id,
        "message": payload,
    }
    target_account_ids = [sender_account_id]
    if recipient_account is not None:
        target_account_ids.append(recipient_account.account_id)

    await realtime_hub.broadcast_chat_event(
        conversation_id=conversation_id,
        account_ids=target_account_ids,
        payload=chat_event,
    )
    if recipient_account is not None:
        if notification_payload is not None:
            await realtime_hub.send_account_event(
                recipient_account.account_id,
                {
                    "type": "notification.created",
                    "notification": notification_payload,
                },
            )
        if recipient_account.account_type in MANAGEMENT_ACCOUNT_TYPES:
            await realtime_hub.broadcast_management_event(
                {
                    "type": "management.notification",
                    "category": "chat",
                    "title": "New user message",
                    "body": f"{payload.get('sender_username') or 'A user'} sent a message.",
                    "conversation_id": conversation_id,
                    "account_id": recipient_account.account_id,
                }
            )
            await _broadcast_management_summary(db)


async def _broadcast_typing_event(
    *,
    conversation_id: int,
    sender_account: Account,
    target_account_ids: list[int],
    is_typing: bool,
) -> None:
    await realtime_hub.broadcast_chat_event(
        conversation_id=conversation_id,
        account_ids=target_account_ids,
        payload={
            "type": "chat.typing",
            "conversation_id": conversation_id,
            "account_id": sender_account.account_id,
            "username": sender_account.username,
            "account_type": sender_account.account_type,
            "is_typing": is_typing,
        },
    )


@router.websocket("/ws/management")
async def management_socket(websocket: WebSocket):
    session_account = _management_socket_account(websocket)
    if session_account is None:
        await websocket.close(code=1008)
        return

    account_id = int(session_account["account_id"])
    db = SessionLocal()
    try:
        await realtime_hub.connect_account(websocket, account_id=account_id)
        await realtime_hub.connect_management(websocket)
        conversation_ids = _subscribe_existing_conversations(db, websocket, account_id=account_id)

        pending_verifications = (
            db.query(func.count(SellerVerificationRequest.request_id))
            .filter(SellerVerificationRequest.status == "pending")
            .scalar()
            or 0
        )
        open_reports = (
            (db.query(func.count(ListingReport.report_id)).filter(ListingReport.status == "open").scalar() or 0)
            + (db.query(func.count(LookingForReport.report_id)).filter(LookingForReport.status == "open").scalar() or 0)
            + (db.query(func.count(SellerReport.report_id)).filter(SellerReport.status == "open").scalar() or 0)
        )

        await websocket.send_json(
            {
                "type": "bootstrap",
                "channel": "management",
                "account": session_account,
                "conversation_ids": conversation_ids,
                "conversations": _load_conversation_summaries(db, account_id=account_id),
                "summary": {
                    "pending_verifications": pending_verifications,
                    "unread_messages": _management_unread_message_total(db),
                    "open_reports": open_reports,
                },
            }
        )

        while True:
            data = await websocket.receive_json()
            action = str(data.get("action") or "").strip().lower()
            if action == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if action == "subscribe_conversation":
                conversation_id = int(data.get("conversation_id") or 0)
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == conversation_id)
                    .first()
                )
                if conversation and account_id in {conversation.participant1_id, conversation.participant2_id}:
                    realtime_hub.subscribe_conversation(websocket, conversation_id=conversation_id)
                    await websocket.send_json(
                        {
                            "type": "chat.subscribed",
                            "conversation_id": conversation_id,
                        }
                    )
                continue
            if action == "mark_conversation_read":
                conversation_id = int(data.get("conversation_id") or 0)
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == conversation_id)
                    .first()
                )
                if conversation is None or account_id not in {conversation.participant1_id, conversation.participant2_id}:
                    await websocket.send_json({"type": "error", "detail": "Conversation not found"})
                    continue
                read_count = _mark_conversation_messages_read(
                    db,
                    conversation_id=conversation_id,
                    reader_account_id=account_id,
                )
                if read_count:
                    db.commit()
                    await _broadcast_management_summary(db)
                await websocket.send_json(
                    {
                        "type": "chat.read",
                        "conversation_id": conversation_id,
                        "read_count": read_count,
                    }
                )
                continue
            if action == "send_message":
                conversation_id = int(data.get("conversation_id") or 0)
                message_text = str(data.get("message_text") or "")
                try:
                    message, _, sender, recipient = create_message_record(
                        db,
                        conversation_id=conversation_id,
                        sender_id=account_id,
                        message_text=message_text,
                    )
                except ValueError as exc:
                    db.rollback()
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    continue
                notification_payload = None
                if recipient is not None and recipient.account_type == "user":
                    notification = create_user_notification(
                        db,
                        user_id=recipient.account_id,
                        notification_type="chat_message",
                        title=f"New message from {sender.username}",
                        body=message.message_text,
                        related_entity_type="conversation",
                        related_entity_id=conversation_id,
                    )
                    notification_payload = serialize_notification(notification)
                db.commit()
                target_account_ids = [account_id]
                if recipient is not None:
                    target_account_ids.append(recipient.account_id)
                await _broadcast_typing_event(
                    conversation_id=conversation_id,
                    sender_account=sender,
                    target_account_ids=target_account_ids,
                    is_typing=False,
                )
                payload = serialize_message(message, sender_username=sender.username)
                await _broadcast_message_events(
                    db,
                    conversation_id=conversation_id,
                    payload=payload,
                    sender_account_id=account_id,
                    recipient_account=recipient,
                    notification_payload=notification_payload,
                )
                continue
            if action == "typing_status":
                conversation_id = int(data.get("conversation_id") or 0)
                is_typing = bool(data.get("is_typing"))
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == conversation_id)
                    .first()
                )
                if conversation is None or account_id not in {conversation.participant1_id, conversation.participant2_id}:
                    await websocket.send_json({"type": "error", "detail": "Conversation not found"})
                    continue
                sender = (
                    db.query(Account)
                    .filter(Account.account_id == account_id)
                    .first()
                )
                if sender is None:
                    await websocket.send_json({"type": "error", "detail": "Account not found"})
                    continue
                target_account_ids = [conversation.participant1_id, conversation.participant2_id]
                await _broadcast_typing_event(
                    conversation_id=conversation_id,
                    sender_account=sender,
                    target_account_ids=target_account_ids,
                    is_typing=is_typing,
                )
                continue

            await websocket.send_json({"type": "error", "detail": "Unsupported action"})
    except WebSocketDisconnect:
        pass
    finally:
        realtime_hub.disconnect(websocket, account_id=account_id)
        db.close()


@router.websocket("/ws/users/{account_id}")
async def user_socket(websocket: WebSocket, account_id: int):
    db = SessionLocal()
    try:
        account = (
            db.query(Account)
            .filter(Account.account_id == account_id, Account.account_type == "user")
            .first()
        )
        if account is None or account.account_status == "banned":
            await websocket.close(code=1008)
            return

        await realtime_hub.connect_account(websocket, account_id=account_id)
        conversation_ids = _subscribe_existing_conversations(db, websocket, account_id=account_id)
        await websocket.send_json(
            {
                "type": "bootstrap",
                "channel": "user",
                "account": {
                    "account_id": account.account_id,
                    "username": account.username,
                    "account_type": account.account_type,
                    "account_status": account.account_status,
                },
                "conversation_ids": conversation_ids,
                "conversations": _load_conversation_summaries(db, account_id=account_id),
                "notifications": _load_user_notifications(db, account_id=account_id),
            }
        )

        while True:
            data = await websocket.receive_json()
            action = str(data.get("action") or "").strip().lower()
            if action == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if action == "subscribe_conversation":
                conversation_id = int(data.get("conversation_id") or 0)
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == conversation_id)
                    .first()
                )
                if conversation and account_id in {conversation.participant1_id, conversation.participant2_id}:
                    realtime_hub.subscribe_conversation(websocket, conversation_id=conversation_id)
                    await websocket.send_json(
                        {
                            "type": "chat.subscribed",
                            "conversation_id": conversation_id,
                        }
                    )
                continue
            if action == "mark_conversation_read":
                conversation_id = int(data.get("conversation_id") or 0)
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == conversation_id)
                    .first()
                )
                if conversation is None or account_id not in {conversation.participant1_id, conversation.participant2_id}:
                    await websocket.send_json({"type": "error", "detail": "Conversation not found"})
                    continue
                read_count = _mark_conversation_messages_read(
                    db,
                    conversation_id=conversation_id,
                    reader_account_id=account_id,
                )
                if read_count:
                    db.commit()
                    await _broadcast_management_summary(db)
                await websocket.send_json(
                    {
                        "type": "chat.read",
                        "conversation_id": conversation_id,
                        "read_count": read_count,
                    }
                )
                continue
            if action == "mark_notification_read":
                notification_id = int(data.get("notification_id") or 0)
                notification = (
                    db.query(Notification)
                    .filter(Notification.notification_id == notification_id, Notification.user_id == account_id)
                    .first()
                )
                if notification is not None:
                    notification.is_read = True
                    notification.read_at = datetime.now(timezone.utc)
                    db.commit()
                    await websocket.send_json(
                        {
                            "type": "notification.updated",
                            "notification": serialize_notification(notification),
                        }
                    )
                continue
            if action == "send_message":
                conversation_id = int(data.get("conversation_id") or 0)
                message_text = str(data.get("message_text") or "")
                try:
                    message, _, sender, recipient = create_message_record(
                        db,
                        conversation_id=conversation_id,
                        sender_id=account_id,
                        message_text=message_text,
                    )
                except ValueError as exc:
                    db.rollback()
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    continue
                notification_payload = None
                if recipient is not None and recipient.account_type == "user":
                    notification = create_user_notification(
                        db,
                        user_id=recipient.account_id,
                        notification_type="chat_message",
                        title=f"New message from {sender.username}",
                        body=message.message_text,
                        related_entity_type="conversation",
                        related_entity_id=conversation_id,
                    )
                    notification_payload = serialize_notification(notification)
                db.commit()
                target_account_ids = [account_id]
                if recipient is not None:
                    target_account_ids.append(recipient.account_id)
                await _broadcast_typing_event(
                    conversation_id=conversation_id,
                    sender_account=sender,
                    target_account_ids=target_account_ids,
                    is_typing=False,
                )
                payload = serialize_message(message, sender_username=sender.username)
                await _broadcast_message_events(
                    db,
                    conversation_id=conversation_id,
                    payload=payload,
                    sender_account_id=account_id,
                    recipient_account=recipient,
                    notification_payload=notification_payload,
                )
                continue
            if action == "typing_status":
                conversation_id = int(data.get("conversation_id") or 0)
                is_typing = bool(data.get("is_typing"))
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.conversation_id == conversation_id)
                    .first()
                )
                if conversation is None or account_id not in {conversation.participant1_id, conversation.participant2_id}:
                    await websocket.send_json({"type": "error", "detail": "Conversation not found"})
                    continue
                sender = (
                    db.query(Account)
                    .filter(Account.account_id == account_id)
                    .first()
                )
                if sender is None:
                    await websocket.send_json({"type": "error", "detail": "Account not found"})
                    continue
                target_account_ids = [conversation.participant1_id, conversation.participant2_id]
                await _broadcast_typing_event(
                    conversation_id=conversation_id,
                    sender_account=sender,
                    target_account_ids=target_account_ids,
                    is_typing=is_typing,
                )
                continue

            await websocket.send_json({"type": "error", "detail": "Unsupported action"})
    except WebSocketDisconnect:
        pass
    finally:
        realtime_hub.disconnect(websocket, account_id=account_id)
        db.close()
