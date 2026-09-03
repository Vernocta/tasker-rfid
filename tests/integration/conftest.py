"""Shared machinery for the failure-mode tests.

These tests drive the whole system the way the warehouse will: publish
tag reads with the simulator, and let ingest, the debouncer and the state
engine do their work. Nothing is stubbed. That means the stack has to be
running:

    docker compose up -d
    uv run alembic upgrade head

If it is not, every test here skips with a message saying so rather than
failing for the wrong reason.

Tests never truncate anything. reads_raw is append-only by design
(SPEC.md section 3), so each test invents its own tag ids and asserts only
on its own rows. That also means the tests exercise the same tables that
have real history in them, which is closer to reality than a clean slate.
"""

import os
import subprocess
import sys
import time
import uuid

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DSN = "postgresql://tasker:tasker@localhost:5432/tasker"

# How long to wait for a read to travel MQTT -> reads_raw -> observations
# -> movements. The debouncer alone holds a group for the 2 second quiet
# period, so this has to be comfortably longer than that.
PIPELINE_TIMEOUT_S = 45.0
POLL_S = 0.25


def dsn() -> str:
    url = os.getenv("DATABASE_URL", DEFAULT_DSN)
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session", autouse=True)
def stack_is_running():
    """Skip everything, with an explanation, if the stack is not up."""
    try:
        with psycopg.connect(dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM observations")
    except psycopg.Error as exc:
        pytest.skip(
            f"the stack is not running ({exc.__class__.__name__}). "
            "Start it with:  docker compose up -d && uv run alembic upgrade head",
            allow_module_level=True,
        )

    for service in ("tasker-ingest", "tasker-debouncer", "tasker-state-engine"):
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", service],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() != "true":
            pytest.skip(
                f"{service} is not running. Start it with:  docker compose up -d",
                allow_module_level=True,
            )


@pytest.fixture
def db():
    with psycopg.connect(dsn(), autocommit=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def no_session_left_open(db):
    """Every test decides for itself whether a dispatch session is open.

    Sessions are warehouse-wide, so one test leaving one open would change
    what the next test is testing.
    """
    close_all_sessions(db)
    yield
    close_all_sessions(db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def a_tid(label: str) -> str:
    """A tag id unique to this test run, so tests never collide."""
    return f"TEST-{label}-{uuid.uuid4().hex[:10].upper()}"


def register(
    db,
    tid: str,
    *,
    kind: str = "BOX",
    status: str = "REGISTERED",
    reusable: bool = False,
    parent_id: int | None = None,
) -> int:
    """Put a container in the database, the way the API will in step 7."""
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO containers (tid, kind, status, reusable, parent_id)
               VALUES (%s, %s, %s, %s, %s) RETURNING container_id""",
            (tid, kind, status, reusable, parent_id),
        )
        return cur.fetchone()[0]


def open_session(db, customer_id: str = "CUST-0001", order_ref: str = "TEST-ORDER") -> int:
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO dispatch_sessions (customer_id, order_ref, operator)
               VALUES (%s, %s, 'test') RETURNING session_id""",
            (customer_id, order_ref),
        )
        return cur.fetchone()[0]


def close_all_sessions(db) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE dispatch_sessions SET closed_at = now() WHERE closed_at IS NULL"
        )


def sim(*args: str) -> None:
    """Run the simulator, exactly as an operator would from the terminal."""
    result = subprocess.run(
        [sys.executable, "-m", "tasker_rfid.services.simulator.cli", *args],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"sim {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )


def status_of(db, tid: str) -> str | None:
    with db.cursor() as cur:
        cur.execute("SELECT status FROM containers WHERE tid = %s", (tid,))
        row = cur.fetchone()
        return row[0] if row else None


def movements_for(db, tid: str) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(
            """SELECT m.from_status, m.to_status, m.portal, m.session_id
               FROM movements m JOIN containers c USING (container_id)
               WHERE c.tid = %s ORDER BY m.occurred_at, m.id""",
            (tid,),
        )
        return cur.fetchall()


def anomalies_for(db, tid: str) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT kind, detail FROM anomalies WHERE tid = %s ORDER BY id", (tid,)
        )
        return cur.fetchall()


def raw_read_count(db, tid: str) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM reads_raw WHERE tid = %s", (tid,))
        return cur.fetchone()[0]


def observation_count(db, tid: str) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM observations WHERE tid = %s", (tid,))
        return cur.fetchone()[0]


def wait_until(condition, description: str, timeout_s: float = PIPELINE_TIMEOUT_S):
    """Poll until the pipeline has caught up, or explain what never happened."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = condition()
        if last:
            return last
        time.sleep(POLL_S)
    raise AssertionError(
        f"timed out after {timeout_s:.0f}s waiting for {description}. Last value: {last!r}"
    )


def wait_for_settled(db, tid: str, description: str = "the pipeline to settle") -> None:
    """Wait until every observation of this tag has been through the state engine."""

    def done():
        with db.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM observations WHERE tid = %s AND processed = FALSE",
                (tid,),
            )
            unprocessed = cur.fetchone()[0]
        return observation_count(db, tid) > 0 and unprocessed == 0

    wait_until(done, description)
    # The state engine commits per observation; give a beat for the last one.
    time.sleep(0.5)
