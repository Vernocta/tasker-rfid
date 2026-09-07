"""What gets copied to the cloud, and in what order.

Two kinds of table, needing two ways of asking "what is new?":

- **Append-only** — written once, never touched again. Their BIGSERIAL id
  is a perfect high-water mark: everything above the last id synced is
  new, and nothing below it can have changed.

- **Mutable** — a container's status changes long after its id was
  assigned, so an id watermark would never see it again. These are asked
  by `updated_at`, which a trigger keeps current (migration 0005).

Order matters. The cloud has the same foreign keys as the local database,
so a row must not arrive before the row it points at. This list is in
dependency order and the worker walks it in order.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SyncedTable:
    name: str
    by_id: bool
    """True to track by BIGSERIAL id, False to track by updated_at."""

    self_referencing: bool = False
    """True if a row can point at another row of the same table.

    Only `containers`, through parent_id. A pallet and a box on it can
    land in the same batch with the box first, which would break the
    foreign key, so those tables are written in two passes.
    """

    @property
    def cursor_column(self) -> str:
        return "id" if self.by_id else "updated_at"


# Parents before children. reads_raw and gate_events reference nothing, so
# their position is free; they sit late because they are much the largest.
SYNCED_TABLES: list[SyncedTable] = [
    SyncedTable("skus", by_id=False),
    SyncedTable("customers", by_id=False),
    SyncedTable("containers", by_id=False, self_referencing=True),
    SyncedTable("container_contents", by_id=False),
    SyncedTable("dispatch_sessions", by_id=False),
    SyncedTable("cycle_counts", by_id=False),
    SyncedTable("cycle_count_items", by_id=False),
    SyncedTable("movements", by_id=True),
    SyncedTable("anomalies", by_id=False),
    SyncedTable("observations", by_id=False),
    SyncedTable("gate_events", by_id=True),
    SyncedTable("reads_raw", by_id=True),
]

# Local bookkeeping, meaningless anywhere else: the debouncer's place in
# reads_raw, and the cloud's own record of how far it has been brought up
# to date.
NOT_SYNCED = {"debouncer_cursor", "sync_cursor", "alembic_version"}
