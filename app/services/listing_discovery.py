from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.v1.common import serialize_model
from app.db.models import (
    Account,
    Listing,
    ListingTag,
    Review,
    SellerVerificationRequest,
    Tag,
    Transaction,
    UserProfile,
)


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _listing_tags_map(db: Session, listing_ids: list[int]) -> dict[int, list[str]]:
    if not listing_ids:
        return {}

    rows = (
        db.query(ListingTag.listing_id, Tag.tag_name)
        .join(Tag, Tag.tag_id == ListingTag.tag_id)
        .filter(ListingTag.listing_id.in_(listing_ids))
        .all()
    )
    mapping: dict[int, list[str]] = {listing_id: [] for listing_id in listing_ids}
    for listing_id, tag_name in rows:
        mapping.setdefault(listing_id, []).append(tag_name)
    return mapping


def _seller_rating_map(db: Session) -> dict[int, dict[str, float]]:
    rows = (
        db.query(
            Review.reviewee_id,
            func.avg(Review.rating).label("average_rating"),
            func.count(Review.review_id).label("review_count"),
        )
        .group_by(Review.reviewee_id)
        .all()
    )
    return {
        row.reviewee_id: {
            "average_rating": float(row.average_rating or 0),
            "review_count": int(row.review_count or 0),
        }
        for row in rows
        if row.reviewee_id is not None
    }


def _verified_seller_ids(db: Session) -> set[int]:
    rows = (
        db.query(SellerVerificationRequest.user_id)
        .filter(SellerVerificationRequest.status == "approved")
        .all()
    )
    return {row.user_id for row in rows if row.user_id is not None}


def _user_preference_profile(db: Session, user_id: int) -> dict[str, Any]:
    purchased_listing_ids = [
        row.listing_id
        for row in db.query(Transaction.listing_id)
        .filter(Transaction.buyer_id == user_id, Transaction.listing_id.isnot(None))
        .all()
        if row.listing_id is not None
    ]
    sold_listing_ids = [
        row.listing_id
        for row in db.query(Transaction.listing_id)
        .filter(Transaction.seller_id == user_id, Transaction.listing_id.isnot(None))
        .all()
        if row.listing_id is not None
    ]
    interacted_listing_ids = list(dict.fromkeys(purchased_listing_ids + sold_listing_ids))

    preferred_tags: Counter[str] = Counter()
    preferred_types: Counter[str] = Counter()
    price_points: list[float] = []

    if interacted_listing_ids:
        tag_rows = (
            db.query(Tag.tag_name)
            .join(ListingTag, ListingTag.tag_id == Tag.tag_id)
            .filter(ListingTag.listing_id.in_(interacted_listing_ids))
            .all()
        )
        preferred_tags.update(
            _normalize_text(row.tag_name) for row in tag_rows if _normalize_text(row.tag_name)
        )

        listing_rows = (
            db.query(Listing.listing_type, Listing.price)
            .filter(Listing.listing_id.in_(interacted_listing_ids))
            .all()
        )
        for row in listing_rows:
            listing_type = _normalize_text(row.listing_type)
            if listing_type:
                preferred_types[listing_type] += 1
            if row.price is not None:
                price_points.append(float(row.price))

    campus = (
        db.query(UserProfile.campus)
        .filter(UserProfile.user_id == user_id)
        .scalar()
    )

    return {
        "preferred_tags": preferred_tags,
        "preferred_types": preferred_types,
        "interacted_listing_ids": set(interacted_listing_ids),
        "avg_price": (sum(price_points) / len(price_points)) if price_points else None,
        "campus": _normalize_text(campus),
    }


def _base_listing_query(db: Session):
    return (
        db.query(Listing, Account.username.label("seller_username"), UserProfile.campus.label("seller_campus"))
        .outerjoin(Account, Account.account_id == Listing.seller_id)
        .outerjoin(UserProfile, UserProfile.user_id == Listing.seller_id)
        .filter(Listing.status == "available")
    )


