"""make subject_ref nullable

Revision ID: 1f53d4c82b1a
Revises: 886c9832cab5
Create Date: 2026-09-14 17:21:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f53d4c82b1a"
down_revision: str | Sequence[str] | None = "886c9832cab5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column(
        "audit_event",
        "subject_ref",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        "audit_event",
        "subject_ref",
        existing_type=sa.String(),
        nullable=False,
    )
