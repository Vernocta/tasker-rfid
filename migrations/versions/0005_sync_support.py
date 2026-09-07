"""What the sync worker needs to find changed rows.

NOT part of SPEC.md section 3 as originally written; SPEC.md has been
updated to match.

Two additions:

1. `updated_at` on every table whose rows can change after they are
   written, kept current by a trigger. Without it there is no way to ask
   "what has changed since last time" for a mutable row: a container's
   status changes long after its id was assigned, so an id high-water
   mark would never see it again.

   The append-only tables — reads_raw, gate_events, movements — do not
   get one. Their rows are written once and never touched, so their
   BIGSERIAL id is already a perfect high-water mark, and a per-row
   trigger on reads_raw at 900 reads/sec would be a cost for nothing.

2. `sync_cursor`, which records how far the cloud replica has been
   brought up to date. It lives in the CLOUD copy of this schema, not the
   local one, and is written in the same transaction as the rows it
   covers so the two can never disagree. Migrations create it in both
   copies because they are the same schema; locally it simply stays
   empty.

Revision ID: 0005_sync_support
Revises: 0004_movement_reason
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005_sync_support"
down_revision: Union[str, None] = "0004_movement_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables whose rows change after they are written.
MUTABLE_TABLES = [
    "skus",
    "customers",
    "containers",
    "container_contents",
    "observations",
    "dispatch_sessions",
    "anomalies",
    "cycle_counts",
    "cycle_count_items",
]

TOUCH_FUNCTION = """
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

SYNC_CURSOR = """
CREATE TABLE sync_cursor (
  table_name      TEXT PRIMARY KEY,
  last_id         BIGINT NOT NULL DEFAULT 0,
  -- The epoch, not '-infinity': a real timestamp converts cleanly to a
  -- date in every client. Nothing in this system can predate 1970, so it
  -- is just as safe a lower bound.
  last_updated_at TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01T00:00:00Z',
  rows_synced     BIGINT NOT NULL DEFAULT 0,
  synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def upgrade() -> None:
    op.execute(TOUCH_FUNCTION)
    for table in MUTABLE_TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        )
        # The sync worker asks each table for rows changed since a moment.
        op.execute(f"CREATE INDEX ON {table} (updated_at)")
        op.execute(
            f"CREATE TRIGGER {table}_touch_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION touch_updated_at()"
        )
    op.execute(SYNC_CURSOR)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sync_cursor")
    for table in MUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_touch_updated_at ON {table}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS updated_at")
    op.execute("DROP FUNCTION IF EXISTS touch_updated_at()")
