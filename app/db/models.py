from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String, Text, TIMESTAMP
from sqlalchemy.sql import func

from .base import Base


# =========================
# ACCOUNT
# =========================

class Account(Base):
    __tablename__ = "account"

    account_id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)

    account_type = Column(String, nullable=False)
    account_status = Column(String, default="active")
    warning_count = Column(Integer, default=0, nullable=False)
    last_warned_at = Column(TIMESTAMP)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# USER PROFILE
# =========================

class UserProfile(Base):
    __tablename__ = "user_profile"

    user_id = Column(Integer, ForeignKey("account.account_id", ondelete="CASCADE"), primary_key=True)

    first_name = Column(String)
    last_name = Column(String)
    campus = Column(String)

    profile_photo = Column(Text)
    is_seller = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# MANAGEMENT ACCOUNT
# =========================

class ManagementAccount(Base):
    __tablename__ = "management_account"

    manager_id = Column(Integer, ForeignKey("account.account_id", ondelete="CASCADE"), primary_key=True)

    first_name = Column(String)
    last_name = Column(String)

    role_name = Column(String)
    profile_photo = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# LISTING
# =========================

class Listing(Base):
    __tablename__ = "listing"

    listing_id = Column(Integer, primary_key=True)

    seller_id = Column(Integer, ForeignKey("user_profile.user_id"))

    title = Column(String, nullable=False)
    description = Column(Text)

    price = Column(Numeric(10, 2))

    listing_type = Column(String)  # single_item | stock_item
    condition = Column(String)

    status = Column(String, default="available")

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# INVENTORY
# =========================

class ListingInventory(Base):
    __tablename__ = "listing_inventory"

    inventory_id = Column(Integer, primary_key=True)

    listing_id = Column(Integer, ForeignKey("listing.listing_id", ondelete="CASCADE"), unique=True)

    quantity_available = Column(Integer)
    max_daily_limit = Column(Integer)

    restockable = Column(Boolean, default=True)


# =========================
# LISTING MEDIA
# =========================

class ListingMedia(Base):
    __tablename__ = "listing_media"

    media_id = Column(Integer, primary_key=True)

    listing_id = Column(Integer, ForeignKey("listing.listing_id", ondelete="CASCADE"))

    file_path = Column(Text)
    sort_order = Column(Integer, default=0)


# =========================
# TAG
# =========================

class Tag(Base):
    __tablename__ = "tag"

    tag_id = Column(Integer, primary_key=True)
    tag_name = Column(String, unique=True)


class ListingTag(Base):
    __tablename__ = "listing_tags"

    listing_id = Column(Integer, ForeignKey("listing.listing_id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tag.tag_id", ondelete="CASCADE"), primary_key=True)


# =========================
# CONVERSATION
# =========================

class Conversation(Base):
    __tablename__ = "conversation"

    conversation_id = Column(Integer, primary_key=True)

    participant1_id = Column(Integer, ForeignKey("account.account_id"))
    participant2_id = Column(Integer, ForeignKey("account.account_id"))

    conversation_type = Column(String)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# MESSAGE
# =========================

class Message(Base):
    __tablename__ = "message"

    message_id = Column(Integer, primary_key=True)

    conversation_id = Column(Integer, ForeignKey("conversation.conversation_id", ondelete="CASCADE"))

    sender_id = Column(Integer, ForeignKey("account.account_id"))

    message_text = Column(Text)

    sent_at = Column(TIMESTAMP, server_default=func.now())

    is_read = Column(Boolean, default=False)


# =========================
# TRANSACTION
# =========================

class Transaction(Base):
    __tablename__ = "transaction"

    transaction_id = Column(Integer, primary_key=True)

    listing_id = Column(Integer, ForeignKey("listing.listing_id"))

    buyer_id = Column(Integer, ForeignKey("user_profile.user_id"))
    seller_id = Column(Integer, ForeignKey("user_profile.user_id"))

    quantity = Column(Integer, default=1)

    agreed_price = Column(Numeric(10, 2))

    transaction_status = Column(String)

    completed_at = Column(TIMESTAMP)


# =========================
# REVIEW
# =========================

class Review(Base):
    __tablename__ = "review"

    review_id = Column(Integer, primary_key=True)

    transaction_id = Column(Integer, ForeignKey("transaction.transaction_id"), unique=True)

    reviewer_id = Column(Integer, ForeignKey("user_profile.user_id"))
    reviewee_id = Column(Integer, ForeignKey("user_profile.user_id"))

    rating = Column(Integer)
    comment = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# QR TRANSACTION
# =========================

