"""Beam-break events from the IR gate at the exit portal.

NOTE: this table is NOT in SPEC.md section 3. SPEC.md section 4 layer 3
requires IR beam gating to resolve direction at the exit, but the spec
does not say where the beam events themselves are kept. They need to be
durable and ordered against reads_raw, so ingest writes them here and the
debouncer reads them, the same shape as the read pipeline.

Append-only, like reads_raw. Never updated.

Revision ID: 0003_gate_events
Revises: 0002_debouncer_cursor
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_gate_events"
down_revision: Union[str, None] = "0002_debouncer_cursor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GATE_EVENTS = """
CREATE TABLE gate_events (
  id          BIGSERIAL PRIMARY KEY,
  gate_id     TEXT NOT NULL,          -- e.g. 'GATE-EXIT'
  beam        TEXT NOT NULL,          -- 'INNER' (warehouse side), 'OUTER' (street side)
  state       TEXT NOT NULL,          -- 'BROKEN','CLEAR'
  occurred_at TIMESTAMPTZ NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

INDEXES = [
    "CREATE INDEX ON gate_events (gate_id, occurred_at)",
    "CREATE INDEX ON gate_events (occurred_at)",
]


def upgrade() -> None:
    op.execute(GATE_EVENTS)
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gate_events")
