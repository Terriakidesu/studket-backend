from fastapi import APIRouter, Depends

from . import (
    accounts,
    auth,
    conversation_reports,
    conversations,
    listing_inventory,
    listing_media,
    listing_reports,
    listing_tags,
    listings,
    looking_for_reports,
    management_accounts,
    messages,
    notifications,
    reviews,
    seller_reports,
    tags,
    transaction_qr,
    transactions,
    user_profiles,
)
from .dependencies import require_dashboard_api_session

router = APIRouter(prefix="/v1")

router.include_router(auth.router)

# User-facing API routers
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
router.include_router(seller_reports.router)

# Dashboard/staff-only API routers
router.include_router(accounts.router, dependencies=[Depends(require_dashboard_api_session)])
router.include_router(user_profiles.router, dependencies=[Depends(require_dashboard_api_session)])
router.include_router(management_accounts.router, dependencies=[Depends(require_dashboard_api_session)])
router.include_router(listing_reports.router, dependencies=[Depends(require_dashboard_api_session)])
router.include_router(looking_for_reports.router, dependencies=[Depends(require_dashboard_api_session)])
router.include_router(conversation_reports.router, dependencies=[Depends(require_dashboard_api_session)])
