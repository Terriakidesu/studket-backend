import os

from dotenv import load_dotenv

load_dotenv()


def build_database_url() -> str:
    host = os.getenv("DB_URL")
    database = os.getenv("DB_NAME")
    username = os.getenv("DB_USERNAME")
    password = os.getenv("DB_PASSWORD")

    missing = [
        key
        for key, value in {
            "DB_URL": host,
            "DB_NAME": database,
            "DB_USERNAME": username,
            "DB_PASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required database environment variables: {', '.join(missing)}"
        )

    return f"postgresql+psycopg://{username}:{password}@{host}/{database}"


DATABASE_URL = build_database_url()
