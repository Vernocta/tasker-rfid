"""Sync: local Postgres to a cloud replica. SPEC.md section 2.4.

    Postgres runs on the edge device in the warehouse. Cloud sync is
    asynchronous. Warehouse networks are unreliable and a lost dispatch
    read is an unrecoverable inventory error.

So the local database is the truth and the cloud is a copy that trails it.
Three properties follow, and the design exists to make them structural
rather than promised.

**NOTHING DEPENDS ON THE CLOUD.** This worker only ever *reads* the local
database. It has no write path to it at all — not even for its own
bookkeeping, which is why the cursor lives in the cloud. Unplug the cloud,
or this whole service, and the warehouse carries on exactly as before.

**IT CANNOT LOSE ROWS.** The cursor is written to the cloud in the same
transaction as the rows it covers. There is no moment where the cursor has
moved past rows that did not land: either the transaction committed, and
both are there, or it did not, and the next run reads from the same place.
Killing the connection mid-sync costs a retry, never a row.

**RUNNING IT TWICE IS HARMLESS.** Every write is an upsert keyed on the
primary key. Re-sending a batch overwrites it with identical values.

Nothing in this system deletes rows, so nothing here propagates deletions.
If that ever changes, this worker will need to learn about it.
"""

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass

import psycopg
from datetime import datetime, timezone
from dotenv import load_dotenv
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ...db_errors import CLOUD_MIGRATIONS_COMMAND, explain
from .tables import SYNCED_TABLES, SyncedTable


class LocalDatabaseError(Exception):
    """Something went wrong reading the warehouse database.

    Kept apart from the cloud's failures on purpose. This service reads one
    machine and writes another, and a single handler covering both once
    reported a missing local table as "cannot reach the cloud replica" —
    which sends somebody to debug the wrong machine.
    """


class CloudDatabaseError(Exception):
    """Something went wrong reaching or writing the cloud replica."""

log = logging.getLogger("sync")

BATCH_ROWS = 5000
"""Rows per transaction. Large enough to move a busy day's reads quickly,
small enough that losing a connection costs little work."""

IDLE_SLEEP_S = 5.0
"""How long to wait after everything is up to date."""

RETRY_INITIAL_S = 2.0
RETRY_MAX_S = 60.0
"""Backoff when the cloud is unreachable. Which it will be, regularly."""

STATS_INTERVAL_S = 30.0

NEVER_SYNCED = datetime(1970, 1, 1, tzinfo=timezone.utc)
"""Where an updated_at cursor starts, and the filler for tables that do not
use one.

Deliberately a real timestamp rather than '-infinity'. Postgres accepts
infinite timestamps but Python's datetime has no such value, so reading
the cursor back raised on every cycle after the first and the sync stalled
for good. Nothing here can predate 1970, so this is just as safe a floor
and it survives the round trip."""


def psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def adapt(value):
    """Make a value read from one database safe to send to another.

    A JSONB column (anomalies.detail) comes back as a Python dict, and a
    bare dict is not something psycopg will send as a parameter — it has
    to be told the value is JSON. Everything else passes through.
    """
    if isinstance(value, (dict, list)):
        return Jsonb(value)
    return value


@dataclass
class TableProgress:
    rows: int = 0
    caught_up: bool = False


