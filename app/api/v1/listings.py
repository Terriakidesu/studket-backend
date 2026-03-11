from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.v1.common import serialize_model
from app.db.models import Account, Conversation, Listing, ListingMedia, ListingTag, Message, Notification, Tag, UserProfile
from app.db.session import get_db
from app.services.listing_discovery import build_listing_payloads, get_recommended_feed, search_listings
from app.services.messaging import create_message_record, serialize_message

router = APIRouter(prefix="/listings", tags=["listings"])


class ListingInquiryPayload(BaseModel):
    account_id: int
    message_text: str | None = None


def _normalize_listing_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    owner_id = normalized.pop("owner_id", None)
    if owner_id is not None and "seller_id" not in normalized:
        normalized["seller_id"] = owner_id
    return normalized


def _normalize_tag_names(raw_tags: Any) -> list[str]:
    if raw_tags is None:
        return []
    if isinstance(raw_tags, str):
        values = [raw_tags]
    elif isinstance(raw_tags, list):
        values = raw_tags
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tags must be a string or array of strings",
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag_name = str(value or "").strip().lower()
        if not tag_name:
            continue
        if tag_name in seen:
            continue
        seen.add(tag_name)
        normalized.append(tag_name)
    return normalized


def _serialize_listing(instance: Listing) -> dict[str, Any]:
    payload = serialize_model(instance)
    payload["owner_id"] = instance.seller_id
    if payload.get("listing_type") == "looking_for":
        payload["poster_id"] = instance.seller_id
    return payload


def _serialize_listing_media_rows(media_rows: list[ListingMedia]) -> list[dict[str, Any]]:
    return [
        {
            "media_id": row.media_id,
            "listing_id": row.listing_id,
            "file_path": row.file_path,
            "file_url": row.file_path,
            "sort_order": row.sort_order,
        }
        for row in media_rows
    ]


def _present_listing_with_media(instance: Listing, db: Session) -> dict[str, Any]:
    payload = _serialize_listing(instance)
    seller_account_type = (
        db.query(Account.account_type)
        .filter(Account.account_id == instance.seller_id)
        .scalar()
    )
    payload["seller_profile_available"] = seller_account_type == "user"
    tag_rows = (
        db.query(Tag.tag_name)
        .join(ListingTag, ListingTag.tag_id == Tag.tag_id)
        .filter(ListingTag.listing_id == instance.listing_id)
        .order_by(Tag.tag_name.asc())
        .all()
    )
    payload["tags"] = [row.tag_name for row in tag_rows]
    media_rows = (
        db.query(ListingMedia)
        .filter(ListingMedia.listing_id == instance.listing_id)
        .order_by(ListingMedia.sort_order.asc(), ListingMedia.media_id.asc())
        .all()
    )
    payload["media"] = _serialize_listing_media_rows(media_rows)
    payload["primary_media_url"] = payload["media"][0]["file_url"] if payload["media"] else None
    return payload


def _get_listing(item_id: int, db: Session) -> Listing:
    instance = db.query(Listing).filter(Listing.listing_id == item_id).first()
    if instance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )
    return instance


def _get_account_listing_items(
    account_id: int,
    db: Session,
    *,
    listing_type: str | None = None,
) -> list[Listing]:
    account = (
        db.query(Account)
        .filter(Account.account_id == account_id, Account.account_type == "user")
        .first()
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found",
        )

    query = db.query(Listing).filter(Listing.seller_id == account_id)
    if listing_type is not None:
        query = query.filter(Listing.listing_type == listing_type)

    return query.order_by(Listing.created_at.desc(), Listing.listing_id.desc()).all()


def _require_user_profile(user_id: int, db: Session) -> None:
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == user_id)
        .first()
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )


def _require_seller_profile(user_id: int, db: Session) -> None:
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == user_id)
        .first()
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )
    if not profile.is_seller:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seller access required for normal listings",
        )


def _validate_listing_creator(payload: dict[str, Any], db: Session) -> None:
    seller_id = payload.get("seller_id")
    if seller_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="seller_id is required",
        )
    listing_type = payload.get("listing_type")
    if listing_type == "looking_for":
        _require_user_profile(seller_id, db)
        return
    _require_seller_profile(seller_id, db)


