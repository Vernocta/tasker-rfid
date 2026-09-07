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
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
    ("/", "stock.html", "Stock", "Stock on hand"),
    ("/live", "live.html", "Live reads", "Live read feed"),
    ("/dispatch", "dispatch.html", "Dispatch", "Dispatch control"),
    ("/anomalies", "anomalies.html", "Anomalies", "Anomaly queue"),
    ("/reports", "reports.html", "Reports", "Consumption by customer"),
]

NAV = [{"path": path, "label": label} for path, _, label, _ in SCREENS]


def api_base_url() -> str:
    return os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")


def render(request: Request, template: str, path: str, title: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "nav": NAV,
            "current_path": path,
            "page_title": title,
            "api_base": api_base_url(),
            "poll_seconds": int(os.getenv("DASHBOARD_POLL_SECONDS", "5")),
        },
    )


def _add_screen(path: str, template: str, title: str) -> None:
    async def screen(request: Request) -> HTMLResponse:
        return render(request, template, path, title)

    app.add_api_route(path, screen, methods=["GET"], include_in_schema=False)


for _path, _template, _label, _title in SCREENS:
    _add_screen(_path, _template, _title)


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