class Sync:
    def __init__(self, *, local_url: str, cloud_url: str) -> None:
        self.local_dsn = psycopg_dsn(local_url)
        self.cloud_dsn = psycopg_dsn(cloud_url)
        self.local: psycopg.Connection | None = None
        self.cloud: psycopg.Connection | None = None
        self.stopping = False
        self.rows_sent = 0
        self.cycles = 0
        # Column lists are read from the database rather than written out
        # here, so a migration that adds a column does not need this file
        # edited as well.
        self.columns: dict[str, list[str]] = {}
        self.keys: dict[str, list[str]] = {}

    # -- Connections ------------------------------------------------------

    def local_connection(self) -> psycopg.Connection:
        """Read-only. This worker must never write to the warehouse."""
        if self.local is None or self.local.closed:
            self.local = psycopg.connect(self.local_dsn, autocommit=True)
            with self.local.cursor() as cur:
                cur.execute("SET default_transaction_read_only = on")
        return self.local

    def reading_local(self, action):
        """Run something against the warehouse database, tagging failures.

        Anything that goes wrong in here is the local machine's problem,
        never the cloud's.
        """
        try:
            return action()
        except psycopg.Error as exc:
            self.drop_local()
            raise LocalDatabaseError(
                explain(exc, database="The warehouse database")
            ) from exc

    def writing_cloud(self, action):
        """Run something against the cloud replica, tagging failures."""
        try:
            return action()
        except psycopg.Error as exc:
            raise CloudDatabaseError(
                explain(
                    exc,
                    database="The cloud replica",
                    command=CLOUD_MIGRATIONS_COMMAND,
                )
            ) from exc

    def drop_local(self) -> None:
        try:
            if self.local is not None:
                self.local.close()
        except psycopg.Error:
            pass
        self.local = None

    def cloud_connection(self) -> psycopg.Connection:
        if self.cloud is None or self.cloud.closed:
            self.cloud = psycopg.connect(self.cloud_dsn, autocommit=False)
        return self.cloud

    def drop_cloud(self) -> None:
        try:
            if self.cloud is not None:
                self.cloud.close()
        except psycopg.Error:
            pass
        self.cloud = None

    # -- Schema ------------------------------------------------------------

    def describe(self, table: str) -> tuple[list[str], list[str]]:
        """Column names and primary key of a table, read from the catalog."""
        if table in self.columns:
            return self.columns[table], self.keys[table]

        conn = self.reading_local(self.local_connection)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                   ORDER BY ordinal_position""",
                (table,),
            )
            columns = [row[0] for row in cur.fetchall()]
            cur.execute(
                """SELECT a.attname
                   FROM pg_index i
                   JOIN pg_attribute a
                     ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                   WHERE i.indrelid = %s::regclass AND i.indisprimary""",
                (table,),
            )
            keys = [row[0] for row in cur.fetchall()]

        if not columns:
            raise RuntimeError(f"the local database has no table '{table}'")
        if not keys:
            raise RuntimeError(f"'{table}' has no primary key, so it cannot be upserted")

        self.columns[table] = columns
        self.keys[table] = keys
        return columns, keys

    def check_cloud_schema(self) -> None:
        """Fail loudly and usefully if the cloud has not been migrated."""
        conn = self.cloud_connection()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema = 'public'"""
            )
            present = {row[0] for row in cur.fetchall()}
        conn.rollback()

        expected = {t.name for t in SYNCED_TABLES} | {"sync_cursor"}
        missing = sorted(expected - present)
        if missing:
            raise RuntimeError(
                "the cloud replica is missing "
                + ", ".join(missing)
                + ". Create the schema there with:\n"
                "    DATABASE_URL=\"$CLOUD_DATABASE_URL\" uv run alembic upgrade head"
            )

    # -- Reading the local side --------------------------------------------

    def read_batch(self, table: SyncedTable, cursor_value) -> list[dict]:
        """The next rows to send, in cursor order.

        A full batch whose rows all share one `updated_at` would leave the
        cursor where it started and the same rows fetched forever, so that
        case is re-read without a limit. It takes a lot of rows updated in
        the same microsecond, but "unlikely" is not the same as "cannot".
        """
        columns, keys = self.describe(table.name)
        column_sql = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
        cursor_column = sql.Identifier(table.cursor_column)
        order = sql.SQL(", ").join(
            [cursor_column] + [sql.Identifier(k) for k in keys]
        )

        query = sql.SQL(
            "SELECT {cols} FROM {table} WHERE {cursor} > %s ORDER BY {order} LIMIT %s"
        ).format(
            cols=column_sql,
            table=sql.Identifier(table.name),
            cursor=cursor_column,
            order=order,
        )

        conn = self.reading_local(self.local_connection)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (cursor_value, BATCH_ROWS))
            rows = cur.fetchall()

            if not table.by_id and len(rows) == BATCH_ROWS:
                stamps = {row["updated_at"] for row in rows}
                if len(stamps) == 1:
                    stuck = stamps.pop()
                    log.warning(
                        "%s has more than %d rows stamped %s; reading them all at once",
                        table.name,
                        BATCH_ROWS,
                        stuck,
                    )
                    cur.execute(
                        sql.SQL(
                            "SELECT {cols} FROM {table} WHERE {cursor} = %s ORDER BY {order}"
                        ).format(
                            cols=column_sql,
                            table=sql.Identifier(table.name),
                            cursor=cursor_column,
                            order=order,
                        ),
                        (stuck,),
                    )
                    rows = cur.fetchall()
        return rows

    # -- Writing the cloud side --------------------------------------------

    def read_cursor(self, cur, table: str):
        cur.execute(
            "SELECT last_id, last_updated_at FROM sync_cursor WHERE table_name = %s",
            (table,),
        )
        row = cur.fetchone()
        return row  # None until the first batch lands

    def upsert_statement(self, table: SyncedTable, columns: list[str], keys: list[str]):
        updatable = [c for c in columns if c not in keys]
        if table.by_id or not updatable:
            # Append-only: the row cannot have changed, so a repeat is a
            # no-op rather than a rewrite.
            conflict = sql.SQL("DO NOTHING")
        else:
            conflict = sql.SQL("DO UPDATE SET {}").format(
                sql.SQL(", ").join(
                    sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
                    for c in updatable
                )
            )
        return sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT ({keys}) {conflict}"
        ).format(
            table=sql.Identifier(table.name),
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            vals=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            keys=sql.SQL(", ").join(sql.Identifier(k) for k in keys),
            conflict=conflict,
        )

    def send_batch(self, table: SyncedTable, rows: list[dict]) -> None:
        """Write rows and the cursor to the cloud in ONE transaction.

        This is the whole guarantee. The cursor cannot move past rows that
        did not land, because they commit together or not at all.
        """
        columns, keys = self.describe(table.name)
        conn = self.cloud_connection()

        with conn.cursor() as cur:
            if table.self_referencing:
                # A pallet and a box on it can be in the same batch with the
                # box first, and the box points at the pallet. Write every
                # row with no parent, then set the parents once they all
                # exist. Same transaction, so nothing sees the half-state.
                without_parent = [
                    tuple(None if c == "parent_id" else adapt(row[c]) for c in columns)
                    for row in rows
                ]
                cur.executemany(
                    self.upsert_statement(table, columns, keys), without_parent
                )
                cur.executemany(
                    sql.SQL(
                        "UPDATE {table} SET parent_id = %s WHERE {key} = %s"
                    ).format(
                        table=sql.Identifier(table.name), key=sql.Identifier(keys[0])
                    ),
                    [(row["parent_id"], row[keys[0]]) for row in rows],
                )
            else:
                cur.executemany(
                    self.upsert_statement(table, columns, keys),
                    [tuple(adapt(row[c]) for c in columns) for row in rows],
                )

            last = rows[-1][table.cursor_column]
            cur.execute(
                """INSERT INTO sync_cursor
                       (table_name, last_id, last_updated_at, rows_synced, synced_at)
                   VALUES (%s, %s, %s, %s, now())
                   ON CONFLICT (table_name) DO UPDATE SET
                       last_id = EXCLUDED.last_id,
                       last_updated_at = EXCLUDED.last_updated_at,
                       rows_synced = sync_cursor.rows_synced + EXCLUDED.rows_synced,
                       synced_at = now()""",
                (
                    table.name,
                    last if table.by_id else 0,
                    last if not table.by_id else NEVER_SYNCED,
                    len(rows),
                ),
            )
        conn.commit()

    # -- The loop -----------------------------------------------------------

    def sync_table(self, table: SyncedTable) -> TableProgress:
        def read_position():
            conn = self.cloud_connection()
            with conn.cursor() as cur:
                found = self.read_cursor(cur, table.name)
            conn.rollback()
            return found

        position = self.writing_cloud(read_position)

        if position is None:
            cursor_value = 0 if table.by_id else NEVER_SYNCED
        else:
            cursor_value = position[0] if table.by_id else position[1]

        rows = self.reading_local(lambda: self.read_batch(table, cursor_value))
        if not rows:
            return TableProgress(rows=0, caught_up=True)

        try:
            self.writing_cloud(lambda: self.send_batch(table, rows))
        except CloudDatabaseError as wrapped:
            # A foreign key violation is not an outage: a row arrived before
            # the row it points at. Unwrap it so the caller below can tell
            # the two apart.
            if not isinstance(wrapped.__cause__, psycopg.errors.ForeignKeyViolation):
                raise
            exc = wrapped.__cause__
            # A row arrived before the row it points at — the parent is in a
            # table this cycle has not reached, or in a later batch. Leave
            # the cursor where it is and try again next cycle, by which time
            # the parent will have gone across.
            self.cloud.rollback()
            log.warning(
                "%s: holding %d row(s) until what they refer to has synced (%s)",
                table.name,
                len(rows),
                exc.diag.constraint_name or exc,
            )
            return TableProgress(rows=0, caught_up=True)

        self.rows_sent += len(rows)
        return TableProgress(rows=len(rows), caught_up=len(rows) < BATCH_ROWS)

    def run_once(self) -> bool:
        """One pass over every table. True when everything is up to date."""
        everything_caught_up = True
        for table in SYNCED_TABLES:
            if self.stopping:
                break
            progress = self.sync_table(table)
            if progress.rows:
                log.debug("%s: sent %d row(s)", table.name, progress.rows)
            if not progress.caught_up:
                everything_caught_up = False
        return everything_caught_up

    def run(self) -> int:
        log.info(
            "sync running; local is read-only to this service, cursor lives in the cloud"
        )
        delay = RETRY_INITIAL_S
        last_stats = time.monotonic()
        announced_outage = False

        while not self.stopping:
            try:
                self.writing_cloud(self.check_cloud_schema)
                caught_up = self.run_once()
                self.cycles += 1

                if announced_outage:
                    log.info("cloud is back; caught up")
                    announced_outage = False
                delay = RETRY_INITIAL_S

                if caught_up:
                    time.sleep(IDLE_SLEEP_S)

            except RuntimeError as exc:
                # Something we can tell them how to fix, so say all of it:
                # the message carries the command to run.
                if not announced_outage:
                    log.warning(
                        "%s\nThe warehouse is unaffected; retrying. Nothing is "
                        "lost in the meantime.",
                        exc,
                    )
                    announced_outage = True
                self.drop_cloud()
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_S)

            except LocalDatabaseError as exc:
                # The warehouse's own database, not the cloud's. Saying
                # "cloud" here would send somebody to the wrong machine.
                if not announced_outage:
                    log.error(
                        "cannot read the warehouse database: %s\nSync is stalled "
                        "until this is fixed. The warehouse itself is unaffected.",
                        exc,
                    )
                    announced_outage = True
                # The local connection is already gone. Drop the cloud one
                # too: a local outage can last a long time, and an idle
                # connection held across it is likely to be dead anyway.
                self.drop_cloud()
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_S)

            except CloudDatabaseError as exc:
                # Nothing local depends on this succeeding, so it is a
                # warning and a retry, not an error.
                if not announced_outage:
                    log.warning(
                        "cannot reach the cloud replica: %s\nThe warehouse is "
                        "unaffected; retrying, and nothing is lost in the meantime.",
                        exc,
                    )
                    announced_outage = True
                self.drop_cloud()
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_S)

            if time.monotonic() - last_stats >= STATS_INTERVAL_S:
                log.info("rows sent=%d cycles=%d", self.rows_sent, self.cycles)
                last_stats = time.monotonic()

        self.drop_cloud()
        if self.local is not None and not self.local.closed:
            self.local.close()
        log.info("stopped. rows sent=%d", self.rows_sent)
        return 0

    def request_stop(self, *_args) -> None:
        self.stopping = True


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )
    local_url = os.getenv("DATABASE_URL")
    cloud_url = os.getenv("CLOUD_DATABASE_URL")
    if not local_url:
        sys.exit("ERROR: DATABASE_URL is not set. Copy .env.example to .env.")
    if not cloud_url:
        sys.exit(
            "ERROR: CLOUD_DATABASE_URL is not set, so there is nowhere to sync to.\n"
            "Set it in .env. Until the real cloud database exists, docker compose "
            "runs a second Postgres that stands in for it."
        )

    service = Sync(local_url=local_url, cloud_url=cloud_url)
    signal.signal(signal.SIGINT, service.request_stop)
    signal.signal(signal.SIGTERM, service.request_stop)
    sys.exit(service.run())


if __name__ == "__main__":
    main()
