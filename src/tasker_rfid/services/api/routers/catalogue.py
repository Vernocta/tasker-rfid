"""The lists an operator picks from: customers and SKUs.

Both come from the CSV files in seeds/ (see `uv run tasker-seed`). These
endpoints exist so the dashboard can offer a customer to choose at the
dock rather than making someone type an ID from memory.
"""

import psycopg
from fastapi import APIRouter, Depends, Query

from ..db import connection, fetch_all

router = APIRouter(tags=["catalogue"])


@router.get("/customers", summary="Customers, for the dispatch picker")
def customers(
    active_only: bool = Query(default=True, description="Hide retired customers."),
    conn: psycopg.Connection = Depends(connection),
) -> list[dict]:
    return fetch_all(
        conn,
        """SELECT customer_id, name, active FROM customers
           WHERE (%s = FALSE OR active) ORDER BY name""",
        (active_only,),
    )


@router.get("/skus", summary="SKUs, with their family and pack size")
def skus(
    active_only: bool = Query(default=True),
    conn: psycopg.Connection = Depends(connection),
) -> list[dict]:
    return fetch_all(
        conn,
        """SELECT sku_id, name, family, units_per_box, tag_class, active
           FROM skus WHERE (%s = FALSE OR active) ORDER BY name""",
        (active_only,),
    )
