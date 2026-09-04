"""Stock on hand. SPEC.md section 6."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from ..db import connection, fetch_all, fetch_one
from ..schemas import StockHolder, StockLine

router = APIRouter(tags=["stock"])

# SPEC.md section 3.1, matching the corrected version there.
#
# An earlier version of the spec had an extra condition on this query:
#
#     AND c.parent_id IS NULL          -- avoid double-counting nested containers
#
# which was wrong, and SPEC.md has been corrected. In the configured mode
# (`hybrid`) the boxes carry the contents and the pallet carries none, so
# that condition filtered out exactly the rows holding the quantities and
# stock sitting on a pallet vanished from the count.
#
# Nothing is double-counted without it: a container_contents row belongs
# to exactly one container, so summing the contents of every IN_STOCK
# container counts each row once. The consumption query makes the same
# assumption.
#
# The one thing that would double-count is putting contents rows on a
# pallet AND on the boxes it carries. SPEC.md section 3 forbids that.
STOCK_ON_HAND_SQL = """
    SELECT s.sku_id, s.name, SUM(cc.quantity) AS boxes
    FROM containers c
    JOIN container_contents cc ON cc.container_id = c.container_id
    JOIN skus s ON s.sku_id = cc.sku_id
    WHERE c.status = 'IN_STOCK'
    GROUP BY s.sku_id, s.name
    ORDER BY s.name
"""

HOLDERS_SQL = """
    SELECT c.tid, c.container_id, c.kind, cc.quantity, cc.lot, cc.expiry,
           c.last_seen_at, c.last_portal
    FROM containers c
    JOIN container_contents cc ON cc.container_id = c.container_id
    WHERE c.status = 'IN_STOCK'
      AND cc.sku_id = %s
    ORDER BY cc.expiry NULLS LAST, c.tid
"""


@router.get(
    "/stock",
    response_model=list[StockLine],
    summary="Stock on hand by SKU",
    description=(
        "How many boxes of each SKU are in the warehouse right now.\n\n"
        "Counts the contents of every container the state engine has marked "
        "IN_STOCK. Each container_contents row belongs to one container, so "
        "nothing is counted twice. This is the first of the two key queries "
        "in SPEC.md section 3.1."
    ),
)
def stock_on_hand(conn: psycopg.Connection = Depends(connection)) -> list[dict]:
    return fetch_all(conn, STOCK_ON_HAND_SQL)


@router.get(
    "/stock/{sku_id}",
    response_model=list[StockHolder],
    summary="Which containers hold this SKU",
    description="The individual containers in stock holding a given SKU, oldest expiry first.",
)
def stock_for_sku(
    sku_id: str, conn: psycopg.Connection = Depends(connection)
) -> list[dict]:
    if fetch_one(conn, "SELECT sku_id FROM skus WHERE sku_id = %s", (sku_id,)) is None:
        raise HTTPException(404, f"No SKU '{sku_id}'. Check seeds/skus.csv.")
    return fetch_all(conn, HOLDERS_SQL, (sku_id,))
