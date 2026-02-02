# app/audit/sequences.py
from __future__ import annotations

SEQUENCE_SQL = "SELECT nextval('audit_event_seq') AS seq;"


async def next_audit_sequence(pg_pool) -> int:
    """
    Monotonic sequence sourced from Postgres, independent of system time.
    Works across restarts and replicas.
    """
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(SEQUENCE_SQL)
        return int(row["seq"])
