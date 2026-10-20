from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SEQUENCE_SQL = text("SELECT nextval('audit_event_seq') AS seq;")


async def next_audit_sequence(session: AsyncSession) -> int:
    row = (await session.execute(SEQUENCE_SQL)).mappings().one()
    return int(row["seq"])
