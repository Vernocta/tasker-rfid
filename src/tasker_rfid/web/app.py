"""The warehouse dashboard. SPEC.md section 5, `web/`.

Five screens, served as plain HTML from Jinja2 templates. Tailwind comes
from a CDN and the JavaScript is hand-written and polls the API every few
seconds. No React, no npm, no build step: what is in these files is what
runs, so anyone can open a template and change it.

It runs as its own service on its own port. The API owns paths like
`/anomalies` for its JSON, and the dashboard needs `/anomalies` for a
page, so the two cannot share a port. Keeping them apart also keeps the
machine interface and the human one independent.

    uv run tasker-dashboard        # http://localhost:8080

The browser talks to the API directly, at API_BASE_URL. That is a URL the
*browser* must be able to reach, so it is localhost by default rather than
the compose service name.
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .text import DEFAULT_LANGUAGE, LANGUAGE_NAMES, LANGUAGES, normalise, strings_for

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

app = FastAPI(
    title="Tasker RFID Dashboard",
    description="The warehouse screens. The API's own documentation is on port 8000.",
    docs_url=None,
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

# Every screen: template, the nav label, and the plain sentence at the top
# saying what you are looking at.
SCREENS = [
    ("/", "stock.html", "nav.stock", "stock.title"),
    ("/live", "live.html", "nav.live", "live.title"),
    ("/dispatch", "dispatch.html", "nav.dispatch", "dispatch.title"),
    ("/anomalies", "anomalies.html", "nav.anomalies", "anom.title"),
    ("/reports", "reports.html", "nav.reports", "rep.title"),
]

LANGUAGE_COOKIE = "tasker_language"
"""Set once on the screen by the dock, and it stays set.

A wall-mounted screen is chosen for its room, not per visit, so the choice
belongs to that browser rather than to a URL somebody has to remember."""


def api_base_url() -> str:
    return os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")


def language_for(request: Request) -> str:
    """?lang= wins, then this screen's saved choice, then the default."""
    asked = request.query_params.get("lang")
    if asked in LANGUAGES:
        return asked
    return normalise(
        request.cookies.get(LANGUAGE_COOKIE) or os.getenv("DASHBOARD_LANGUAGE", DEFAULT_LANGUAGE)
    )


def render(request: Request, template: str, path: str, title_key: str) -> HTMLResponse:
    language = language_for(request)
    text = strings_for(language)
    response = templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "nav": [
                {"path": p, "label": text[label_key]}
                for p, _, label_key, _ in SCREENS
            ],
            "current_path": path,
            "page_title": text[title_key],
            "t": text,
            "language": language,
            "languages": [
                {"code": code, "name": LANGUAGE_NAMES[code]} for code in LANGUAGES
            ],
            "api_base": api_base_url(),
            "poll_seconds": int(os.getenv("DASHBOARD_POLL_SECONDS", "5")),
        },
    )
    # Remember the choice on this screen so it survives a reload.
    if request.query_params.get("lang") in LANGUAGES:
        response.set_cookie(
            LANGUAGE_COOKIE, language, max_age=60 * 60 * 24 * 365, samesite="lax"
        )
    return response


def _add_screen(path: str, template: str, title_key: str) -> None:
    async def screen(request: Request) -> HTMLResponse:
        return render(request, template, path, title_key)

    app.add_api_route(path, screen, methods=["GET"], include_in_schema=False)


for _path, _template, _label_key, _title_key in SCREENS:
    _add_screen(_path, _template, _title_key)


@app.get("/language/{code}", include_in_schema=False)
async def choose_language(code: str, request: Request) -> RedirectResponse:
    """Switch language and come straight back to the screen you were on."""
    back = request.query_params.get("back", "/")
    if not back.startswith("/") or back.startswith("//"):
        back = "/"  # never bounce off this site
    response = RedirectResponse(back, status_code=303)
    response.set_cookie(
        LANGUAGE_COOKIE, normalise(code), max_age=60 * 60 * 24 * 365, samesite="lax"
    )
    return response


def main() -> None:
    """Entry point for `uv run tasker-dashboard` and the container's CMD."""
    import uvicorn

    uvicorn.run(
        "tasker_rfid.web.app:app",
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.getenv("DASHBOARD_PORT", "8080")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
