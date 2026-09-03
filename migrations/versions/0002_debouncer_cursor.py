"""Where the debouncer has read up to in reads_raw.

NOTE: this table is NOT in SPEC.md section 3. It is added because layer 2
needs to remember which raw reads it has already collapsed, and there is
nowhere in the spec's schema to keep that. `observations` has a
`processed` flag for layer 4 to use, but `reads_raw` deliberately has no
such column — it is append-only and never updated (SPEC.md section 3), so
the position has to live somewhere else.

It holds one row. Nothing else references it, and deleting it only causes
the debouncer to re-read reads_raw from the beginning.

Revision ID: 0002_debouncer_cursor
Revises: 0001_initial_schema
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_debouncer_cursor"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CURSOR_TABLE = """
CREATE TABLE debouncer_cursor (
  name          TEXT PRIMARY KEY,
  last_read_id  BIGINT NOT NULL DEFAULT 0,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

SEED_ROW = """
INSERT INTO debouncer_cursor (name, last_read_id) VALUES ('debouncer', 0)
"""


def upgrade() -> None:
    op.execute(CURSOR_TABLE)
    op.execute(SEED_ROW)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS debouncer_cursor")
