# app/db.py
import asyncio
import os

import asyncpg


def pg_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    db = os.getenv("POSTGRES_DB", "xhuma")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def open_pool():
    dsn = pg_dsn()

    last_exc = None
    for attempt in range(1, 31):  # ~30 attempts
        try:
            return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
        except Exception as e:
            last_exc = e
            await asyncio.sleep(min(0.5 * attempt, 3.0))  # backoff up to 3s

    raise RuntimeError(
        f"Failed to connect to Postgres after retries (dsn={dsn!r})"
    ) from last_exc