def _get_user_account(user_id: int, db: Session) -> Account:
    account = (
        db.query(Account)
        .filter(Account.account_id == user_id, Account.account_type == "user")
        .first()
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found",
        )
    _require_user_profile(user_id, db)
    return account


def _get_inquiry_conversation_type(listing: Listing) -> str:
    prefix = "looking_for_inquiry" if listing.listing_type == "looking_for" else "listing_inquiry"
    return f"{prefix}:{listing.listing_id}"


def _find_inquiry_conversation(
    *,
    listing: Listing,
    account_id: int,
    db: Session,
) -> Conversation | None:
    owner_id = listing.seller_id
    if owner_id is None:
        return None
    conversation_type = _get_inquiry_conversation_type(listing)
    return (
        db.query(Conversation)
        .filter(
            Conversation.conversation_type == conversation_type,
            or_(
                (Conversation.participant1_id == owner_id) & (Conversation.participant2_id == account_id),
                (Conversation.participant1_id == account_id) & (Conversation.participant2_id == owner_id),
            ),
        )
        .first()
    )


def _parse_inquiry_listing_id(conversation_type: str | None) -> int | None:
    if not conversation_type or ":" not in conversation_type:
        return None
    prefix, raw_listing_id = conversation_type.split(":", 1)
    if prefix not in {"listing_inquiry", "looking_for_inquiry"}:
        return None
    try:
        return int(raw_listing_id)
    except ValueError:
        return None


def _present_inquiry_conversation(
    conversation: Conversation,
    *,
    listing: Listing,
    account_id: int | None,
    db: Session,
) -> dict[str, Any]:
    participant_ids = [participant_id for participant_id in (conversation.participant1_id, conversation.participant2_id) if participant_id is not None]
    account_rows = (
        db.query(Account.account_id, Account.username)
        .filter(Account.account_id.in_(participant_ids))
        .all()
    )
    usernames = {row.account_id: row.username for row in account_rows}

    last_message = (
        db.query(Message, Account.username.label("sender_username"))
        .outerjoin(Account, Account.account_id == Message.sender_id)
        .filter(Message.conversation_id == conversation.conversation_id)
        .order_by(Message.message_id.desc())
        .first()
    )

    inquirer_id = next(
        (participant_id for participant_id in participant_ids if participant_id != listing.seller_id),
        None,
    )
    payload = {
        "conversation_id": conversation.conversation_id,
        "conversation_type": conversation.conversation_type,
        "listing_id": listing.listing_id,
        "listing_type": listing.listing_type,
        "listing_title": listing.title,
        "listing_status": listing.status,
        "owner_id": listing.seller_id,
        "owner_username": usernames.get(listing.seller_id),
        "inquirer_id": inquirer_id,
        "inquirer_username": usernames.get(inquirer_id),
        "participant1_id": conversation.participant1_id,
        "participant1_username": usernames.get(conversation.participant1_id),
        "participant2_id": conversation.participant2_id,
        "participant2_username": usernames.get(conversation.participant2_id),
        "created_at": conversation.created_at,
        "is_owner_view": account_id == listing.seller_id,
    }
    if last_message is not None:
        message_row, sender_username = last_message
        payload["last_message"] = serialize_message(message_row, sender_username=sender_username)
    else:
        payload["last_message"] = None
    return payload


def _sync_listing_tags(db: Session, *, listing_id: int, tag_names: list[str]) -> None:
    existing_links = (
        db.query(ListingTag)
        .filter(ListingTag.listing_id == listing_id)
        .all()
    )
    existing_by_tag_id = {row.tag_id: row for row in existing_links}

    desired_tag_ids: set[int] = set()
    for tag_name in tag_names:
        existing_tag = (
            db.query(Tag)
            .filter(Tag.tag_name == tag_name)
            .first()
        )
        if existing_tag is None:
            existing_tag = Tag(tag_name=tag_name)
            db.add(existing_tag)
            db.flush()
        desired_tag_ids.add(existing_tag.tag_id)
        if existing_tag.tag_id not in existing_by_tag_id:
            db.add(ListingTag(listing_id=listing_id, tag_id=existing_tag.tag_id))

    for tag_id, link in existing_by_tag_id.items():
        if tag_id not in desired_tag_ids:
            db.delete(link)


