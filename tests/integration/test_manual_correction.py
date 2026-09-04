"""Putting things right by hand, and the three failure modes that need it.

SPEC.md section 7 leaves three rows to a person and a cycle count:

- **Reader offline** — /health raises it
- **Container missed at portal** — cycle count finds it, a correction fixes it
- **Tag destroyed** — cycle count finds it, the container is re-registered

All three end at POST /containers/{tid}/correct, the one status change
that does not come from a portal read.
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import a_tid, register, run_and_settle

API = os.getenv("API_URL", "http://localhost:8000")


@pytest.fixture(scope="module", autouse=True)
def api_is_running():
    try:
        httpx.get(f"{API}/health", timeout=5.0).raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        pytest.skip(
            f"the API is not reachable at {API} ({exc.__class__.__name__}). "
            "Start it with:  docker compose up -d"
        )


@pytest.fixture
def api():
    with httpx.Client(base_url=API, timeout=30.0) as client:
        yield client


def status_via_api(api, tid: str) -> str:
    return api.get(f"/containers/{tid}").json()["container"]["status"]


def movements_via_api(api, tid: str) -> list[dict]:
    return api.get(f"/containers/{tid}").json()["movements"]


# ---------------------------------------------------------------------------
# The correction endpoint itself
# ---------------------------------------------------------------------------


def test_a_correction_changes_status_and_says_who_and_why(db, api):
    tid = a_tid("CORRECT")
    register(db, tid, status="IN_STOCK")

    response = api.post(
        f"/containers/{tid}/correct",
        json={
            "to_status": "DISPATCHED",
            "reason": "Went out on SO-99312; tag not read at the exit.",
            "operator": "mlopez",
        },
    )
    assert response.status_code == 200
    assert status_via_api(api, tid) == "DISPATCHED"

    movements = movements_via_api(api, tid)
    assert len(movements) == 1
    assert movements[0]["from_status"] == "IN_STOCK"
    assert movements[0]["to_status"] == "DISPATCHED"
    assert movements[0]["source"] == "MANUAL", "a typed change must not look like a read"
    assert movements[0]["portal"] is None

    with db.cursor() as cur:
        cur.execute(
            """SELECT m.reason, m.operator FROM movements m
               JOIN containers c USING (container_id) WHERE c.tid = %s""",
            (tid,),
        )
        reason, operator = cur.fetchone()
    assert "SO-99312" in reason
    assert operator == "mlopez"


def test_a_correction_without_a_reason_is_refused(db, api):
    tid = a_tid("NOREASON")
    register(db, tid, status="IN_STOCK")
    assert api.post(
        f"/containers/{tid}/correct",
        json={"to_status": "DISPATCHED", "reason": "", "operator": "mlopez"},
    ).status_code == 422
    assert status_via_api(api, tid) == "IN_STOCK"


def test_a_correction_that_changes_nothing_is_refused(db, api):
    tid = a_tid("SAMESTATUS")
    register(db, tid, status="IN_STOCK")
    response = api.post(
        f"/containers/{tid}/correct",
        json={"to_status": "IN_STOCK", "reason": "no change", "operator": "mlopez"},
    )
    assert response.status_code == 409
    assert "already IN_STOCK" in response.json()["detail"]


def test_a_correction_to_an_invented_status_is_refused(db, api):
    tid = a_tid("BADSTATUS")
    register(db, tid, status="IN_STOCK")
    assert api.post(
        f"/containers/{tid}/correct",
        json={"to_status": "SOLD", "reason": "nope", "operator": "mlopez"},
    ).status_code == 409


def test_correcting_a_pallet_leaves_its_boxes_alone(db, api):
    """A targeted decision about one container, not a cascade."""
    pallet_tid = a_tid("CORRECTPAL")
    pallet_id = register(db, pallet_tid, kind="PALLET", status="IN_STOCK", reusable=True)
    box_tids = [a_tid(f"CORRECTBOX{n}") for n in range(3)]
    for box_tid in box_tids:
        register(db, box_tid, status="IN_STOCK", parent_id=pallet_id)

    api.post(
        f"/containers/{pallet_tid}/correct",
        json={"to_status": "DISPATCHED", "reason": "Pallet shipped separately.", "operator": "mlopez"},
    ).raise_for_status()

    assert status_via_api(api, pallet_tid) == "DISPATCHED"
    for box_tid in box_tids:
        assert status_via_api(api, box_tid) == "IN_STOCK"


def test_correcting_an_unknown_container_is_a_clean_404(api):
    assert api.post(
        "/containers/NOT-A-REAL-TID/correct",
        json={"to_status": "IN_STOCK", "reason": "x y z", "operator": "mlopez"},
    ).status_code == 404


# ---------------------------------------------------------------------------
# SPEC.md section 7: "Carried back out the entrance -> ILLEGAL_TRANSITION
# anomaly + manual correction". Resolving the anomaly does the correction.
# ---------------------------------------------------------------------------


def test_resolving_an_anomaly_can_put_the_container_right(db, api):
    tid = a_tid("RESOLVEFIX")
    register(db, tid, status="DISPATCHED")

    # A dispatched box read at the entrance: ILLEGAL_TRANSITION.
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "90")
    assert status_via_api(api, tid) == "DISPATCHED"

    queue = api.get("/anomalies", params={"resolved": False, "limit": 200}).json()
    anomaly = next(a for a in queue if a["tid"] == tid)
    assert anomaly["kind"] == "ILLEGAL_TRANSITION"

    # A person looks: the customer really did send it back.
    resolved = api.post(
        f"/anomalies/{anomaly['id']}/resolve",
        json={
            "resolved_by": "mlopez",
            "note": "Customer returned it; inspected and put back on the shelf.",
            "correct_to_status": "IN_STOCK",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["detail"]["corrected_from"] == "DISPATCHED"
    assert resolved.json()["detail"]["corrected_to"] == "IN_STOCK"

    assert status_via_api(api, tid) == "IN_STOCK"
    manual = [m for m in movements_via_api(api, tid) if m["source"] == "MANUAL"]
    assert len(manual) == 1


def test_an_anomaly_can_be_closed_without_touching_stock(db, api):
    """The default. Most dispositions are 'looked at it, nothing to change'."""
    tid = a_tid("RESOLVEONLY")
    register(db, tid, status="DISPATCHED")
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "91")

    queue = api.get("/anomalies", params={"resolved": False, "limit": 200}).json()
    anomaly = next(a for a in queue if a["tid"] == tid)
    api.post(
        f"/anomalies/{anomaly['id']}/resolve",
        json={"resolved_by": "mlopez", "note": "Duplicate scan by a forklift driver."},
    ).raise_for_status()

    assert status_via_api(api, tid) == "DISPATCHED"
    assert movements_via_api(api, tid) == []


def test_an_unknown_tag_anomaly_cannot_be_corrected(db, api):
    """UNKNOWN_TID has no container, so there is no status to set."""
    tid = a_tid("GHOSTFIX")
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "92")

    queue = api.get("/anomalies", params={"resolved": False, "limit": 200}).json()
    anomaly = next(a for a in queue if a["tid"] == tid)
    assert anomaly["kind"] == "UNKNOWN_TID"

    response = api.post(
        f"/anomalies/{anomaly['id']}/resolve",
        json={"resolved_by": "mlopez", "note": "Supplier crate.", "correct_to_status": "IN_STOCK"},
    )
    assert response.status_code == 422
    assert "Register it first" in response.json()["detail"]


# ---------------------------------------------------------------------------
# SPEC.md section 7: "Reader offline | /health; alert if no read in 4 h
# during working hours"
# ---------------------------------------------------------------------------


def test_health_raises_a_reader_that_has_gone_quiet(db, api):
    """A reader that has quietly died is the most dangerous failure there is.

    Everything downstream keeps working perfectly on a stream of nothing,
    so the only thing that catches it is noticing the silence.
    """
    reader_id = f"READER-OFFLINE-{a_tid('R')[-8:]}"
    long_ago = datetime.now(timezone.utc) - timedelta(hours=9)
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO reads_raw (tid, reader_id, antenna_id, rssi, read_at)
               VALUES (%s, %s, 1, -50, %s)""",
            (a_tid("QUIET"), reader_id, long_ago),
        )

    body = api.get("/health").json()
    reader = next(r for r in body["readers"] if r["reader_id"] == reader_id)
    assert reader["minutes_since_last_read"] > 4 * 60

    # SPEC.md section 7 alerts only during working hours: outside them the
    # warehouse is shut and silence is exactly what a healthy reader does.
    if body["within_working_hours"]:
        assert reader["healthy"] is False
        assert body["status"] == "degraded"
        assert any(reader_id in w for w in body["warnings"])
    else:
        assert reader["healthy"] is True


