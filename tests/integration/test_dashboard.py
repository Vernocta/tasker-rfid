"""The five warehouse screens, and the API endpoints they lean on.

These check that each page is served, says plainly what it shows, and
that the data behind it is there. How it looks is a matter for eyes, not
assertions; what these catch is a screen that has stopped loading, or an
endpoint that changed shape underneath it.
"""

import os

import httpx
import pytest

from conftest import a_tid, open_session, register, run_and_settle

API = os.getenv("API_URL", "http://localhost:8000")
DASHBOARD = os.getenv("DASHBOARD_URL", "http://localhost:8080")

SCREENS = {
    "/": "Stock on hand",
    "/live": "Live read feed",
    "/dispatch": "Dispatch control",
    "/anomalies": "Anomaly queue",
    "/reports": "Consumption by customer",
}


@pytest.fixture(scope="module", autouse=True)
def dashboard_is_running():
    try:
        httpx.get(DASHBOARD + "/", timeout=5.0).raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        pytest.skip(
            f"the dashboard is not reachable at {DASHBOARD} "
            f"({exc.__class__.__name__}). Start it with:  docker compose up -d"
        )


@pytest.fixture
def web():
    with httpx.Client(base_url=DASHBOARD, timeout=30.0) as client:
        yield client


@pytest.fixture
def api():
    with httpx.Client(base_url=API, timeout=30.0) as client:
        yield client


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,heading", SCREENS.items())
def test_every_screen_is_served_and_names_itself(web, path, heading):
    response = web.get(path)
    assert response.status_code == 200
    assert heading in response.text


@pytest.mark.parametrize("path", SCREENS)
def test_every_screen_carries_the_nav_and_the_polling_clock(web, path):
    body = web.get(path).text
    for other in SCREENS:
        assert f'href="{other}"' in body
    assert 'id="updated"' in body, "no sign of whether the screen is live"


@pytest.mark.parametrize("path", SCREENS)
def test_every_screen_knows_where_the_api_is(web, path):
    body = web.get(path).text
    assert "window.TASKER" in body
    assert "apiBase" in body


def test_the_screens_ask_the_internet_for_nothing(web):
    """The dock screen must not lose its layout when the wifi drops.

    SPEC.md 2.4 treats the warehouse network as unreliable, which is why
    Postgres runs on the edge device. A screen that fetches its stylesheet
    and typefaces from a CDN would be the one part of the system that is
    not local-first.
    """
    for path in SCREENS:
        body = web.get(path).text
        for host in ("cdn.tailwindcss.com", "fonts.googleapis.com", "fonts.gstatic.com"):
            assert host not in body, f"{path} still loads from {host}"


def test_the_vendored_stylesheet_and_typefaces_are_served(web):
    tailwind = web.get("/static/vendor/tailwind-3.4.17.js")
    assert tailwind.status_code == 200
    assert len(tailwind.content) > 100_000, "that is not the Tailwind build"

    fonts = web.get("/static/vendor/fonts.css")
    assert fonts.status_code == 200
    assert "Archivo" in fonts.text and "Public Sans" in fonts.text
    assert "https://" not in fonts.text, "a font is still being fetched remotely"

    face = web.get("/static/vendor/fonts/Archivo-700-latin.woff2")
    assert face.status_code == 200
    assert face.content[:4] == b"wOF2", "that is not a woff2 file"


def test_the_shared_javascript_is_served(web):
    response = web.get("/static/app.js")
    assert response.status_code == 200
    assert "startPolling" in response.text


def test_screens_say_what_would_fill_them_when_empty(web):
    """SPEC-adjacent, but the brief is explicit: never just 'No data'."""
    assert "read at the entrance portal" in web.get("/").text
    assert "appears here within a few seconds" in web.get("/live").text
    assert "passed the exit" in web.get("/reports").text
    assert "cannot work out on its own" in web.get("/anomalies").text


# ---------------------------------------------------------------------------
# The endpoints the screens are built on
# ---------------------------------------------------------------------------


def test_the_live_feed_is_newest_read_first(db, api):
    """Sorted by when the tag was read, not by the order groups closed."""
    for n in range(2):
        tid = a_tid(f"FEEDORDER{n}")
        register(db, tid)
        run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "96")

    rows = api.get("/observations", params={"limit": 25}).json()
    times = [row["last_read"] for row in rows]
    assert times == sorted(times, reverse=True), "the feed is out of order"


def test_the_live_feed_identifies_what_a_tag_is(db, api):
    tid = a_tid("FEEDNAMED")
    api.post(
        "/containers",
        json={"tid": tid, "contents": [{"sku_id": "CONE-STD-120", "quantity": 24}]},
    ).raise_for_status()
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "97")

    row = next(r for r in api.get("/observations", params={"limit": 50}).json() if r["tid"] == tid)
    assert row["contents"] == "Standard sugar cone 120mm x24"
    assert row["status"] == "IN_STOCK"


def test_an_unregistered_tag_shows_as_unidentified(db, api):
    tid = a_tid("FEEDGHOST")
    run_and_settle(db, tid, "box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "98")

    row = next(r for r in api.get("/observations", params={"limit": 50}).json() if r["tid"] == tid)
    assert row["container_id"] is None
    assert row["contents"] is None


def test_the_customer_picker_has_the_seeded_customers(api):
    names = {c["name"] for c in api.get("/customers").json()}
    assert "Heladeria Del Sol" in names


def test_the_dispatch_screen_can_tell_whether_the_dock_is_open(db, api):
    assert api.get("/dispatch-sessions/open").json() is None
    session_id = open_session(db, customer_id="CUST-0001")
    assert api.get("/dispatch-sessions/open").json()["session_id"] == session_id


def test_the_report_accepts_a_date_range(api):
    assert api.get("/reports/consumption", params={"from_date": "2020-01-01", "to_date": "2020-03-01"}).json() == []
    backwards = api.get("/reports/consumption", params={"from_date": "2026-09-01", "to_date": "2026-08-01"})
    assert backwards.status_code == 422
