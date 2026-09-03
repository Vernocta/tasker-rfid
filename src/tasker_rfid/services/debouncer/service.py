"""Debouncer: reads_raw to observations. SPEC.md section 4, layer 2.

Polls reads_raw for rows it has not seen, groups them by (tid, portal),
and writes one observation per group once that tag has been quiet at that
portal for quiet_period_ms. Applies the RSSI floor and minimum read count
from config/tasker.yaml.

WHAT THIS SERVICE DOES NOT DO. It does not decide direction. That is
layer 3, and for the exit portal it needs IR beam data that no hardware
or simulator produces yet, so observations are written with direction
NULL for layer 3 to fill in later. It does not touch containers.status —
SPEC.md section 4 is explicit that only the state engine may do that.

HOW IT REMEMBERS ITS PLACE. reads_raw is append-only with no processed
flag, so the position is kept in debouncer_cursor (see migration 0002,
which explains why that table is not in SPEC.md section 3). Observations
and the cursor advance in the same transaction, so a crash cannot lose an
observation or double-count one that was already written.

ON AN UNCLEAN SHUTDOWN. Groups still open when the process dies are lost
from memory, and their reads have already been consumed. The event is not
lost — reads_raw keeps every read for replay — but the observation may be
split or truncated. A graceful stop (docker compose stop, Ctrl-C) flushes
open groups first, so a normal restart loses nothing.
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg
from dotenv import load_dotenv

from ...config import load_config, load_filters, portal_by_antenna
from .grouping import (
    Observation,
    OpenGroup,
    has_gone_quiet,
    rejection_reason,
    start_group,
    starts_new_session,
    to_observation,
)

log = logging.getLogger("debouncer")

FETCH_BATCH = 5000
"""Raw reads to pull per round. Large enough to keep up with a busy portal."""

POLL_INTERVAL_S = 0.25
"""How long to wait before asking again when there was nothing new."""

STATS_INTERVAL_S = 10.0

FUTURE_READ_WARN_S = 300
"""Warn when a read is stamped this far ahead of now: a reader clock is wrong.

Diagnostic only. Grouping is per (tid, portal), so a bad clock affects
only that reader's own tags and cannot disturb anything else.
"""

DB_RETRY_INITIAL_S = 1.0
DB_RETRY_MAX_S = 30.0

FETCH_SQL = """
    SELECT id, tid, antenna_id, rssi, read_at
    FROM reads_raw
    WHERE id > %s
    ORDER BY id
    LIMIT %s
"""

INSERT_OBSERVATION_SQL = """
    INSERT INTO observations
        (tid, portal, direction, first_read, last_read, read_count, peak_rssi, processed)
    VALUES (%s, %s, NULL, %s, %s, %s, %s, FALSE)
"""

READ_CURSOR_SQL = "SELECT last_read_id FROM debouncer_cursor WHERE name = 'debouncer'"

WRITE_CURSOR_SQL = """
    INSERT INTO debouncer_cursor (name, last_read_id, updated_at)
    VALUES ('debouncer', %s, now())
    ON CONFLICT (name) DO UPDATE
        SET last_read_id = EXCLUDED.last_read_id, updated_at = now()
"""


def psycopg_dsn(database_url: str) -> str:
    """The rest of the project uses SQLAlchemy URLs; psycopg wants a plain one."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


