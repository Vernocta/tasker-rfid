"""State engine: observations to movements and anomalies. SPEC.md section 4, layer 4.

    Apply the transition. All status changes go through this module — no
    other code writes containers.status.

That sentence is the reason this service exists. Ingest, the debouncer and
the API all stop short of `containers.status`; only the code below sets
it. One place to reason about, one place to audit, one place where the
rules in SPEC.md section 2.3 are enforced.

Rules, from SPEC.md section 4:

- Illegal transitions become anomalies. Never silently dropped.
- An exit read with no open dispatch session becomes a NO_SESSION anomaly
  and nothing moves, so a load cannot leave unattributed (SPEC.md 2.5).
- Moving a pallet moves everything on it, in the same transaction.
- A read from a tag with no container record becomes an UNKNOWN_TID
  anomaly and is not counted.
- Reusable containers return to REGISTERED on re-entry rather than being
  consumed.

Each observation is handled in its own transaction: the status changes,
the movement rows, any anomaly, and marking the observation processed all
commit together or not at all. A crash mid-way leaves the observation
unprocessed and it is simply done again.
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from .transitions import (
    DISPATCHED,
    NO_SESSION,
    SHORT_PALLET,
    UNKNOWN_TID,
    Decision,
    decide,
)

log = logging.getLogger("state_engine")

FETCH_BATCH = 500
POLL_INTERVAL_S = 0.25
STATS_INTERVAL_S = 10.0
DB_RETRY_INITIAL_S = 1.0
DB_RETRY_MAX_S = 30.0

PALLET_SETTLE_S = 5.0
"""How long to hold a pallet's observation before judging its load.

The boxes on a pallet cross the portal together, but their observations
are not written together: the debouncer releases each tag's group once
that tag has gone quiet, so a pallet's observation can land a second or
two before the last box's. Counting the load at that moment sees boxes
missing that are simply still in flight, and raises a SHORT_PALLET for a
pallet that was complete.

So a pallet observation waits until every sibling has had time to appear.
Comfortably longer than the debouncer's quiet period. Only pallets wait;
everything else is handled as soon as it arrives.
"""

SHORT_PALLET_WINDOW_S = 15.0
"""How close in time a child's own observation must be to its pallet's.

A pallet and the boxes on it cross a portal together, so their
observations overlap. This window is generous enough to absorb the quiet
period at both ends without reaching into a different load.
"""

SOURCE_PORTAL = "PORTAL"

FETCH_OBSERVATIONS_SQL = """
    SELECT id, tid, portal, direction, first_read, last_read
    FROM observations
    WHERE processed = FALSE
    ORDER BY id
    LIMIT %s
"""

# Locked, because this is the row whose status only this service may set.
LOCK_CONTAINER_SQL = """
    SELECT container_id, kind, status, reusable
    FROM containers
    WHERE tid = %s
    FOR UPDATE
"""

# A pallet and everything stacked on it, however deep the nesting goes.
CONTAINER_TREE_SQL = """
    WITH RECURSIVE tree AS (
        SELECT container_id, tid, kind, status, reusable
        FROM containers WHERE container_id = %s
      UNION ALL
        SELECT c.container_id, c.tid, c.kind, c.status, c.reusable
        FROM containers c JOIN tree t ON c.parent_id = t.container_id
    )
    SELECT container_id, tid, kind, status, reusable FROM tree
"""

CHILD_TIDS_SQL = "SELECT tid FROM containers WHERE parent_id = %s"

CHILDREN_SEEN_SQL = """
    SELECT count(DISTINCT tid)
    FROM observations
    WHERE portal = %s
      AND tid = ANY(%s)
      AND last_read BETWEEN %s AND %s
"""

OPEN_SESSION_SQL = """
    SELECT session_id FROM dispatch_sessions
    WHERE closed_at IS NULL
    ORDER BY opened_at DESC
    LIMIT 1
"""

UPDATE_STATUS_SQL = """
    UPDATE containers
    SET status = %s, last_seen_at = %s, last_portal = %s
    WHERE container_id = %s
"""

TOUCH_CONTAINER_SQL = """
    UPDATE containers SET last_seen_at = %s, last_portal = %s WHERE container_id = %s
"""

INSERT_MOVEMENT_SQL = """
    INSERT INTO movements
        (container_id, from_status, to_status, portal, session_id, occurred_at, source)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

INSERT_ANOMALY_SQL = """
    INSERT INTO anomalies (tid, container_id, kind, detail, occurred_at)
    VALUES (%s, %s, %s, %s, %s)
"""

MARK_PROCESSED_SQL = "UPDATE observations SET processed = TRUE WHERE id = %s"


def psycopg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


