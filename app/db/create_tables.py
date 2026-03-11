from secrets import token_urlsafe

from sqlalchemy import inspect, text

from .base import Base
from . import models  # noqa: F401
from .session import engine


def _ensure_account_report_columns() -> None:
    inspector = inspect(engine)
    if "account" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("account")}
    statements: list[str] = []

    if "warning_count" not in existing_columns:
        statements.append(
            "ALTER TABLE account ADD COLUMN warning_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_warned_at" not in existing_columns:
        statements.append("ALTER TABLE account ADD COLUMN last_warned_at TIMESTAMP")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_user_profile_seller_column() -> None:
    inspector = inspect(engine)
    if "user_profile" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("user_profile")}
    statements: list[str] = []

    if "is_seller" not in existing_columns:
        statements.append(
            "ALTER TABLE user_profile ADD COLUMN is_seller BOOLEAN NOT NULL DEFAULT FALSE"
        )

    # Preserve seller access for existing accounts that already have normal listings.
    statements.append(
        """
        UPDATE user_profile
        SET is_seller = TRUE
        WHERE user_id IN (
            SELECT DISTINCT seller_id
            FROM listing
            WHERE seller_id IS NOT NULL
              AND (listing_type IS NULL OR listing_type <> 'looking_for')
        )
        """
    )

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_management_profile_photo_column() -> None:
    inspector = inspect(engine)
    if "management_account" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("management_account")}
    if "profile_photo" in existing_columns:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE management_account ADD COLUMN profile_photo TEXT"))


def _generate_share_token() -> str:
    return token_urlsafe(9).replace("-", "").replace("_", "")


def _ensure_listing_share_token_column() -> None:
    inspector = inspect(engine)
    if "listing" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("listing")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("listing")}

    with engine.begin() as connection:
        if "share_token" not in existing_columns:
            connection.execute(text("ALTER TABLE listing ADD COLUMN share_token TEXT"))

        listing_ids = [
            row[0]
            for row in connection.execute(
                text("SELECT listing_id FROM listing WHERE share_token IS NULL OR share_token = ''")
            ).fetchall()
        ]
        for listing_id in listing_ids:
            token = _generate_share_token()
            while connection.execute(
                text("SELECT 1 FROM listing WHERE share_token = :token"),
                {"token": token},
            ).first():
                token = _generate_share_token()
            connection.execute(
                text("UPDATE listing SET share_token = :token WHERE listing_id = :listing_id"),
                {"token": token, "listing_id": listing_id},
            )

        if "uq_listing_share_token" not in existing_indexes:
            connection.execute(
                text("CREATE UNIQUE INDEX uq_listing_share_token ON listing (share_token)")
            )


def create_tables():
    Base.metadata.create_all(bind=engine)
    _ensure_account_report_columns()
    _ensure_user_profile_seller_column()
    _ensure_management_profile_photo_column()
    _ensure_listing_share_token_column()


if __name__ == "__main__":
    create_tables()