def build_listing_payloads(db: Session, listings: list[Listing]) -> list[dict[str, Any]]:
    listing_ids = [listing.listing_id for listing in listings]
    tags_map = _listing_tags_map(db, listing_ids)
    seller_ratings = _seller_rating_map(db)
    verified_sellers = _verified_seller_ids(db)

    payloads: list[dict[str, Any]] = []
    for listing in listings:
        payload = serialize_model(listing)
        payload["tags"] = tags_map.get(listing.listing_id, [])
        rating_data = seller_ratings.get(listing.seller_id or -1, {})
        payload["seller_average_rating"] = rating_data.get("average_rating")
        payload["seller_review_count"] = rating_data.get("review_count", 0)
        payload["seller_is_verified"] = bool(listing.seller_id in verified_sellers)
        payloads.append(payload)
    return payloads


def get_recommended_feed(
    db: Session,
    *,
    user_id: int | None,
    limit: int,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    base_rows = (
        _base_listing_query(db)
        .order_by(Listing.created_at.desc())
        .limit(max(limit * 4, 40))
        .all()
    )
    listings = [row[0] for row in base_rows]
    listing_ids = [listing.listing_id for listing in listings]
    tags_map = _listing_tags_map(db, listing_ids)
    seller_ratings = _seller_rating_map(db)
    verified_sellers = _verified_seller_ids(db)

    preference_profile: dict[str, Any] | None = None
    if user_id is not None:
        preference_profile = _user_preference_profile(db, user_id)

    normalized_filter_tags = {
        _normalize_text(tag_value) for tag_value in (tags or []) if _normalize_text(tag_value)
    }
    scored_items: list[dict[str, Any]] = []
    for listing, seller_username, seller_campus in base_rows:
        score = 0.0
        reasons: list[str] = []
        listing_tags = [_normalize_text(tag) for tag in tags_map.get(listing.listing_id, [])]

        if normalized_filter_tags and not normalized_filter_tags.intersection(listing_tags):
            continue

        score += 2.0
        reasons.append("available")

        if listing.created_at is not None:
            score += 1.5
            reasons.append("recent")

        rating_data = seller_ratings.get(listing.seller_id or -1, {})
        average_rating = float(rating_data.get("average_rating") or 0)
        review_count = int(rating_data.get("review_count") or 0)
        if average_rating:
            score += min(average_rating, 5.0) * 0.5
            reasons.append("seller_rating")
        if review_count:
            score += min(review_count, 10) * 0.1

        if listing.seller_id in verified_sellers:
            score += 1.0
            reasons.append("verified_seller")

        if preference_profile:
            if listing.listing_id in preference_profile["interacted_listing_ids"]:
                continue
            if listing.seller_id == user_id:
                continue

            type_key = _normalize_text(listing.listing_type)
            if type_key and preference_profile["preferred_types"].get(type_key):
                score += 2.0 + 0.4 * preference_profile["preferred_types"][type_key]
                reasons.append("preferred_type")

            matching_tags = [
                tag
                for tag in listing_tags
                if preference_profile["preferred_tags"].get(tag)
            ]
            if matching_tags:
                score += 1.2 * sum(preference_profile["preferred_tags"][tag] for tag in matching_tags)
                reasons.append("matching_tags")

            avg_price = preference_profile.get("avg_price")
            if avg_price is not None and listing.price is not None:
                price_gap = abs(float(listing.price) - avg_price)
                if price_gap <= max(avg_price * 0.25, 50):
                    score += 1.0
                    reasons.append("price_match")

            preferred_campus = preference_profile.get("campus")
            if preferred_campus and _normalize_text(seller_campus) == preferred_campus:
                score += 0.8
                reasons.append("same_campus")

        if normalized_filter_tags:
            score += 2.5
            reasons.append("tag_filtered")

        scored_items.append(
            {
                "listing": listing,
                "seller_username": seller_username,
                "seller_campus": seller_campus,
                "tags": tags_map.get(listing.listing_id, []),
                "score": round(score, 3),
                "reasons": reasons,
            }
        )

    scored_items.sort(key=lambda item: (item["score"], item["listing"].created_at), reverse=True)
    feed_items = []
    for item in scored_items[:limit]:
        payload = serialize_model(item["listing"])
        payload["seller_username"] = item["seller_username"]
        payload["seller_campus"] = item["seller_campus"]
        payload["tags"] = item["tags"]
        payload["recommendation_score"] = item["score"]
        payload["recommendation_reasons"] = item["reasons"]
        payload["seller_is_verified"] = bool(item["listing"].seller_id in verified_sellers)
        rating_data = seller_ratings.get(item["listing"].seller_id or -1, {})
        payload["seller_average_rating"] = rating_data.get("average_rating")
        payload["seller_review_count"] = rating_data.get("review_count", 0)
        feed_items.append(payload)

    return {
        "user_id": user_id,
        "personalized": preference_profile is not None,
        "tags": sorted(normalized_filter_tags),
        "count": len(feed_items),
        "items": feed_items,
    }


def search_listings(
    db: Session,
    *,
    query_text: str | None,
    listing_type: str | None,
    min_price: Decimal | None,
    max_price: Decimal | None,
    tag: str | None,
    seller_id: int | None,
    limit: int,
) -> dict[str, Any]:
    query = _base_listing_query(db)

    normalized_query = _normalize_text(query_text)
    normalized_tag = _normalize_text(tag)
    if listing_type:
        query = query.filter(Listing.listing_type == listing_type)
    if seller_id is not None:
        query = query.filter(Listing.seller_id == seller_id)
    if min_price is not None:
        query = query.filter(Listing.price >= min_price)
    if max_price is not None:
        query = query.filter(Listing.price <= max_price)
    if normalized_query:
        like_value = f"%{normalized_query}%"
        query = (
            query.outerjoin(ListingTag, ListingTag.listing_id == Listing.listing_id)
            .outerjoin(Tag, Tag.tag_id == ListingTag.tag_id)
            .filter(
                or_(
                    func.lower(Listing.title).like(like_value),
                    func.lower(Listing.description).like(like_value),
                    func.lower(Tag.tag_name).like(like_value),
                )
            )
        )
    if normalized_tag:
        query = (
            query.join(ListingTag, ListingTag.listing_id == Listing.listing_id)
            .join(Tag, Tag.tag_id == ListingTag.tag_id)
            .filter(func.lower(Tag.tag_name) == normalized_tag)
        )

    rows = query.distinct(Listing.listing_id).order_by(Listing.created_at.desc()).limit(limit * 3).all()
    listings = [row[0] for row in rows]
    listing_ids = [listing.listing_id for listing in listings]
    tags_map = _listing_tags_map(db, listing_ids)
    seller_ratings = _seller_rating_map(db)
    verified_sellers = _verified_seller_ids(db)

    scored_results: list[dict[str, Any]] = []
    for listing, seller_username, seller_campus in rows:
        score = 0.0
        reasons: list[str] = []
        title_text = _normalize_text(listing.title)
        description_text = _normalize_text(listing.description)
        tag_values = [_normalize_text(value) for value in tags_map.get(listing.listing_id, [])]

        if normalized_query:
            if normalized_query == title_text:
                score += 8.0
                reasons.append("exact_title_match")
            elif normalized_query in title_text:
                score += 5.0
                reasons.append("title_match")
            if normalized_query and normalized_query in description_text:
                score += 2.0
                reasons.append("description_match")
            if normalized_query and any(normalized_query in value for value in tag_values):
                score += 3.0
                reasons.append("tag_match")
        else:
            score += 1.0

        if normalized_tag and normalized_tag in tag_values:
            score += 4.0
            reasons.append("filtered_tag")

        rating_data = seller_ratings.get(listing.seller_id or -1, {})
        average_rating = float(rating_data.get("average_rating") or 0)
        if average_rating:
            score += min(average_rating, 5.0) * 0.25

        if listing.seller_id in verified_sellers:
            score += 0.75

        if listing.created_at is not None:
            score += 0.5

        payload = serialize_model(listing)
        payload["seller_username"] = seller_username
        payload["seller_campus"] = seller_campus
        payload["tags"] = tags_map.get(listing.listing_id, [])
        payload["search_score"] = round(score, 3)
        payload["search_reasons"] = reasons
        payload["seller_is_verified"] = bool(listing.seller_id in verified_sellers)
        payload["seller_average_rating"] = rating_data.get("average_rating")
        payload["seller_review_count"] = rating_data.get("review_count", 0)
        scored_results.append(payload)

    scored_results.sort(key=lambda item: (item["search_score"], item["created_at"]), reverse=True)
    return {
        "query": query_text or "",
        "count": min(len(scored_results), limit),
        "items": scored_results[:limit],
    }
