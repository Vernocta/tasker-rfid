"""Initial schema — SPEC.md section 3.

The SQL below is copied from SPEC.md section 3 verbatim, one statement per
op.execute() call so that any failure names the exact statement that failed.
Do not "improve" it here; change SPEC.md first.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-31
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============ Catalogue ============

SKUS = """
CREATE TABLE skus (
  sku_id        TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  family        TEXT NOT NULL,   -- 'cones','cups','powder','bag_in_box','tetra','sauce','accessories'
  units_per_box INTEGER NOT NULL,
  tag_class     TEXT,            -- 'paper','paper_long','on_metal' (set after RF test)
  active        BOOLEAN NOT NULL DEFAULT TRUE
)
"""

CUSTOMERS = """
CREATE TABLE customers (
  customer_id   TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  active        BOOLEAN NOT NULL DEFAULT TRUE
)
"""

# ============ Physical objects ============

CONTAINERS = """
CREATE TABLE containers (
  container_id  BIGSERIAL PRIMARY KEY,
  tid           TEXT UNIQUE NOT NULL,        -- factory-locked chip serial
  epc           TEXT,                        -- informational only
  kind          TEXT NOT NULL,               -- 'BOX','PALLET','TOTE'
  parent_id     BIGINT REFERENCES containers(container_id),
  status        TEXT NOT NULL DEFAULT 'REGISTERED',
  reusable      BOOLEAN NOT NULL DEFAULT FALSE,  -- pallets/totes: TRUE
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ,
  last_portal   TEXT
)
"""

# Contents of a container. A box has one row. A pallet-level container
# may have several (mixed-SKU pallet). Empty for a pallet whose contents
# are derived from child boxes.
CONTAINER_CONTENTS = """
CREATE TABLE container_contents (
  id            BIGSERIAL PRIMARY KEY,
  container_id  BIGINT NOT NULL REFERENCES containers(container_id) ON DELETE CASCADE,
  sku_id        TEXT NOT NULL REFERENCES skus(sku_id),
  quantity      INTEGER NOT NULL,       -- number of boxes
  lot           TEXT,
  produced_at   DATE,
  expiry        DATE
)
"""

# ============ Read pipeline ============

# Append-only. NEVER delete or truncate. Replay source of truth.
READS_RAW = """
CREATE TABLE reads_raw (
  id          BIGSERIAL PRIMARY KEY,
  tid         TEXT NOT NULL,
  epc         TEXT,
  reader_id   TEXT NOT NULL,
  antenna_id  SMALLINT NOT NULL,
  rssi        SMALLINT,
  read_at     TIMESTAMPTZ NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Debounced and filtered
OBSERVATIONS = """
CREATE TABLE observations (
  id           BIGSERIAL PRIMARY KEY,
  tid          TEXT NOT NULL,
  portal       TEXT NOT NULL,        -- 'ENTRANCE','EXIT'
  direction    TEXT,                 -- 'IN','OUT','UNKNOWN'
  first_read   TIMESTAMPTZ NOT NULL,
  last_read    TIMESTAMPTZ NOT NULL,
  read_count   INTEGER NOT NULL,
  peak_rssi    SMALLINT,
  processed    BOOLEAN NOT NULL DEFAULT FALSE
)
"""

# ============ Business events ============

DISPATCH_SESSIONS = """
CREATE TABLE dispatch_sessions (
  session_id   BIGSERIAL PRIMARY KEY,
  customer_id  TEXT REFERENCES customers(customer_id),
  order_ref    TEXT,
  opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at    TIMESTAMPTZ,
  operator     TEXT
)
"""

MOVEMENTS = """
CREATE TABLE movements (
  id           BIGSERIAL PRIMARY KEY,
  container_id BIGINT NOT NULL REFERENCES containers(container_id),
  from_status  TEXT,
  to_status    TEXT NOT NULL,
  portal       TEXT,
  session_id   BIGINT REFERENCES dispatch_sessions(session_id),
  occurred_at  TIMESTAMPTZ NOT NULL,
  source       TEXT NOT NULL         -- 'PORTAL','HANDHELD','MANUAL'
)
"""

# Anomaly kinds:
#   UNKNOWN_TID          read from a tag with no container record
#   ILLEGAL_TRANSITION   e.g. DISPATCHED -> DISPATCHED
#   NO_DIRECTION         portal could not resolve direction
#   NO_SESSION           exit read with no open dispatch session
#   SHORT_PALLET         pallet read but child count < declared
#   COUNT_MISMATCH       cycle count variance
ANOMALIES = """
CREATE TABLE anomalies (
  id           BIGSERIAL PRIMARY KEY,
  tid          TEXT,
  container_id BIGINT REFERENCES containers(container_id),
  kind         TEXT NOT NULL,
  detail       JSONB,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved     BOOLEAN NOT NULL DEFAULT FALSE,
  resolved_by  TEXT,
  resolved_at  TIMESTAMPTZ
)
"""

# ============ Cycle counts ============

CYCLE_COUNTS = """
CREATE TABLE cycle_counts (
  id           BIGSERIAL PRIMARY KEY,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  operator     TEXT,
  notes        TEXT
)
"""

CYCLE_COUNT_ITEMS = """
CREATE TABLE cycle_count_items (
  cycle_id     BIGINT NOT NULL REFERENCES cycle_counts(id) ON DELETE CASCADE,
  tid          TEXT NOT NULL,
  found        BOOLEAN NOT NULL,
  PRIMARY KEY (cycle_id, tid)
)
"""

TABLES = [
    SKUS,
    CUSTOMERS,
    CONTAINERS,
    CONTAINER_CONTENTS,
    READS_RAW,
    OBSERVATIONS,
    DISPATCH_SESSIONS,
    MOVEMENTS,
    ANOMALIES,
    CYCLE_COUNTS,
    CYCLE_COUNT_ITEMS,
]

INDEXES = [
    "CREATE INDEX ON containers (status)",
    "CREATE INDEX ON containers (parent_id)",
    "CREATE INDEX ON containers (kind, status)",
    "CREATE INDEX ON container_contents (container_id)",
    "CREATE INDEX ON container_contents (sku_id)",
    "CREATE INDEX ON reads_raw (tid, read_at)",
    "CREATE INDEX ON reads_raw (read_at)",
    "CREATE INDEX ON observations (processed) WHERE processed = FALSE",
    "CREATE INDEX ON movements (container_id, occurred_at)",
    "CREATE INDEX ON movements (session_id)",
    "CREATE INDEX ON anomalies (resolved) WHERE resolved = FALSE",
]

# Reverse order of creation, so foreign keys never block a drop.
DROP_ORDER = [
    "cycle_count_items",
    "cycle_counts",
    "anomalies",
    "movements",
    "dispatch_sessions",
    "observations",
    "reads_raw",
    "container_contents",
    "containers",
    "customers",
    "skus",
]


def upgrade() -> None:
    for statement in TABLES:
        op.execute(statement)
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
