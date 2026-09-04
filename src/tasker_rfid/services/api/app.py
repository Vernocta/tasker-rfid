"""The Tasker RFID API. SPEC.md section 6.

Interactive documentation, where you can try every endpoint from the
browser, is at:

    http://localhost:8000/docs

Run it with the rest of the stack:

    docker compose up -d

or on its own during development:

    uv run tasker-api
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .routers import (
    anomalies,
    containers,
    cycle_counts,
    dispatch_sessions,
    health,
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


app = FastAPI(
    title="Tasker RFID Stock Control",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=TAGS,
    contact={"name": "SPEC.md", "url": "https://github.com/Vernocta/tasker-rfid"},
)

for router in (
    stock.router,
    containers.router,
    dispatch_sessions.router,
    cycle_counts.router,
    anomalies.router,
    reports.router,
    health.router,
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