class StateEngine:
    def __init__(self, *, database_url: str) -> None:
        self.dsn = psycopg_dsn(database_url)
        self.conn: psycopg.Connection | None = None
        self.stopping = False

        self.processed = 0
        self.movements = 0
        self.anomalies = 0
        self.no_ops = 0
        # Observation id -> when this service first saw it, for the wait above.
        self.first_seen: dict[int, float] = {}

    # -- Database ---------------------------------------------------------

    def connect(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.dsn, autocommit=False)
        return self.conn

    def with_retry(self, what: str, action):
        delay = DB_RETRY_INITIAL_S
        while True:
            try:
                return action(self.connect())
            except psycopg.Error as exc:
                log.error("%s failed (%s); retrying in %.0fs", what, exc, delay)
                try:
                    if self.conn is not None:
                        self.conn.rollback()
                        self.conn.close()
                except psycopg.Error:
                    pass
                self.conn = None
                time.sleep(delay)
                delay = min(delay * 2, DB_RETRY_MAX_S)

    def fetch_observations(self) -> list[tuple]:
        def action(conn):
            with conn.cursor() as cur:
                cur.execute(FETCH_OBSERVATIONS_SQL, (FETCH_BATCH,))
                rows = cur.fetchall()
            conn.rollback()
            return rows

        return self.with_retry("fetching observations", action)

    # -- Writing (the only place containers.status is set) ----------------

    def record_anomaly(
        self,
        cur,
        *,
        tid: str,
        container_id: int | None,
        kind: str,
        detail: dict,
        occurred_at: datetime,
    ) -> None:
        cur.execute(
            INSERT_ANOMALY_SQL,
            (tid, container_id, kind, Jsonb(detail), occurred_at),
        )
        self.anomalies += 1
        log.info("anomaly %s for %s: %s", kind, tid, json.dumps(detail))

    def move_container_tree(
        self,
        cur,
        *,
        root_id: int,
        to_status: str,
        portal: str,
        session_id: int | None,
        occurred_at: datetime,
    ) -> int:
        """Move a container and everything on it, in the caller's transaction.

        SPEC.md section 4: moving a pallet moves all children. A box that
        is already at the target status is skipped, so re-reading a pallet
        does not pile up duplicate movement rows.
        """
        cur.execute(CONTAINER_TREE_SQL, (root_id,))
        tree = cur.fetchall()

        moved = 0
        for container_id, _tid, _kind, status, _reusable in tree:
            if status == to_status:
                cur.execute(TOUCH_CONTAINER_SQL, (occurred_at, portal, container_id))
                continue
            cur.execute(
                UPDATE_STATUS_SQL, (to_status, occurred_at, portal, container_id)
            )
            cur.execute(
                INSERT_MOVEMENT_SQL,
                (
                    container_id,
                    status,
                    to_status,
                    portal,
                    session_id,
                    occurred_at,
                    SOURCE_PORTAL,
                ),
            )
            moved += 1
        self.movements += moved
        return moved

    def check_short_pallet(
        self,
        cur,
        *,
        container_id: int,
        tid: str,
        portal: str,
        first_read: datetime,
        last_read: datetime,
        occurred_at: datetime,
    ) -> None:
        """Were fewer boxes read than are attached to this pallet?

        SPEC.md section 7: a pallet read whose children come up short means
        boxes were missed at the portal, or are not on the pallet at all.
        The pallet still moves — the load did leave — but the discrepancy
        is recorded for a person to reconcile.
        """
        cur.execute(CHILD_TIDS_SQL, (container_id,))
        child_tids = [row[0] for row in cur.fetchall()]
        if not child_tids:
            return

        margin = timedelta(seconds=SHORT_PALLET_WINDOW_S)
        cur.execute(
            CHILDREN_SEEN_SQL,
            (portal, child_tids, first_read - margin, last_read + margin),
        )
        seen = cur.fetchone()[0]

        if seen < len(child_tids):
            self.record_anomaly(
                cur,
                tid=tid,
                container_id=container_id,
                kind=SHORT_PALLET,
                detail={
                    "declared_children": len(child_tids),
                    "children_read": seen,
                    "missing": len(child_tids) - seen,
                    "portal": portal,
                },
                occurred_at=occurred_at,
            )

    def is_settled(self, obs_id: int, tid: str) -> bool:
        """Has this pallet's load had time to finish arriving?

        Measured in wall time since this service first saw the observation,
        not from the read timestamps, so a reader with an odd clock cannot
        make a pallet wait forever.
        """
        first_seen = self.first_seen.setdefault(obs_id, time.monotonic())
        if time.monotonic() - first_seen >= PALLET_SETTLE_S:
            return True
        log.debug("holding pallet %s for its load to finish arriving", tid)
        return False

    def has_children(self, container_id: int) -> bool:
        def action(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM containers WHERE parent_id = %s)",
                    (container_id,),
                )
                found = cur.fetchone()[0]
            conn.rollback()
            return found

        return self.with_retry("looking for child containers", action)

    def container_id_for(self, tid: str) -> int | None:
        def action(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT container_id FROM containers WHERE tid = %s", (tid,))
                row = cur.fetchone()
            conn.rollback()
            return row[0] if row else None

        return self.with_retry("looking up a container", action)

    def should_wait(self, observation: tuple) -> bool:
        """True if this observation is a loaded pallet that has not settled."""
        obs_id, tid, _portal, _direction, _first_read, _last_read = observation
        container_id = self.container_id_for(tid)
        if container_id is None:
            return False
        if not self.has_children(container_id):
            return False
        return not self.is_settled(obs_id, tid)

    def handle(self, observation: tuple) -> None:
        """Apply one observation, in its own transaction."""
        obs_id, tid, portal, direction, first_read, last_read = observation
        occurred_at = last_read

        def action(conn):
            with conn.cursor() as cur:
                cur.execute(LOCK_CONTAINER_SQL, (tid,))
                row = cur.fetchone()

                if row is None:
                    # SPEC.md section 4: a read from a tag with no container
                    # record is an anomaly and is not counted.
                    self.record_anomaly(
                        cur,
                        tid=tid,
                        container_id=None,
                        kind=UNKNOWN_TID,
                        detail={"portal": portal, "direction": direction},
                        occurred_at=occurred_at,
                    )
                    cur.execute(MARK_PROCESSED_SQL, (obs_id,))
                    conn.commit()
                    return

                container_id, kind, status, reusable = row
                decision: Decision = decide(
                    portal=portal, direction=direction, status=status, reusable=reusable
                )

                if decision.anomaly is not None:
                    self.record_anomaly(
                        cur,
                        tid=tid,
                        container_id=container_id,
                        kind=decision.anomaly,
                        detail={
                            "portal": portal,
                            "direction": direction,
                            "status": status,
                            "reason": decision.reason,
                        },
                        occurred_at=occurred_at,
                    )
                    cur.execute(TOUCH_CONTAINER_SQL, (occurred_at, portal, container_id))
                    cur.execute(MARK_PROCESSED_SQL, (obs_id,))
                    conn.commit()
                    return

                if decision.is_no_op:
                    # The heart of SPEC.md 2.3. A container already in the
                    # state it would move to is left alone, so a tag parked
                    # in the field for an hour changes nothing.
                    self.no_ops += 1
                    cur.execute(TOUCH_CONTAINER_SQL, (occurred_at, portal, container_id))
                    cur.execute(MARK_PROCESSED_SQL, (obs_id,))
                    conn.commit()
                    return

                session_id = None
                if decision.to_status == DISPATCHED:
                    cur.execute(OPEN_SESSION_SQL)
                    session_row = cur.fetchone()
                    if session_row is None:
                        # SPEC.md 2.5: a dispatch without a destination is
                        # an anomaly, not a valid state. Nothing moves.
                        self.record_anomaly(
                            cur,
                            tid=tid,
                            container_id=container_id,
                            kind=NO_SESSION,
                            detail={
                                "portal": portal,
                                "direction": direction,
                                "status": status,
                                "reason": "no open dispatch session; nothing dispatched",
                            },
                            occurred_at=occurred_at,
                        )
                        cur.execute(
                            TOUCH_CONTAINER_SQL, (occurred_at, portal, container_id)
                        )
                        cur.execute(MARK_PROCESSED_SQL, (obs_id,))
                        conn.commit()
                        return
                    session_id = session_row[0]

                moved = self.move_container_tree(
                    cur,
                    root_id=container_id,
                    to_status=decision.to_status,
                    portal=portal,
                    session_id=session_id,
                    occurred_at=occurred_at,
                )

                # A pallet that moved with boxes missing is still a move,
                # but the shortfall is recorded alongside it.
                self.check_short_pallet(
                    cur,
                    container_id=container_id,
                    tid=tid,
                    portal=portal,
                    first_read=first_read,
                    last_read=last_read,
                    occurred_at=occurred_at,
                )

                cur.execute(MARK_PROCESSED_SQL, (obs_id,))
                conn.commit()
                log.debug(
                    "%s at %s: %s -> %s (%d container(s))",
                    tid,
                    portal,
                    status,
                    decision.to_status,
                    moved,
                )

        self.with_retry(f"handling observation {obs_id}", action)
        self.first_seen.pop(obs_id, None)
        self.processed += 1

    def run(self) -> int:
        log.info("state engine running; this is the only writer of containers.status")
        last_stats = time.monotonic()

        while not self.stopping:
            observations = self.fetch_observations()
            handled_any = False
            for observation in observations:
                if self.stopping:
                    break
                if self.should_wait(observation):
                    continue
                self.handle(observation)
                handled_any = True

            if not handled_any:
                # Either nothing to do, or everything left is a pallet
                # waiting for its load to finish arriving.
                time.sleep(POLL_INTERVAL_S)

            if time.monotonic() - last_stats >= STATS_INTERVAL_S:
                log.info(
                    "observations=%d movements=%d anomalies=%d no_ops=%d",
                    self.processed,
                    self.movements,
                    self.anomalies,
                    self.no_ops,
                )
                last_stats = time.monotonic()

        if self.conn is not None and not self.conn.closed:
            self.conn.close()
        log.info(
            "stopped. observations=%d movements=%d anomalies=%d no_ops=%d",
            self.processed,
            self.movements,
            self.anomalies,
            self.no_ops,
        )
        return 0

    def request_stop(self, *_args) -> None:
        self.stopping = True


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        sys.exit("ERROR: DATABASE_URL is not set. Copy .env.example to .env and fill it in.")

    service = StateEngine(database_url=database_url)
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    sys.exit(service.run())


if __name__ == "__main__":
    main()
