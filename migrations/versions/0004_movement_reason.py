"""Why a manual correction was made, and who made it.

A movement recorded by a portal explains itself: a reader saw the tag.
A movement typed in by a person does not, so a correction carries a
reason and the name of whoever made it. Both are NULL for the portal
movements that make up almost every row.

SPEC.md section 3 has been updated to match.

Revision ID: 0004_movement_reason
Revises: 0003_gate_events
Create Date: 2026-09-04
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004_movement_reason"
down_revision: Union[str, None] = "0003_gate_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE movements ADD COLUMN reason TEXT")
    op.execute("ALTER TABLE movements ADD COLUMN operator TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE movements DROP COLUMN IF EXISTS operator")
    op.execute("ALTER TABLE movements DROP COLUMN IF EXISTS reason")
