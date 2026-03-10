from .base import Base
from . import models  # noqa: F401
from .session import engine


def create_tables():
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    create_tables()
