"""Every endpoint in SPEC.md section 6, against the running API.

These call the real HTTP service over the network, not a test client, so
they catch the things a test client hides: SQL that only fails against
Postgres, serialisation, status codes.

Run them with the stack up:

    docker compose up -d
    uv run pytest tests/integration -v
"""

import os

import httpx
import pytest

from conftest import a_tid, open_session, run_and_settle

API = os.getenv("API_URL", "http://localhost:8000")
TIMEOUT = 30.0


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
    with httpx.Client(base_url=API, timeout=TIMEOUT) as client:
        yield client


# ---------------------------------------------------------------------------
# The interactive documentation the whole endpoint list hangs off
# ---------------------------------------------------------------------------


def test_the_docs_page_is_served(api):
    assert api.get("/docs").status_code == 200


def test_every_endpoint_in_the_spec_is_published(api):
    """SPEC.md section 6, endpoint for endpoint."""
    paths = api.get("/openapi.json").json()["paths"]
    expected = {
        ("/stock", "get"),
        ("/stock/{sku_id}", "get"),
        ("/containers/{tid}", "get"),
        ("/containers", "post"),
        ("/containers/{tid}/children", "post"),
        ("/dispatch-sessions", "post"),
        ("/dispatch-sessions/{session_id}/close", "post"),
        ("/dispatch-sessions/{session_id}", "get"),
        ("/cycle-counts", "post"),
        ("/cycle-counts/{cycle_id}/scan", "post"),
        ("/cycle-counts/{cycle_id}/close", "post"),
        ("/anomalies", "get"),
        ("/anomalies/{anomaly_id}/resolve", "post"),
        ("/reports/consumption", "get"),
        ("/health", "get"),
    }
    published = {(path, method) for path, methods in paths.items() for method in methods}
    assert expected <= published, f"missing: {sorted(expected - published)}"


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


def test_registering_a_container_starts_it_registered(api):
    """The API never sets status; the schema default applies."""
    tid = a_tid("APIREG")
    created = api.post(
        "/containers",
        json={"tid": tid, "kind": "BOX", "contents": [{"sku_id": "CONE-STD-120", "quantity": 24}]},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "REGISTERED"

    detail = api.get(f"/containers/{tid}").json()
    assert detail["contents"][0]["quantity"] == 24
    assert detail["movements"] == []


def test_a_duplicate_tid_is_refused_with_an_explanation(api):
    tid = a_tid("APIDUP")
    api.post("/containers", json={"tid": tid}).raise_for_status()
    again = api.post("/containers", json={"tid": tid})
    assert again.status_code == 409
    assert tid in again.json()["detail"]


def test_an_unknown_sku_is_refused(api):
    response = api.post(
        "/containers",
        json={"tid": a_tid("APIBADSKU"), "contents": [{"sku_id": "NO-SUCH-SKU", "quantity": 1}]},
    )
    assert response.status_code == 422
    assert "seeds/skus.csv" in response.json()["detail"]


def test_an_unknown_container_is_a_clean_404(api):
    assert api.get("/containers/NOT-A-REAL-TID").status_code == 404


def test_building_a_pallet_attaches_boxes(api):
    pallet_tid = a_tid("APIPAL")
    box_tids = [a_tid(f"APIONPAL{n}") for n in range(2)]
    api.post("/containers", json={"tid": pallet_tid, "kind": "PALLET", "reusable": True}).raise_for_status()
    for box_tid in box_tids:
        api.post("/containers", json={"tid": box_tid}).raise_for_status()

    detail = api.post(f"/containers/{pallet_tid}/children", json={"child_tids": box_tids})
    assert detail.status_code == 200
    assert {c["tid"] for c in detail.json()["children"]} == set(box_tids)


def test_a_box_cannot_be_on_two_pallets_at_once(api):
    first, second = a_tid("APIPALA"), a_tid("APIPALB")
    box_tid = a_tid("APICONTESTED")
    for tid in (first, second):
        api.post("/containers", json={"tid": tid, "kind": "PALLET", "reusable": True}).raise_for_status()
    api.post("/containers", json={"tid": box_tid}).raise_for_status()

    api.post(f"/containers/{first}/children", json={"child_tids": [box_tid]}).raise_for_status()
    clash = api.post(f"/containers/{second}/children", json={"child_tids": [box_tid]})
    assert clash.status_code == 409


# ---------------------------------------------------------------------------
# Stock, and SPEC.md section 3.1
# ---------------------------------------------------------------------------


def test_stock_counts_boxes_that_have_come_through_the_entrance(db, api):
    tid = a_tid("APISTOCK")
    api.post(
        "/containers",
        json={"tid": tid, "contents": [{"sku_id": "PWD-VAN-1KG", "quantity": 7, "lot": "L-API"}]},
    ).raise_for_status()

    before = {r["sku_id"]: r["boxes"] for r in api.get("/stock").json()}
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "80")

    after = {r["sku_id"]: r["boxes"] for r in api.get("/stock").json()}
    assert after.get("PWD-VAN-1KG", 0) == before.get("PWD-VAN-1KG", 0) + 7

    holders = api.get("/stock/PWD-VAN-1KG").json()
    assert tid in {h["tid"] for h in holders}


