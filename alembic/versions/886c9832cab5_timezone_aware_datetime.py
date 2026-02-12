"""timezone aware datetime

Revision ID: 886c9832cab5
Revises: f00bd8f01eae
Create Date: 2026-02-10 11:03:11.510022

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "886c9832cab5"
down_revision: Union[str, Sequence[str], None] = "f00bd8f01eae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.alter_column(
        "audit_event",
        "event_time",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(timezone=False),
        postgresql_using="event_time AT TIME ZONE 'UTC'",
    )


def downgrade():
    op.alter_column(
        "audit_event",
        "event_time",
        type_=sa.DateTime(timezone=False),
        existing_type=sa.DateTime(timezone=True),
        postgresql_using="event_time",
    )
