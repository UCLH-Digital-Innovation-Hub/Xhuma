import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def database_url() -> str:
    # Prefer a single URL if you want (good for Alembic too)
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    db = os.getenv("POSTGRES_DB", "xhuma")

    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def make_engine() -> AsyncEngine:
    return create_async_engine(database_url(), pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> sessionmaker:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