def test_health_leaves_a_working_reader_alone(db, api):
    reader_id = f"READER-BUSY-{a_tid('R')[-8:]}"
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO reads_raw (tid, reader_id, antenna_id, rssi, read_at)
               VALUES (%s, %s, 1, -50, now())""",
            (a_tid("BUSY"), reader_id),
        )

    body = api.get("/health").json()
    reader = next(r for r in body["readers"] if r["reader_id"] == reader_id)
    assert reader["healthy"] is True
    assert reader["minutes_since_last_read"] < 5


# ---------------------------------------------------------------------------
# SPEC.md section 7: "Container missed at portal | Cycle count reconciliation"
# ---------------------------------------------------------------------------


def test_a_container_missed_at_the_exit_is_found_by_a_cycle_count(db, api):
    """It left on the truck, but no reader saw it. The books say otherwise.

    Nothing in the read pipeline can catch this — there is no read to
    process. The floor count is what finds it, and a correction squares
    the books.
    """
    shipped_tid = a_tid("MISSEDATEXIT")
    on_shelf_tid = a_tid("STILLHERE")
    for tid in (shipped_tid, on_shelf_tid):
        api.post(
            "/containers",
            json={"tid": tid, "contents": [{"sku_id": "CONE-STD-120", "quantity": 24}]},
        ).raise_for_status()
        run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "93")
        assert status_via_api(api, tid) == "IN_STOCK"

    # Count the aisle. The shipped box is not on the floor to be scanned.
    cycle_id = api.post("/cycle-counts", json={"operator": "mlopez"}).json()["cycle_id"]
    api.post(f"/cycle-counts/{cycle_id}/scan", json={"tid": on_shelf_tid}).raise_for_status()
    report = api.post(f"/cycle-counts/{cycle_id}/close").json()

    assert shipped_tid in {m["tid"] for m in report["missing"]}
    assert on_shelf_tid not in {m["tid"] for m in report["missing"]}

    variance = next(
        a
        for a in api.get("/anomalies", params={"kind": "COUNT_MISMATCH", "limit": 500}).json()
        if a["tid"] == shipped_tid
    )
    assert variance["detail"]["variance"] == "missing"

    # The paperwork says it shipped, so square the books.
    api.post(
        f"/anomalies/{variance['id']}/resolve",
        json={
            "resolved_by": "mlopez",
            "note": "Shipped on SO-99312; missed at the exit portal.",
            "correct_to_status": "DISPATCHED",
        },
    ).raise_for_status()

    assert status_via_api(api, shipped_tid) == "DISPATCHED"
    assert status_via_api(api, on_shelf_tid) == "IN_STOCK"

    # And it is out of the stock figures.
    holders = api.get("/stock/CONE-STD-120").json()
    assert shipped_tid not in {h["tid"] for h in holders}
    assert on_shelf_tid in {h["tid"] for h in holders}


# ---------------------------------------------------------------------------
# SPEC.md section 7: "Tag destroyed | Cycle count catches it; re-register"
# ---------------------------------------------------------------------------


def test_a_destroyed_tag_is_caught_by_a_cycle_count_and_re_registered(db, api):
    """The box is on the shelf. Its tag is dead, so nothing can read it.

    The container looks present to the system and invisible to every
    reader. A cycle count is the only thing that notices, and the fix is
    a new tag: SPEC.md 2.1 makes the TID the primary key, so a new chip
    means a new container record, and the old one is retired by hand.
    """
    dead_tag = a_tid("DEADTAG")
    api.post(
        "/containers",
        json={"tid": dead_tag, "contents": [{"sku_id": "PWD-VAN-1KG", "quantity": 10}]},
    ).raise_for_status()
    run_and_settle(db, dead_tag, "box", "--tid", dead_tag, "--portal", "ENTRANCE", "--seed", "94")
    assert status_via_api(api, dead_tag) == "IN_STOCK"

    # From here the tag answers nothing: no simulator command, ever again.
    cycle_id = api.post("/cycle-counts", json={"operator": "mlopez", "notes": "aisle 4"}).json()["cycle_id"]
    report = api.post(f"/cycle-counts/{cycle_id}/close").json()
    assert dead_tag in {m["tid"] for m in report["missing"]}

    # The box is physically there, so a new tag goes on it and it is
    # registered afresh. Nothing is ever written to a tag (SPEC.md 2.1),
    # so re-tagging is only ever a new record.
    replacement_tag = a_tid("NEWTAG")
    api.post(
        "/containers",
        json={"tid": replacement_tag, "contents": [{"sku_id": "PWD-VAN-1KG", "quantity": 10}]},
    ).raise_for_status()
    run_and_settle(db, replacement_tag, "box", "--tid", replacement_tag, "--portal", "ENTRANCE", "--seed", "95")
    assert status_via_api(api, replacement_tag) == "IN_STOCK"

    # The dead record is retired by hand, so the stock is not counted twice.
    variance = next(
        a
        for a in api.get("/anomalies", params={"kind": "COUNT_MISMATCH", "limit": 500}).json()
        if a["tid"] == dead_tag
    )
    api.post(
        f"/anomalies/{variance['id']}/resolve",
        json={
            "resolved_by": "mlopez",
            "note": f"Tag destroyed. Re-tagged as {replacement_tag}; this record retired.",
            "correct_to_status": "DISPATCHED",
        },
    ).raise_for_status()

    holders = {h["tid"] for h in api.get("/stock/PWD-VAN-1KG").json()}
    assert replacement_tag in holders
    assert dead_tag not in holders, "the same physical box was counted twice"
