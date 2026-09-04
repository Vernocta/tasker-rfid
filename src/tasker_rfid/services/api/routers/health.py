"""Health. SPEC.md section 6: reader status, last read, queue depth.

SPEC.md section 7 asks for an alert if no read arrives in four hours
during working hours. A reader that has quietly stopped answering is the
most dangerous failure in the system, because everything downstream keeps
working perfectly on a stream of nothing.
"""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Depends

from ....config import load_health
from ..db import connection, fetch_all, fetch_one
from ..schemas import HealthReport

router = APIRouter(tags=["health"])

READERS_SQL = """
    SELECT reader_id,
           max(read_at) AS last_read_at,
           count(*) FILTER (WHERE ingested_at > now() - INTERVAL '24 hours') AS reads_today
    FROM reads_raw
    GROUP BY reader_id
    ORDER BY reader_id
"""

QUEUE_SQL = """
    SELECT
      (SELECT coalesce(max(id), 0) FROM reads_raw)
        - (SELECT coalesce(max(last_read_id), 0) FROM debouncer_cursor)
            AS reads_awaiting_debounce,
      (SELECT count(*) FROM observations WHERE processed = FALSE)
            AS observations_awaiting_state_engine
"""


@router.get(
    "/health",
    response_model=HealthReport,
    summary="Is the system actually working?",
    description=(
        "Reader status, when each reader last spoke, how far behind the "
        "pipeline is, and how many anomalies are waiting.\n\n"
        "`status` is `degraded` if a reader has gone quiet during working "
        "hours for longer than `no_read_alert_hours` in "
        "`config/tasker.yaml`, or if the database cannot be reached. "
        "Outside working hours a silent reader is expected, not a fault."
    ),
)
def health(conn: psycopg.Connection = Depends(connection)) -> dict:
    settings = load_health()
    try:
        tz = ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")

    now_local = datetime.now(tz)
    opens, closes = settings.working_hours_range()
    within_working_hours = opens <= now_local.time() <= closes

    warnings: list[str] = []
    readers = []
    for row in fetch_all(conn, READERS_SQL):
        last = row["last_read_at"]
        minutes = None
        healthy = True
        if last is not None:
            minutes = (datetime.now(last.tzinfo) - last).total_seconds() / 60
            if within_working_hours and minutes > settings.no_read_alert_hours * 60:
                healthy = False
                warnings.append(
                    f"{row['reader_id']} has not reported for "
                    f"{minutes / 60:.1f} hours during working hours."
                )
        readers.append(
            {
                "reader_id": row["reader_id"],
                "last_read_at": last,
                "minutes_since_last_read": minutes,
                "reads_today": row["reads_today"],
                "healthy": healthy,
            }
        )

    if not readers:
        warnings.append("No reader has ever reported a read.")

    queue = fetch_one(conn, QUEUE_SQL) or {
        "reads_awaiting_debounce": 0,
        "observations_awaiting_state_engine": 0,
    }
    unresolved = fetch_one(
        conn, "SELECT count(*) AS n FROM anomalies WHERE resolved = FALSE"
    )

    return {
        "status": "degraded" if warnings else "ok",
        "database": "ok",
        "checked_at": now_local,
        "within_working_hours": within_working_hours,
        "no_read_alert_hours": settings.no_read_alert_hours,
        "readers": readers,
        "queue_depth": queue,
        "unresolved_anomalies": unresolved["n"] if unresolved else 0,
        "warnings": warnings,
    }