class Debouncer:
    def __init__(self, *, database_url: str) -> None:
        self.dsn = psycopg_dsn(database_url)
        config = load_config()
        self.filters = load_filters(config)
        self.portal_of = portal_by_antenna(config)

        self.open_groups: dict[tuple[str, str], OpenGroup] = {}
        self.closed_this_round: list[Observation] = []
        self.cursor_id = 0
        self.conn: psycopg.Connection | None = None
        self.stopping = False

        self.reads_consumed = 0
        self.observations_written = 0
        self.observations_rejected = 0
        self.unmapped_antenna_reads = 0
        self.future_dated_reads = 0

    # -- Database ---------------------------------------------------------

    def connect(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.dsn, autocommit=False)
        return self.conn

    def with_retry(self, what: str, action):
        """Run a database action, retrying forever. Never gives up quietly."""
        delay = DB_RETRY_INITIAL_S
        while True:
            try:
                return action(self.connect())
            except psycopg.Error as exc:
                log.error("%s failed (%s); retrying in %.0fs", what, exc, delay)
                try:
                    if self.conn is not None:
                        self.conn.close()
                except psycopg.Error:
                    pass
                self.conn = None
                time.sleep(delay)
                delay = min(delay * 2, DB_RETRY_MAX_S)

    def load_cursor(self) -> int:
        def action(conn):
            with conn.cursor() as cur:
                cur.execute(READ_CURSOR_SQL)
                row = cur.fetchone()
            conn.rollback()
            return row[0] if row else 0

        return self.with_retry("reading the cursor", action)

    def fetch_reads(self) -> list[tuple]:
        def action(conn):
            with conn.cursor() as cur:
                cur.execute(FETCH_SQL, (self.cursor_id, FETCH_BATCH))
                rows = cur.fetchall()
            conn.rollback()
            return rows

        return self.with_retry("fetching reads", action)

    def commit(self, observations: list[Observation], cursor_id: int) -> None:
        """Write observations and advance the cursor in one transaction.

        Together, so a crash between the two is impossible: either the
        observations exist and the cursor has moved past their reads, or
        neither happened and the reads are simply read again.
        """

        def action(conn):
            with conn.cursor() as cur:
                if observations:
                    cur.executemany(
                        INSERT_OBSERVATION_SQL,
                        [
                            (
                                o.tid,
                                o.portal,
                                o.first_read,
                                o.last_read,
                                o.read_count,
                                o.peak_rssi,
                            )
                            for o in observations
                        ],
                    )
                cur.execute(WRITE_CURSOR_SQL, (cursor_id,))
            conn.commit()

        self.with_retry("writing observations", action)
        self.observations_written += len(observations)

    # -- The work ---------------------------------------------------------

    def warn_if_future_dated(self, read_at: datetime, tid: str) -> None:
        """Flag a reader whose clock is wrong. Does not change grouping."""
        if read_at <= datetime.now(timezone.utc) + timedelta(seconds=FUTURE_READ_WARN_S):
            return
        self.future_dated_reads += 1
        if self.future_dated_reads % 1000 == 1:
            log.warning(
                "read of %s is stamped %s, well ahead of now. Check that reader's "
                "clock. The read is stored and grouped normally.",
                tid,
                read_at.isoformat(),
            )

    def absorb(self, rows: list[tuple]) -> None:
        """Fold a batch of raw reads into the open groups.

        A read that arrives more than the quiet period after its group's
        last read belongs to a separate event, so the open group is closed
        and a fresh one started from that read.
        """
        monotonic_now = time.monotonic()
        quiet_period_s = self.filters.quiet_period_s

        for _read_id, tid, antenna_id, rssi, read_at in rows:
            portal = self.portal_of.get(antenna_id)
            if portal is None:
                # An antenna that belongs to no portal in config/tasker.yaml.
                # A configuration error, not a read to guess about. The read
                # stays in reads_raw and can be replayed once the config is
                # fixed.
                self.unmapped_antenna_reads += 1
                log.warning(
                    "antenna %s is not listed under any portal in config/tasker.yaml; "
                    "read of %s ignored",
                    antenna_id,
                    tid,
                )
                continue

            self.warn_if_future_dated(read_at, tid)

            key = (tid, portal)
            group = self.open_groups.get(key)
            if group is not None and starts_new_session(group, read_at, quiet_period_s):
                self.closed_this_round.append(to_observation(group))
                group = None

            if group is None:
                self.open_groups[key] = start_group(
                    tid, portal, read_at, rssi, monotonic_now
                )
            else:
                group.add(read_at, rssi, monotonic_now)
        self.reads_consumed += len(rows)

    def close_quiet_groups(self, force: bool = False) -> list[Observation]:
        """Collect finished observations and apply the filters.

        Takes groups that absorb() already closed because a later read
        started a new event, plus any that have simply gone silent.
        """
        monotonic_now = time.monotonic()
        quiet_period_s = self.filters.quiet_period_s

        finished = self.closed_this_round
        self.closed_this_round = []

        for key in [
            key
            for key, group in self.open_groups.items()
            if force or has_gone_quiet(group, monotonic_now, quiet_period_s)
        ]:
            finished.append(to_observation(self.open_groups.pop(key)))

        kept: list[Observation] = []
        for observation in finished:
            reason = rejection_reason(
                observation, self.filters.rssi_floor_dbm, self.filters.min_read_count
            )
            if reason is not None:
                self.observations_rejected += 1
                log.info(
                    "rejected %s at %s: %s", observation.tid, observation.portal, reason
                )
                continue
            log.debug(
                "observation %s at %s: %d reads over %.2fs, peak %s dBm",
                observation.tid,
                observation.portal,
                observation.read_count,
                observation.duration_s,
                observation.peak_rssi,
            )
            kept.append(observation)
        return kept

    def run(self) -> int:
        self.cursor_id = self.load_cursor()
        log.info(
            "debouncer running; resuming after read id %d. "
            "quiet_period=%dms rssi_floor=%ddBm min_read_count=%d",
            self.cursor_id,
            self.filters.quiet_period_ms,
            self.filters.rssi_floor_dbm,
            self.filters.min_read_count,
        )

        last_stats = time.monotonic()
        while not self.stopping:
            rows = self.fetch_reads()
            if rows:
                self.absorb(rows)
                highest_id = rows[-1][0]
            else:
                highest_id = self.cursor_id

            observations = self.close_quiet_groups()
            if observations or highest_id != self.cursor_id:
                self.commit(observations, highest_id)
                self.cursor_id = highest_id

            if not rows:
                time.sleep(POLL_INTERVAL_S)

            if time.monotonic() - last_stats >= STATS_INTERVAL_S:
                log.info(
                    "reads=%d observations=%d rejected=%d open_groups=%d",
                    self.reads_consumed,
                    self.observations_written,
                    self.observations_rejected,
                    len(self.open_groups),
                )
                last_stats = time.monotonic()

        # Graceful stop: flush whatever is still open rather than losing it.
        log.info("stopping; flushing %d open group(s)", len(self.open_groups))
        final = self.close_quiet_groups(force=True)
        self.commit(final, self.cursor_id)
        if self.conn is not None and not self.conn.closed:
            self.conn.close()
        log.info(
            "stopped. reads=%d observations=%d rejected=%d",
            self.reads_consumed,
            self.observations_written,
            self.observations_rejected,
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

    service = Debouncer(database_url=database_url)
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    sys.exit(service.run())


if __name__ == "__main__":
    main()
