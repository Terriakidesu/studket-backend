from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.common import serialize_model, _validate_numeric_payload
from app.db.models import Account, Listing, Review, Transaction, UserProfile
from app.db.session import get_db

router = APIRouter(prefix="/reviews", tags=["reviews"])


class CreateReviewPayload(BaseModel):
    transaction_id: int
    reviewer_id: int
    rating: int
    comment: str | None = None

class DirectReviewPayload(BaseModel):
    reviewer_id: int
    rating: int
    comment: str | None = None



def _get_user_account_or_404(account_id: int, db: Session) -> Account:
    account = (
        db.query(Account)
        .filter(Account.account_id == account_id, Account.account_type == "user")
        .first()
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "User account not found"},
        )
    profile = db.query(UserProfile).filter(UserProfile.user_id == account_id).first()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "User profile not found"},
        )
    return account


def _get_review_or_404(review_id: int, db: Session) -> Review:
    review = db.query(Review).filter(Review.review_id == review_id).first()
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Review not found"},
        )
    return review


def _get_transaction_or_404(transaction_id: int, db: Session) -> Transaction:
    transaction = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transaction_id)
        .first()
    )
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Transaction not found"},
        )
    return transaction


@router.get("/")
def list_reviews(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    reviews = db.query(Review).all()
    return jsonable_encoder([serialize_model(review) for review in reviews])


@router.get("/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    review = _get_review_or_404(review_id, db)
    return jsonable_encoder(serialize_model(review))


@router.get("/transactions/{transaction_id}")
def get_reviews_for_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    transaction = _get_transaction_or_404(transaction_id, db)
    listing = (
        db.query(Listing)
        .filter(Listing.listing_id == transaction.listing_id)
        .first()
    )
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Listing not found"},
        )
    if listing.listing_type == "looking_for":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Reviews for looking-for transactions are not supported"},
        )

    reviews = (
        db.query(Review)
        .filter(Review.transaction_id == transaction_id)
        .all()
    )
    return jsonable_encoder(
        {
            "transaction_id": transaction_id,
            "count": len(reviews),
            "items": [serialize_model(review) for review in reviews],
        }
    )


@router.get("/users/{account_id}")
def get_reviews_for_user(
    account_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _get_user_account_or_404(account_id, db)
    reviews = (
        db.query(Review)
        .filter(Review.reviewee_id == account_id)
        .all()
    )
    return jsonable_encoder(
        {
            "account_id": account_id,
            "count": len(reviews),
            "items": [serialize_model(review) for review in reviews],
        }
    )


@router.post("/users/{seller_id}", status_code=status.HTTP_201_CREATED)
def create_seller_review(
    seller_id: int,
    payload: CreateReviewPayload = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.transaction_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "transaction_id is required"},
        )
    _get_user_account_or_404(payload.reviewer_id, db)
    _get_user_account_or_404(seller_id, db)
    transaction = _get_transaction_or_404(payload.transaction_id, db)

    if transaction.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Transaction does not belong to this seller"},
        )
    if payload.reviewer_id != transaction.buyer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Only the buyer can review the seller"},
        )
    if transaction.transaction_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Only completed transactions can be reviewed"},
        )

    existing = (
        db.query(Review)
        .filter(Review.transaction_id == payload.transaction_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "A review already exists for this transaction"},
        )

    if not 1 <= payload.rating <= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "rating must be between 1 and 5"},
        )

    review = Review(
        transaction_id=payload.transaction_id,
        reviewer_id=payload.reviewer_id,
        reviewee_id=seller_id,
        rating=payload.rating,
        comment=(payload.comment or "").strip() or None,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return jsonable_encoder(serialize_model(review))


@router.post("/users/{seller_id}/direct", status_code=status.HTTP_201_CREATED)
def create_seller_review_direct(
    seller_id: int,
    payload: DirectReviewPayload = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _get_user_account_or_404(payload.reviewer_id, db)
    _get_user_account_or_404(seller_id, db)

    completed_txn = (
        db.query(Transaction)
        .filter(
            Transaction.buyer_id == payload.reviewer_id,
            Transaction.seller_id == seller_id,
            Transaction.transaction_status == "completed",
        )
        .order_by(Transaction.completed_at.desc(), Transaction.transaction_id.desc())
        .first()
    )
    if completed_txn is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Reviewer must have a completed transaction with this seller"},
        )

    existing = (
        db.query(Review)
        .filter(Review.transaction_id == completed_txn.transaction_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "A review already exists for the latest completed transaction"},
        )

    if not 1 <= payload.rating <= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "rating must be between 1 and 5"},
        )

    review = Review(
        transaction_id=completed_txn.transaction_id,
        reviewer_id=payload.reviewer_id,
        reviewee_id=seller_id,
        rating=payload.rating,
        comment=(payload.comment or "").strip() or None,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return jsonable_encoder(serialize_model(review))


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_review(
    payload: CreateReviewPayload = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _get_user_account_or_404(payload.reviewer_id, db)
    transaction = _get_transaction_or_404(payload.transaction_id, db)

    if payload.reviewer_id not in (transaction.buyer_id, transaction.seller_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Only transaction participants can leave a review"},
        )
    if transaction.transaction_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Only completed transactions can be reviewed"},
        )

    existing = (
        db.query(Review)
        .filter(Review.transaction_id == payload.transaction_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "A review already exists for this transaction"},
        )

    reviewee_id = (
        transaction.seller_id
        if payload.reviewer_id == transaction.buyer_id
        else transaction.buyer_id
    )
    if reviewee_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Reviewee could not be determined"},
        )
    if reviewee_id == transaction.buyer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Buyers cannot be reviewed"},
        )

    if not 1 <= payload.rating <= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "rating must be between 1 and 5"},
        )

    review = Review(
        transaction_id=payload.transaction_id,
        reviewer_id=payload.reviewer_id,
        reviewee_id=reviewee_id,
        rating=payload.rating,
        comment=(payload.comment or "").strip() or None,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return jsonable_encoder(serialize_model(review))


@router.patch("/{review_id}")
def update_review(
    review_id: int,
    account_id: int,
    payload: dict[str, object] = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _get_user_account_or_404(account_id, db)
    review = _get_review_or_404(review_id, db)
    if review.reviewer_id != account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Only the review author can update this review"},
        )

    _validate_numeric_payload(Review, payload)
    restricted_fields = {"review_id", "transaction_id", "reviewer_id", "reviewee_id", "created_at"}
    for field, value in payload.items():
        if field in restricted_fields:
            continue
        if hasattr(review, field):
            setattr(review, field, value)
    db.commit()
    db.refresh(review)
    return jsonable_encoder(serialize_model(review))


@router.delete("/{review_id}")
def delete_review(review_id: int) -> Response:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail={"error": "Review deletion is not allowed"},
    )
