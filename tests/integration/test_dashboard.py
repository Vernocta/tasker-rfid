"""The five warehouse screens, and the API endpoints they lean on.

These check that each page is served, says plainly what it shows, and
that the data behind it is there. How it looks is a matter for eyes, not
assertions; what these catch is a screen that has stopped loading, or an
endpoint that changed shape underneath it.
"""

import json
import os

import httpx
import pytest

from tasker_rfid.web.text import LANGUAGES, TEXT, strings_for

from conftest import a_tid, open_session, register, run_and_settle

API = os.getenv("API_URL", "http://localhost:8000")
DASHBOARD = os.getenv("DASHBOARD_URL", "http://localhost:8080")

# path -> the key of the heading that screen shows. The words themselves
# live in web/text.py, in both languages, so the tests read them from there
# rather than repeating them and drifting.
SCREENS = {
    "/": "stock.title",
    "/live": "live.title",
    "/dispatch": "dispatch.title",
    "/anomalies": "anom.title",
    "/reports": "rep.title",
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


@pytest.mark.parametrize("path,heading_key", SCREENS.items())
@pytest.mark.parametrize("language", LANGUAGES)
def test_every_screen_is_served_and_names_itself(web, path, heading_key, language):
    response = web.get(path, params={"lang": language})
    assert response.status_code == 200
    assert TEXT[heading_key][language] in response.text


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


@pytest.mark.parametrize("language", LANGUAGES)
def test_screens_say_what_would_fill_them_when_empty(web, language):
    """The brief is explicit: never just 'No data'.

    The empty-state sentences are written into the page for the browser to
    use, so they can be checked in the served HTML.
    """
    text = strings_for(language)
    for path, key in (
        ("/", "stock.empty"),
        ("/live", "live.empty"),
        ("/reports", "rep.empty"),
        ("/anomalies", "anom.empty"),
        ("/dispatch", "dispatch.empty"),
    ):
        body = web.get(path, params={"lang": language}).text
        # The phrases are handed to the browser as JSON, so an accented
        # character reaches the page escaped: "acá" arrives as "ac\u00e1".
        # Compare against the same encoding the browser receives.
        as_delivered = json.dumps(text[key], ensure_ascii=True)[1:-1]
        assert as_delivered[:40] in body, (
            f"{path} does not say what would fill it in {language}"
        )


# ---------------------------------------------------------------------------
# Both languages
# ---------------------------------------------------------------------------


def test_neither_language_is_missing_a_phrase():
    """One phrase per entry, both languages side by side, so a gap shows."""
    for key, phrases in TEXT.items():
        for language in LANGUAGES:
            assert phrases.get(language), f"{key} has no {language} text"


def test_the_default_is_spanish(web):
    """The warehouse is in Buenos Aires."""
    body = web.get("/dispatch").text
    assert TEXT["dispatch.closed"]["es"] in body
    assert 'lang="es"' in body


def test_the_language_switch_sticks(web):
    """Chosen once on the screen by the dock, not per visit."""
    switched = web.get("/language/en", params={"back": "/dispatch"}, follow_redirects=False)
    assert switched.status_code == 303
    assert switched.headers["location"] == "/dispatch"
    assert "tasker_language=en" in switched.headers["set-cookie"]

    # The client keeps the cookie, so the next page comes back in English.
    body = web.get("/dispatch").text
    assert TEXT["dispatch.closed"]["en"] in body
    assert 'lang="en"' in body

    web.get("/language/es", params={"back": "/dispatch"}, follow_redirects=False)
    assert TEXT["dispatch.closed"]["es"] in web.get("/dispatch").text


def test_an_unknown_language_falls_back_rather_than_half_translating(web):
    body = web.get("/dispatch", params={"lang": "fr"}).text
    assert TEXT["dispatch.closed"]["es"] in body


def test_the_language_switch_cannot_be_used_to_bounce_off_the_site(web):
    """`back` is a path on this dashboard, never somewhere else."""
    for elsewhere in ("https://example.com/", "//example.com/"):
        response = web.get(
            "/language/en", params={"back": elsewhere}, follow_redirects=False
        )
        assert response.headers["location"] == "/"


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
