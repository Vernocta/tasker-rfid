"""The live read feed.

One row per physical event at a portal, newest first, with the tag
resolved to whatever it turned out to be. This is what somebody watches
while walking a box through a portal to confirm the reader is working.
"""

import psycopg
from fastapi import APIRouter, Depends, Query

from ..db import connection, fetch_all

router = APIRouter(tags=["observations"])

RECENT_SQL = """
    SELECT o.id, o.tid, o.portal, o.direction, o.first_read, o.last_read,
           o.read_count, o.peak_rssi, o.processed,
           c.container_id, c.kind, c.status,
           (SELECT string_agg(s.name || ' x' || cc.quantity, ', ' ORDER BY s.name)
              FROM container_contents cc
              JOIN skus s ON s.sku_id = cc.sku_id
             WHERE cc.container_id = c.container_id) AS contents
    FROM observations o
    LEFT JOIN containers c ON c.tid = o.tid
    -- By when the tag was actually read, not by the order the debouncer
    -- happened to close each group: a tag that goes quiet sooner gets a
    -- lower id, which would put it above a read that happened later.
    ORDER BY o.last_read DESC, o.id DESC
    LIMIT %s
"""


@router.get(
    "/observations",
    summary="Recent reads at the portals",
    description=(
        "Debounced reads, newest read first — one row per physical event, not "
        "per raw read. `contents` and `kind` are NULL when the tag matches "
        "no registered container, which is what an `UNKNOWN_TID` anomaly "
        "looks like from the floor."
    ),
)
def recent_observations(
    limit: int = Query(default=50, ge=1, le=500),
    conn: psycopg.Connection = Depends(connection),
) -> list[dict]:
    return fetch_all(conn, RECENT_SQL, (limit,))
