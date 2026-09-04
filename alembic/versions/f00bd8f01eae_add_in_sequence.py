"""add in sequence

Revision ID: f00bd8f01eae
Revises: bcc2e8eaade2
Create Date: 2026-02-10 10:25:52.926073

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f00bd8f01eae"
down_revision: str | Sequence[str] | None = "bcc2e8eaade2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS audit_event_seq;")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS audit_event_seq;")