class TransactionQR(Base):
    __tablename__ = "transaction_qr"

    transaction_qr_id = Column(Integer, primary_key=True)

    transaction_id = Column(Integer, ForeignKey("transaction.transaction_id", ondelete="CASCADE"))

    qr_token = Column(String, unique=True)

    expires_at = Column(TIMESTAMP)

    is_used = Column(Boolean, default=False)

    generated_by = Column(Integer, ForeignKey("user_profile.user_id"))
    scanned_by = Column(Integer, ForeignKey("user_profile.user_id"))

    scanned_at = Column(TIMESTAMP)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# NOTIFICATIONS
# =========================

class Notification(Base):
    __tablename__ = "notification"

    notification_id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("user_profile.user_id", ondelete="CASCADE"))

    notification_type = Column(String)

    title = Column(String)
    body = Column(Text)

    related_entity_type = Column(String)
    related_entity_id = Column(Integer)

    is_read = Column(Boolean, default=False)

    read_at = Column(TIMESTAMP)

    created_at = Column(TIMESTAMP, server_default=func.now())


# =========================
# SELLER VERIFICATION REQUEST
# =========================

class SellerVerificationRequest(Base):
    __tablename__ = "seller_verification_request"

    request_id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("user_profile.user_id", ondelete="CASCADE"), nullable=False)
    status = Column(String, default="pending", nullable=False)
    submission_note = Column(Text)

    reviewed_by = Column(Integer, ForeignKey("account.account_id"))
    review_note = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())
    reviewed_at = Column(TIMESTAMP)


# =========================
# LISTING REPORT
# =========================

class ListingReport(Base):
    __tablename__ = "listing_report"

    report_id = Column(Integer, primary_key=True)

    listing_id = Column(Integer, ForeignKey("listing.listing_id", ondelete="CASCADE"), nullable=False)
    reporter_id = Column(Integer, ForeignKey("user_profile.user_id", ondelete="CASCADE"), nullable=False)

    reason = Column(String, nullable=False)
    details = Column(Text)
    status = Column(String, default="open", nullable=False)

    reviewed_by = Column(Integer, ForeignKey("account.account_id"))
    resolution_note = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())
    reviewed_at = Column(TIMESTAMP)


# =========================
# LOOKING FOR REPORT
# =========================

class LookingForReport(Base):
    __tablename__ = "looking_for_report"

    report_id = Column(Integer, primary_key=True)

    listing_id = Column(Integer, ForeignKey("listing.listing_id", ondelete="CASCADE"), nullable=False)
    reporter_id = Column(Integer, ForeignKey("user_profile.user_id", ondelete="CASCADE"), nullable=False)

    reason = Column(String, nullable=False)
    details = Column(Text)
    status = Column(String, default="open", nullable=False)

    reviewed_by = Column(Integer, ForeignKey("account.account_id"))
    resolution_note = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())
    reviewed_at = Column(TIMESTAMP)


# =========================
# CONVERSATION REPORT
# =========================

class ConversationReport(Base):
    __tablename__ = "conversation_report"

    report_id = Column(Integer, primary_key=True)

    conversation_id = Column(Integer, ForeignKey("conversation.conversation_id", ondelete="CASCADE"), nullable=False)
    reporter_id = Column(Integer, ForeignKey("user_profile.user_id", ondelete="CASCADE"), nullable=False)
    reported_account_id = Column(Integer, ForeignKey("account.account_id"))

    reason = Column(String, nullable=False)
    details = Column(Text)
    status = Column(String, default="open", nullable=False)

    reviewed_by = Column(Integer, ForeignKey("account.account_id"))
    resolution_note = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())
    reviewed_at = Column(TIMESTAMP)


# =========================
# SELLER REPORT
# =========================

class SellerReport(Base):
    __tablename__ = "seller_report"

    report_id = Column(Integer, primary_key=True)

    seller_id = Column(Integer, ForeignKey("user_profile.user_id", ondelete="CASCADE"), nullable=False)
    reporter_id = Column(Integer, ForeignKey("user_profile.user_id", ondelete="CASCADE"), nullable=False)

    reason = Column(String, nullable=False)
    details = Column(Text)
    status = Column(String, default="open", nullable=False)

    reviewed_by = Column(Integer, ForeignKey("account.account_id"))
    resolution_note = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())
    reviewed_at = Column(TIMESTAMP)


# =========================
# APP SETTINGS
# =========================

class AppSetting(Base):
    __tablename__ = "app_setting"

    setting_key = Column(String, primary_key=True)
    setting_value = Column(String, nullable=False)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


# =========================
# AUDIT LOG
# =========================

class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_log_id = Column(Integer, primary_key=True)

    actor_account_id = Column(Integer, ForeignKey("account.account_id"))
    actor_username = Column(String)
    actor_role = Column(String)

    action = Column(String, nullable=False)
    target_type = Column(String)
    target_id = Column(String)
    target_label = Column(String)
    details = Column(Text)

    created_at = Column(TIMESTAMP, server_default=func.now())