@router.get("/feed")
def feed(
    user_id: int | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return jsonable_encoder(
        get_recommended_feed(
            db,
            user_id=user_id,
            limit=limit,
            tags=tags,
        )
    )


@router.get("/search")
def search(
    q: str | None = Query(default=None),
    listing_type: str | None = Query(default=None),
    min_price: Decimal | None = Query(default=None),
    max_price: Decimal | None = Query(default=None),
    tag: str | None = Query(default=None),
    seller_id: int | None = Query(default=None),
    owner_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    effective_seller_id = owner_id if owner_id is not None else seller_id
    return jsonable_encoder(
        search_listings(
            db,
            query_text=q,
            listing_type=listing_type,
            min_price=min_price,
            max_price=max_price,
            tag=tag,
            seller_id=effective_seller_id,
            limit=limit,
        )
    )


@router.get("/users/{account_id}/inquiries")
def get_user_inquiries(
    account_id: int,
    listing_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _get_user_account(account_id, db)
    query = db.query(Conversation).filter(
        or_(
            Conversation.conversation_type.like("listing_inquiry:%"),
            Conversation.conversation_type.like("looking_for_inquiry:%"),
        ),
        or_(
            Conversation.participant1_id == account_id,
            Conversation.participant2_id == account_id,
        ),
    )
    conversations = query.order_by(Conversation.created_at.desc(), Conversation.conversation_id.desc()).all()

    listing_ids = [
        listing_id
        for listing_id in (
            _parse_inquiry_listing_id(conversation.conversation_type)
            for conversation in conversations
        )
        if listing_id is not None
    ]
    listings = (
        db.query(Listing)
        .filter(Listing.listing_id.in_(listing_ids))
        .all()
        if listing_ids
        else []
    )
    listings_by_id = {listing.listing_id: listing for listing in listings}

    items: list[dict[str, Any]] = []
    for conversation in conversations:
        listing_id = _parse_inquiry_listing_id(conversation.conversation_type)
        if listing_id is None:
            continue
        listing = listings_by_id.get(listing_id)
        if listing is None:
            continue
        if listing_type is not None and listing.listing_type != listing_type:
            continue
        items.append(
            _present_inquiry_conversation(
                conversation,
                listing=listing,
                account_id=account_id,
                db=db,
            )
        )

    items.sort(
        key=lambda item: (
            item["last_message"]["sent_at"] if item.get("last_message") else "",
            item["conversation_id"],
        ),
        reverse=True,
    )
    return jsonable_encoder(
        {
            "account_id": account_id,
            "listing_type": listing_type,
            "count": len(items),
            "items": items,
        }
    )


@router.get("/")
def list_items(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    items = db.query(Listing).all()
    return jsonable_encoder([_present_listing_with_media(item, db) for item in items])


@router.get("/users/{account_id}")
def get_user_listings(account_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    items = _get_account_listing_items(account_id, db)
    payload_items = [_present_listing_with_media(item, db) for item in items]
    return jsonable_encoder(
        {
            "account_id": account_id,
            "count": len(payload_items),
            "items": payload_items,
        }
    )


@router.get("/users/{account_id}/looking-for")
def get_user_looking_for_posts(account_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    items = _get_account_listing_items(account_id, db, listing_type="looking_for")
    payload_items = [_present_listing_with_media(item, db) for item in items]
    return jsonable_encoder(
        {
            "account_id": account_id,
            "listing_type": "looking_for",
            "count": len(payload_items),
            "items": payload_items,
        }
    )


@router.get("/{item_id}/inquiries")
def get_item_inquiries(
    item_id: int,
    account_id: int = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _get_user_account(account_id, db)
    listing = _get_listing(item_id, db)
    if listing.seller_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Listing owner not found",
        )

    inquiry_type = _get_inquiry_conversation_type(listing)
    query = db.query(Conversation).filter(Conversation.conversation_type == inquiry_type)
    if account_id != listing.seller_id:
        query = query.filter(
            or_(
                (Conversation.participant1_id == listing.seller_id) & (Conversation.participant2_id == account_id),
                (Conversation.participant1_id == account_id) & (Conversation.participant2_id == listing.seller_id),
            )
        )

    conversations = query.order_by(Conversation.created_at.desc(), Conversation.conversation_id.desc()).all()
    items = [
        _present_inquiry_conversation(
            conversation,
            listing=listing,
            account_id=account_id,
            db=db,
        )
        for conversation in conversations
    ]
    items.sort(
        key=lambda item: (
            item["last_message"]["sent_at"] if item.get("last_message") else "",
            item["conversation_id"],
        ),
        reverse=True,
    )
    return jsonable_encoder(
        {
            "listing_id": listing.listing_id,
            "listing_type": listing.listing_type,
            "account_id": account_id,
            "count": len(items),
            "items": items,
        }
    )


@router.post("/{item_id}/inquiries", status_code=status.HTTP_201_CREATED)
def open_item_inquiry(
    item_id: int,
    payload: ListingInquiryPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    requester = _get_user_account(payload.account_id, db)
    listing = _get_listing(item_id, db)
    if listing.seller_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Listing owner not found",
        )
    if payload.account_id == listing.seller_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot open an inquiry on your own listing",
        )

    owner = _get_user_account(listing.seller_id, db)
    conversation = _find_inquiry_conversation(listing=listing, account_id=payload.account_id, db=db)
    created = False
    if conversation is None:
        conversation = Conversation(
            participant1_id=listing.seller_id,
            participant2_id=payload.account_id,
            conversation_type=_get_inquiry_conversation_type(listing),
        )
        db.add(conversation)
        db.flush()
        created = True

    message_payload = None
    trimmed_message = (payload.message_text or "").strip()
    if trimmed_message:
        message, _, _, _ = create_message_record(
            db,
            conversation_id=conversation.conversation_id,
            sender_id=requester.account_id,
            message_text=trimmed_message,
        )
        db.flush()
        message_payload = serialize_message(message, sender_username=requester.username)
        db.add(
            Notification(
                user_id=owner.account_id,
                notification_type="listing_inquiry",
                title="New inquiry received",
                body=f"{requester.username} sent an inquiry about {listing.title}.",
                related_entity_type="conversation",
                related_entity_id=conversation.conversation_id,
                is_read=False,
            )
        )

    db.commit()
    db.refresh(conversation)
    return jsonable_encoder(
        {
            "message": "Inquiry conversation opened" if created else "Inquiry conversation found",
            "created": created,
            "conversation": _present_inquiry_conversation(
                conversation,
                listing=listing,
                account_id=payload.account_id,
                db=db,
            ),
            "initial_message": message_payload,
        }
    )


@router.get("/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    instance = _get_listing(item_id, db)
    return jsonable_encoder(_present_listing_with_media(instance, db))


@router.get("/{item_id}/media")
def get_item_media(item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    instance = _get_listing(item_id, db)
    media_rows = (
        db.query(ListingMedia)
        .filter(ListingMedia.listing_id == instance.listing_id)
        .order_by(ListingMedia.sort_order.asc(), ListingMedia.media_id.asc())
        .all()
    )
    media = _serialize_listing_media_rows(media_rows)
    return jsonable_encoder(
        {
            "listing_id": instance.listing_id,
            "count": len(media),
            "items": media,
            "primary_media_url": media[0]["file_url"] if media else None,
        }
    )


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = _normalize_listing_payload(payload)
    tag_names = _normalize_tag_names(payload.pop("tags", None))
    _validate_listing_creator(payload, db)
    instance = Listing(**payload)
    db.add(instance)
    db.flush()
    _sync_listing_tags(db, listing_id=instance.listing_id, tag_names=tag_names)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_present_listing_with_media(instance, db))


@router.patch("/{item_id}")
def update_item(
    item_id: int,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = _normalize_listing_payload(payload)
    tags_provided = "tags" in payload
    tag_names = _normalize_tag_names(payload.pop("tags", None)) if tags_provided else []
    instance = _get_listing(item_id, db)
    if "seller_id" in payload or "listing_type" in payload:
        next_payload = {
            "seller_id": payload.get("seller_id", instance.seller_id),
            "listing_type": payload.get("listing_type", instance.listing_type),
        }
        _validate_listing_creator(next_payload, db)
    for field, value in payload.items():
        if hasattr(instance, field):
            setattr(instance, field, value)
    if tags_provided:
        _sync_listing_tags(db, listing_id=instance.listing_id, tag_names=tag_names)
    db.commit()
    db.refresh(instance)
    return jsonable_encoder(_present_listing_with_media(instance, db))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> Response:
    instance = _get_listing(item_id, db)
    db.delete(instance)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
