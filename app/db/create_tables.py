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


def create_tables():
    Base.metadata.create_all(bind=engine)
    _ensure_account_report_columns()


if __name__ == "__main__":
    create_tables()
