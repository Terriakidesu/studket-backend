from fastapi import APIRouter

from . import (
    accounts,
    auth,
    conversations,
    listing_inventory,
    listing_media,
    listing_tags,
    listings,
    management_accounts,
    messages,
    notifications,
    reviews,
    tags,
    transaction_qr,
    transactions,
    user_profiles,
)

router = APIRouter(prefix="/v1")

router.include_router(auth.router)
router.include_router(accounts.router)
router.include_router(user_profiles.router)
router.include_router(management_accounts.router)
router.include_router(listings.router)
router.include_router(listing_inventory.router)
router.include_router(listing_media.router)
router.include_router(tags.router)
router.include_router(listing_tags.router)
router.include_router(conversations.router)
router.include_router(messages.router)
router.include_router(transactions.router)
router.include_router(reviews.router)
router.include_router(transaction_qr.router)
router.include_router(notifications.router)
