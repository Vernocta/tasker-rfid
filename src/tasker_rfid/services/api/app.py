"""The Tasker RFID API. SPEC.md section 6.

Interactive documentation, where you can try every endpoint from the
browser, is at:

    http://localhost:8000/docs

Run it with the rest of the stack:

    docker compose up -d

or on its own during development:

    uv run tasker-api
"""

import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ...db_errors import SCHEMA_IS_BEHIND, explain
from . import db
from .routers import (
    anomalies,
    catalogue,
    containers,
    cycle_counts,
    dispatch_sessions,
    health,
    observations,
    reports,
    stock,
)

DESCRIPTION = """
Finished-goods stock control for Tasker S.A., driven by UHF RFID reads at
warehouse chokepoints.

It answers three questions:

1. **What is in stock**, by SKU and lot — `GET /stock`
2. **What left the building**, when, and for whom — `GET /dispatch-sessions/{id}`
3. **What each customer consumes**, per SKU — `GET /reports/consumption`

The third is the business objective. Tasker sells machines once and
consumables forever, so consumption rate per account is the figure that
drives purchasing, production planning and sales attention.

### How a container moves

    REGISTERED  ->  IN_STOCK  ->  DISPATCHED

**This API never changes a container's status.** Registering one starts it
at REGISTERED; everything after that happens because a portal read it, and
only the state engine applies those changes. That single rule is what makes
double-counting structurally impossible rather than something the system
tries to detect.

### Nothing leaves unattributed

A dispatch requires an open dispatch session naming the customer. An exit
read with no session open raises a `NO_SESSION` anomaly and the container
stays where it is. Anything the system cannot resolve lands in
`GET /anomalies` rather than being quietly dropped.
"""

TAGS = [
    {"name": "stock", "description": "What is in the warehouse right now."},
    {
        "name": "containers",
        "description": (
            "Register containers and build pallets. Never changes status: "
            "that belongs to the state engine."
        ),
    },
    {
        "name": "dispatch",
        "description": (
            "Open the dock for a customer before loading. Everything read at "
            "the exit during a session is attributed to it."
        ),
    },
    {
        "name": "cycle counts",
        "description": "Reconcile the floor against the system, and record the variance.",
    },
    {
        "name": "anomalies",
        "description": "Everything the system could not resolve on its own.",
    },
    {"name": "reports", "description": "Consumption per customer per SKU."},
    {"name": "health", "description": "Reader status, last read, queue depth."},
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.open_pool()
    yield
    db.close_pool()


log = logging.getLogger("api")

app = FastAPI(
    title="Tasker RFID Stock Control",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=TAGS,
    contact={"name": "SPEC.md", "url": "https://github.com/Vernocta/tasker-rfid"},
)

# The dashboard is served from its own port, so its JavaScript calls this
# API cross-origin. On a warehouse LAN with no authentication anywhere,
# the origin list is not the thing keeping anyone out; set
# CORS_ALLOW_ORIGINS if this is ever reachable beyond the warehouse.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(psycopg.Error)
async def database_is_unavailable(request: Request, exc: psycopg.Error) -> JSONResponse:
    """Say what is wrong and what to do, rather than returning a bare 500.

    The commonest cause is migrations that have not been run after a pull.
    A stack trace in the log does not tell whoever is standing at the dock
    anything they can act on.
    """
    message = explain(exc, database="The warehouse database")
    status = 503 if isinstance(exc, SCHEMA_IS_BEHIND) else 500
    log.error("request to %s failed: %s", request.url.path, message)
    return JSONResponse(status_code=status, content={"detail": message})


for router in (
    stock.router,
    containers.router,
    dispatch_sessions.router,
    cycle_counts.router,
    anomalies.router,
    reports.router,
    health.router,
    catalogue.router,
    observations.router,
):
    app.include_router(router)


def main() -> None:
    """Entry point for `uv run tasker-api` and the container's CMD."""
    import uvicorn

    uvicorn.run(
        "tasker_rfid.services.api.app:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