def test_stock_on_a_pallet_is_still_stock(db, api):
    """Regression: the old parent_id filter hid stock that was on a pallet."""
    pallet_tid = a_tid("APISTOCKPAL")
    box_tid = a_tid("APISTOCKBOX")
    api.post("/containers", json={"tid": pallet_tid, "kind": "PALLET", "reusable": True}).raise_for_status()
    api.post(
        "/containers",
        json={"tid": box_tid, "parent_tid": pallet_tid,
              "contents": [{"sku_id": "SAUCE-CHOC-1L", "quantity": 5}]},
    ).raise_for_status()

    before = {r["sku_id"]: r["boxes"] for r in api.get("/stock").json()}
    run_and_settle(db, box_tid, "pallet", "--tid", pallet_tid, "--box-tid", box_tid, "--portal", "ENTRANCE", "--seed", "81")

    after = {r["sku_id"]: r["boxes"] for r in api.get("/stock").json()}
    assert after.get("SAUCE-CHOC-1L", 0) == before.get("SAUCE-CHOC-1L", 0) + 5


def test_stock_for_an_unknown_sku_is_a_clean_404(api):
    assert api.get("/stock/NO-SUCH-SKU").status_code == 404


# ---------------------------------------------------------------------------
# Dispatch sessions
# ---------------------------------------------------------------------------


def test_a_session_attributes_what_goes_out(db, api):
    tid = a_tid("APIDISPATCH")
    api.post(
        "/containers",
        json={"tid": tid, "contents": [{"sku_id": "CUP-PAPER-150", "quantity": 12}]},
    ).raise_for_status()
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "82")

    session = api.post(
        "/dispatch-sessions",
        json={"customer_id": "CUST-0002", "order_ref": "SO-API", "operator": "tester"},
    )
    assert session.status_code == 201
    session_id = session.json()["session_id"]

    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "EXIT", "--seed", "83")

    detail = api.get(f"/dispatch-sessions/{session_id}").json()
    assert tid in {c["tid"] for c in detail["containers"]}
    assert any(t["sku_id"] == "CUP-PAPER-150" and t["boxes"] == 12 for t in detail["totals_by_sku"])

    closed = api.post(f"/dispatch-sessions/{session_id}/close")
    assert closed.status_code == 200
    assert closed.json()["session"]["closed_at"] is not None

    assert api.post(f"/dispatch-sessions/{session_id}/close").status_code == 409


def test_only_one_session_may_be_open(db, api):
    """Two open sessions would leave a departing load with no clear destination."""
    open_session(db, customer_id="CUST-0001")
    clash = api.post("/dispatch-sessions", json={"customer_id": "CUST-0003"})
    assert clash.status_code == 409
    assert "Close it before opening another" in clash.json()["detail"]


def test_an_unknown_customer_is_refused(db, api):
    response = api.post("/dispatch-sessions", json={"customer_id": "CUST-NOPE"})
    assert response.status_code == 422
    assert "seeds/customers.csv" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Cycle counts
# ---------------------------------------------------------------------------


