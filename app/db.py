# app/db.py
import os

import asyncpg


def pg_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "postgres")
    db = os.getenv("POSTGRES_DB", "xhuma")
    user = os.getenv("POSTGRES_USER", "postgres")
    pwd = os.getenv("POSTGRES_PASSWORD", "postgres")
    return f"postgresql://{user}:{pwd}@{host}:5432/{db}"


async def open_pool():
    return await asyncpg.create_pool(dsn=pg_dsn(), min_size=1, max_size=10)
