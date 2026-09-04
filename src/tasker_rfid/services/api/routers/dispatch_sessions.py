"""Dispatch sessions. SPEC.md section 6.

SPEC.md 2.5: every dispatch is attributed. The operator opens a session
against a customer at the dock before loading, and everything read at the
exit during that window belongs to it. An exit read with no open session
is an anomaly, not a dispatch — the state engine enforces that.
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.rows import dict_row

from ..db import connection, fetch_all, fetch_one
from ..schemas import DispatchSession, DispatchSessionDetail, NewDispatchSession

router = APIRouter(prefix="/dispatch-sessions", tags=["dispatch"])

SESSION_SQL = """
    SELECT session_id, customer_id, order_ref, operator, opened_at, closed_at
    FROM dispatch_sessions WHERE session_id = %s
"""


@router.post(
    "",
    response_model=DispatchSession,
    status_code=status.HTTP_201_CREATED,
    summary="Open the dock for a customer",
    description=(
        "Opens a dispatch session. Everything read at the exit from now "
        "until it is closed is attributed to this customer and order.\n\n"
        "Only one session may be open at a time: with two open, a load "
        "leaving the building would have no unambiguous destination."
    ),
)
def open_session(
    body: NewDispatchSession, conn: psycopg.Connection = Depends(connection)
) -> dict:
    already_open = fetch_one(
        conn,
        """SELECT session_id, customer_id FROM dispatch_sessions
           WHERE closed_at IS NULL ORDER BY opened_at DESC LIMIT 1""",
    )
    if already_open:
        raise HTTPException(
            409,
            f"Session {already_open['session_id']} is still open for "
            f"{already_open['customer_id']}. Close it before opening another.",
        )

    if not fetch_one(
        conn, "SELECT 1 FROM customers WHERE customer_id = %s", (body.customer_id,)
    ):
        raise HTTPException(
            422,
            f"No customer '{body.customer_id}'. Add them to seeds/customers.csv "
            "and re-seed.",
        )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO dispatch_sessions (customer_id, order_ref, operator)
               VALUES (%s, %s, %s)
               RETURNING session_id, customer_id, order_ref, operator,
                         opened_at, closed_at""",
            (body.customer_id, body.order_ref, body.operator),
        )
        created = cur.fetchone()
    conn.commit()
    return created


@router.post(
    "/{session_id}/close",
    response_model=DispatchSessionDetail,
    summary="Close the dock",
    description="Closes the session and returns what went out during it.",
)
def close_session(
    session_id: int, conn: psycopg.Connection = Depends(connection)
) -> dict:
    session = fetch_one(conn, SESSION_SQL, (session_id,))
    if session is None:
        raise HTTPException(404, f"No dispatch session {session_id}.")
    if session["closed_at"] is not None:
        raise HTTPException(
            409, f"Session {session_id} was already closed at {session['closed_at']}."
        )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE dispatch_sessions SET closed_at = now() WHERE session_id = %s",
            (session_id,),
        )
    conn.commit()
    return _detail(conn, session_id)


@router.get(
    "/{session_id}",
    response_model=DispatchSessionDetail,
    summary="What went out during a session",
)
def session_detail(
    session_id: int, conn: psycopg.Connection = Depends(connection)
) -> dict:
    return _detail(conn, session_id)


def _detail(conn: psycopg.Connection, session_id: int) -> dict:
    session = fetch_one(conn, SESSION_SQL, (session_id,))
    if session is None:
        raise HTTPException(404, f"No dispatch session {session_id}.")
    return {
        "session": session,
        "containers": fetch_all(
            conn,
            """SELECT c.tid, c.kind, m.occurred_at, m.portal
               FROM movements m JOIN containers c USING (container_id)
               WHERE m.session_id = %s AND m.to_status = 'DISPATCHED'
               ORDER BY m.occurred_at, c.tid""",
            (session_id,),
        ),
        "totals_by_sku": fetch_all(
            conn,
            """SELECT s.sku_id, s.name AS sku_name, SUM(cc.quantity) AS boxes
               FROM movements m
               JOIN container_contents cc ON cc.container_id = m.container_id
               JOIN skus s ON s.sku_id = cc.sku_id
               WHERE m.session_id = %s AND m.to_status = 'DISPATCHED'
               GROUP BY s.sku_id, s.name ORDER BY s.name""",
            (session_id,),
        ),
    }