def test_a_cycle_count_reports_what_is_missing_and_what_is_unexpected(db, api):
    present_tid = a_tid("APICOUNTED")
    absent_tid = a_tid("APIMISSING")
    for tid in (present_tid, absent_tid):
        api.post("/containers", json={"tid": tid}).raise_for_status()
        run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "84")

    ghost_tid = a_tid("APIGHOST")
    cycle_id = api.post("/cycle-counts", json={"operator": "tester"}).json()["cycle_id"]

    api.post(f"/cycle-counts/{cycle_id}/scan", json={"tid": present_tid}).raise_for_status()
    # Scanning twice is harmless: a tag is either found or it is not.
    api.post(f"/cycle-counts/{cycle_id}/scan", json={"tid": present_tid}).raise_for_status()
    api.post(f"/cycle-counts/{cycle_id}/scan", json={"tid": ghost_tid}).raise_for_status()

    report = api.post(f"/cycle-counts/{cycle_id}/close").json()
    assert absent_tid in {m["tid"] for m in report["missing"]}
    assert ghost_tid in {u["tid"] for u in report["unexpected"]}
    assert report["anomalies_raised"] >= 2

    # A closed count takes no more scans.
    assert api.post(f"/cycle-counts/{cycle_id}/scan", json={"tid": present_tid}).status_code == 409
    assert api.post(f"/cycle-counts/{cycle_id}/close").status_code == 409

    raised = api.get("/anomalies", params={"kind": "COUNT_MISMATCH", "limit": 200}).json()
    assert absent_tid in {a["tid"] for a in raised}


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------


def test_the_anomaly_queue_lists_filters_and_resolves(db, api):
    """Regression: filtering by kind once returned a 500, not a filtered list."""
    tid = a_tid("APIANOM")
    # An exit read for a tag nobody registered: UNKNOWN_TID.
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "85")

    queue = api.get("/anomalies", params={"resolved": False, "limit": 200})
    assert queue.status_code == 200, queue.text
    assert tid in {a["tid"] for a in queue.json()}

    filtered = api.get("/anomalies", params={"kind": "UNKNOWN_TID", "limit": 200})
    assert filtered.status_code == 200, filtered.text
    assert all(a["kind"] == "UNKNOWN_TID" for a in filtered.json())

    assert api.get("/anomalies", params={"kind": "NOT_A_KIND"}).status_code == 422

    anomaly_id = next(a["id"] for a in queue.json() if a["tid"] == tid)
    resolved = api.post(
        f"/anomalies/{anomaly_id}/resolve",
        json={"resolved_by": "tester", "note": "Tag belongs to a supplier crate."},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["resolved"] is True
    assert body["resolved_by"] == "tester"
    assert body["detail"]["resolution_note"] == "Tag belongs to a supplier crate."

    # Resolving twice is refused rather than silently overwriting who did it.
    assert api.post(
        f"/anomalies/{anomaly_id}/resolve", json={"resolved_by": "someone else"}
    ).status_code == 409


# ---------------------------------------------------------------------------
# Reports and health
# ---------------------------------------------------------------------------


def test_consumption_reports_boxes_per_customer_per_sku(db, api):
    tid = a_tid("APICONSUMPTION")
    api.post(
        "/containers",
        json={"tid": tid, "contents": [{"sku_id": "CONE-STD-120", "quantity": 30}]},
    ).raise_for_status()
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "86")

    def cones_for(customer):
        return next(
            (
                r["boxes"]
                for r in api.get("/reports/consumption", params={"days": 90}).json()
                if r["customer"] == customer and r["sku"] == "Standard sugar cone 120mm"
            ),
            0,
        )

    before = cones_for("Kiosco Palermo")
    session_id = api.post("/dispatch-sessions", json={"customer_id": "CUST-0003"}).json()["session_id"]
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "EXIT", "--seed", "87")
    api.post(f"/dispatch-sessions/{session_id}/close").raise_for_status()

    assert cones_for("Kiosco Palermo") == before + 30


def test_consumption_window_is_a_parameter(api):
    assert api.get("/reports/consumption", params={"days": 1}).status_code == 200
    assert api.get("/reports/consumption", params={"days": 0}).status_code == 422


def test_health_reports_readers_and_queue_depth(api):
    body = api.get("/health").json()
    assert body["status"] in {"ok", "degraded"}
    assert body["database"] == "ok"
    assert {"reads_awaiting_debounce", "observations_awaiting_state_engine"} <= set(
        body["queue_depth"]
    )
    assert body["no_read_alert_hours"] == 4
    for reader in body["readers"]:
        assert reader["reader_id"].startswith("READER-")
