"""Consumption reporting. SPEC.md section 6.

Question 3 of SPEC.md section 1, and the business objective of the whole
system: Tasker sells machines once and consumables forever, so
consumption rate per account is what drives purchasing, production
planning and sales attention.
"""

import psycopg
from fastapi import APIRouter, Depends, Query

from ..db import connection, fetch_all
from ..schemas import ConsumptionLine

router = APIRouter(tags=["reports"])

# SPEC.md section 3.1, verbatim except that the 90 day window is a
# parameter rather than being fixed in the SQL.
#
# Note on double counting: this joins contents to every dispatched
# container, so a pallet that carries its own container_contents rows AND
# has child boxes with their own rows would count both. SPEC.md section 3
# is explicit that a pallet whose contents come from its boxes has no
# contents rows of its own. Keep that discipline and this is correct.
CONSUMPTION_SQL = """
    SELECT cu.name AS customer, s.name AS sku, SUM(cc.quantity) AS boxes
    FROM movements m
    JOIN dispatch_sessions ds ON ds.session_id = m.session_id
    JOIN customers cu ON cu.customer_id = ds.customer_id
    JOIN container_contents cc ON cc.container_id = m.container_id
    JOIN skus s ON s.sku_id = cc.sku_id
    WHERE m.to_status = 'DISPATCHED'
      AND m.occurred_at > now() - make_interval(days => %s)
    GROUP BY cu.name, s.name
    ORDER BY cu.name, boxes DESC
"""


@router.get(
    "/reports/consumption",
    response_model=list[ConsumptionLine],
    summary="Consumption per customer per SKU",
    description=(
        "How many boxes of each SKU each customer has taken over a period. "
        "The second of the two key queries in SPEC.md section 3.1, and the "
        "number the business actually runs on.\n\n"
        "Only dispatches attributed to a customer appear here. That is the "
        "point of requiring a dispatch session at the dock: an unattributed "
        "load would be invisible to this report, so the system refuses to "
        "record one."
    ),
)
def consumption(
    days: int = Query(
        default=90, ge=1, le=3650, description="How many days back to look."
    ),
    conn: psycopg.Connection = Depends(connection),
) -> list[dict]:
    return fetch_all(conn, CONSUMPTION_SQL, (days,))
