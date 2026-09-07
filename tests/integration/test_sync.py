"""Cloud sync. SPEC.md section 2.4.

    Postgres runs on the edge device in the warehouse. Cloud sync is
    asynchronous.

The local database is the truth; the cloud is a copy that trails it. These
tests are about the three things that has to mean: the warehouse never
depends on the cloud, nothing is lost when the link drops, and running the
sync again changes nothing.

They need both databases and the sync worker:

    docker compose up -d
    uv run alembic upgrade head
    DATABASE_URL="$CLOUD_DATABASE_URL" uv run alembic upgrade head
"""

import os
import subprocess
import time

import psycopg
import pytest

from conftest import a_tid, dsn, register, run_and_settle, wait_until

CLOUD_DSN = os.getenv(
    "CLOUD_DATABASE_URL", "postgresql://tasker:tasker@localhost:5433/tasker_cloud"
).replace("postgresql+psycopg://", "postgresql://", 1)

SYNCED_TABLES = [
    "skus", "customers", "containers", "container_contents",
    "dispatch_sessions", "movements", "anomalies", "observations",
    "gate_events", "reads_raw",
]


@pytest.fixture(scope="module", autouse=True)
def cloud_is_running():
    try:
        with psycopg.connect(CLOUD_DSN, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM sync_cursor")
    except psycopg.Error as exc:
        pytest.skip(
            f"the cloud replica is not reachable ({exc.__class__.__name__}). "
            "Start it with:  docker compose up -d && "
            'DATABASE_URL="$CLOUD_DATABASE_URL" uv run alembic upgrade head'
        )
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "tasker-sync"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() != "true":
        pytest.skip("tasker-sync is not running. Start it with: docker compose up -d")


class Cloud:
    """A client of the cloud replica that reconnects when it comes back.

    These tests stop and kill the cloud container on purpose, which drops
    every connection to it — including this one. A real client reconnects,
    so this one does too; otherwise the test fails on its own plumbing
    rather than on the thing being tested.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._conn: psycopg.Connection | None = None

    def connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
        return self._conn

    def drop(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except psycopg.Error:
            pass
        self._conn = None

    def query(self, sql: str, params: tuple = ()):
        """One row, or None while the cloud is unreachable."""
        for last_attempt in (False, True):
            try:
                with self.connection().cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchone()
            except psycopg.Error:
                self.drop()
                if last_attempt:
                    return None
        return None

    def count(self, table: str, where: str = "TRUE", params: tuple = ()):
        row = self.query(f"SELECT count(*) FROM {table} WHERE {where}", params)
        return row[0] if row else None


@pytest.fixture
def cloud():
    client = Cloud(CLOUD_DSN)
    yield client
    client.drop()


def count(conn, table: str, where: str = "TRUE", params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE {where}", params)
        return cur.fetchone()[0]


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def stable(read, samples: int = 3, gap_s: float = 1.0) -> bool:
    """True when a number has stopped moving — the burst has all landed."""
    first = read()
    for _ in range(samples):
        time.sleep(gap_s)
        if read() != first:
            return False
    return True


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, capture_output=True, timeout=180
    )


def wait_for_cloud(cloud: "Cloud", table: str, where: str, params: tuple, expected: int, timeout=120):
    wait_until(
        lambda: cloud.count(table, where, params) == expected,
        f"{expected} row(s) in the cloud's {table}",
        timeout_s=timeout,
    )


def wait_until_caught_up(db, cloud: "Cloud", timeout_s=180):
    """Wait for every table to have the same number of rows in both."""
    def level():
        for table in SYNCED_TABLES:
            if count(db, table) != cloud.count(table):
                return False
        return True

    wait_until(level, "the cloud to catch up with the warehouse", timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# The ordinary case
# ---------------------------------------------------------------------------


def test_new_rows_reach_the_cloud(db, cloud):
    tid = a_tid("SYNCED")
    register(db, tid)
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "50")

    wait_for_cloud(cloud, "containers", "tid = %s", (tid,), 1)
    wait_for_cloud(cloud, "observations", "tid = %s", (tid,), 1)
    # Raw reads go across in id order, so this tag's reads arrive only once
    # everything written before them has. After a full test run that is a
    # backlog of a hundred thousand rows, which is exactly how a replica is
    # meant to behave -- it just takes longer than the tables above.
    wait_until(
        lambda: (cloud.count("reads_raw", "tid = %s", (tid,)) or 0) > 100,
        "this tag's raw reads to reach the cloud, behind whatever is queued",
        timeout_s=420,
    )

    assert cloud.query("SELECT status FROM containers WHERE tid = %s", (tid,))[0] == "IN_STOCK"


def test_a_status_change_reaches_the_cloud_not_just_the_first_write(db, cloud):
    """A container's status changes long after its row was created.

    An id high-water mark would never look at that row again, which is
    why the mutable tables are tracked by updated_at.
    """
    tid = a_tid("SYNCEDCHANGE")
    register(db, tid)
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "51")
    wait_for_cloud(cloud, "containers", "tid = %s AND status = 'IN_STOCK'", (tid,), 1)

    # Same row, new status.
    with db.cursor() as cur:
        cur.execute(
            """UPDATE containers SET status = 'DISPATCHED' WHERE tid = %s""", (tid,)
        )

    wait_for_cloud(cloud, "containers", "tid = %s AND status = 'DISPATCHED'", (tid,), 1)


def test_running_the_sync_again_changes_nothing(db, cloud):
    """Every write is an upsert, so a repeat overwrites with identical values."""
    tid = a_tid("SYNCIDEMPOTENT")
    register(db, tid)
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "52")
    wait_until_caught_up(db, cloud)

    before = {t: cloud.count(t) for t in SYNCED_TABLES}

    # Rewind every cursor to the beginning: the next pass re-sends
    # everything the cloud already has.
    cloud.query(
        "UPDATE sync_cursor SET last_id = 0, last_updated_at = '1970-01-01T00:00:00Z' "
        "RETURNING table_name"
    )
    time.sleep(25)
    wait_until_caught_up(db, cloud)

    after = {t: cloud.count(t) for t in SYNCED_TABLES}
    assert after == before, "re-syncing duplicated rows"


# ---------------------------------------------------------------------------
# The warehouse does not depend on the cloud
# ---------------------------------------------------------------------------


def test_the_warehouse_carries_on_with_the_cloud_switched_off(db, cloud):
    """SPEC.md 2.4. Local is the primary; the cloud is a copy that trails it."""
    compose("stop", "cloud")
    try:
        tid = a_tid("CLOUDDOWN")
        register(db, tid)
        # The whole pipeline, with nowhere to sync to.
        run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "53")

        with db.cursor() as cur:
            cur.execute("SELECT status FROM containers WHERE tid = %s", (tid,))
            assert cur.fetchone()[0] == "IN_STOCK", "the warehouse stopped working"
    finally:
        compose("start", "cloud")

    # And it catches up on its own once the cloud is back.
    wait_for_cloud(cloud, "containers", "tid = %s", (tid,), 1, timeout=180)


def test_the_sync_worker_never_writes_to_the_warehouse(db):
    """Its connection to the local database is read-only, on purpose.

    Nothing the cloud does can reach back and change the warehouse's own
    records — not even the sync worker's bookkeeping, which is why the
    cursor lives in the cloud.
    """
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM sync_cursor")
        assert cur.fetchone()[0] == 0, (
            "the local sync_cursor has rows in it, so something is keeping "
            "sync state in the warehouse database"
        )


# ---------------------------------------------------------------------------
# Losing the link mid-sync
# ---------------------------------------------------------------------------


def test_killing_the_link_mid_sync_loses_nothing(db, cloud):
    """Pull the cloud away twice while reads are still pouring in.

    The cursor is written to the cloud in the same transaction as the rows
    it covers, so there is no moment where it has moved past rows that did
    not land. A kill costs a retry, never a row.

    The kills happen while the simulator is still publishing, so the whole
    pipeline is in motion: ingest writing, the debouncer reading, and the
    sync worker shipping rows out from under itself.
    """
    wait_until_caught_up(db, cloud)
    started_with = count(db, "reads_raw")

    # Publish in the background so the interruptions land mid-flight.
    burst = subprocess.Popen(
        ["uv", "run", "sim", "burst", "--count", "20000", "--duration", "12",
         "--tags", "400", "--seed", "55"],
        cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_until(
            lambda: count(db, "reads_raw") > started_with + 2000,
            "the burst to start arriving",
        )
        interrupted_at = cloud.count("reads_raw")
        assert interrupted_at is not None
        assert interrupted_at < count(db, "reads_raw"), (
            "the cloud was already level; nothing would be interrupted"
        )

        # Cut it, twice, with the data still coming.
        for _ in range(2):
            compose("kill", "cloud")
            cloud.drop()
            time.sleep(3)
            assert cloud.count("reads_raw") is None, "the cloud is still answering"
            compose("start", "cloud")
            time.sleep(4)
    finally:
        burst.wait(timeout=300)
        compose("start", "cloud")

    # Let the warehouse finish taking everything in.
    wait_until(
        lambda: stable(lambda: count(db, "reads_raw")),
        "the warehouse to finish ingesting the burst",
        timeout_s=180,
    )
    local_reads = count(db, "reads_raw")
    assert local_reads >= started_with + 20000, "the burst did not all arrive locally"

    # The worker reconnects on its own and picks up where it left off.
    wait_until(
        lambda: cloud.count("reads_raw") == local_reads,
        f"all {local_reads} reads to reach the cloud after two outages",
        timeout_s=300,
    )

    # Nothing lost, and nothing sent twice.
    duplicates = cloud.query(
        "SELECT count(*) FROM (SELECT id FROM reads_raw GROUP BY id HAVING count(*) > 1) d"
    )[0]
    assert duplicates == 0, "a row was written twice"

    # And they are the same rows, not merely the same number of them.
    with db.cursor() as cur:
        cur.execute("SELECT min(id), max(id), sum(id) FROM reads_raw")
        local_shape = cur.fetchone()
    assert tuple(cloud.query("SELECT min(id), max(id), sum(id) FROM reads_raw")) == local_shape, (
        "the cloud holds different rows"
    )

    # Every other table caught up too.
    wait_until_caught_up(db, cloud)
