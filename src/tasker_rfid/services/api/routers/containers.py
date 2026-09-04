"""Registering containers and building pallets. SPEC.md section 6.

Note what is absent: nothing here writes containers.status. Creating a
container lets the schema's own default apply (REGISTERED), and every
change after that belongs to the state engine (SPEC.md section 4).
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.rows import dict_row

from ....services.state_engine.corrections import (
    ContainerNotFound,
    CorrectionRefused,
    apply_manual_correction,
)
from ..db import connection, fetch_all, fetch_one
from ..schemas import (
    AttachChildren,
    ContainerDetail,
    ContainerSummary,
    ManualCorrection,
    NewContainer,
)

router = APIRouter(tags=["containers"])

CONTAINER_BY_TID_SQL = """
    SELECT container_id, tid, kind, status, reusable, parent_id, epc,
           created_at, last_seen_at, last_portal
    FROM containers WHERE tid = %s
"""


def _require_container(conn: psycopg.Connection, tid: str) -> dict:
    container = fetch_one(conn, CONTAINER_BY_TID_SQL, (tid,))
    if container is None:
        raise HTTPException(404, f"No container registered against TID '{tid}'.")
    return container


@router.post(
    "/containers",
    response_model=ContainerSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Register a container and what is in it",
    description=(
        "Records a physical container against its chip's TID, with its "
        "contents.\n\n"
        "The container starts REGISTERED. It becomes IN_STOCK when a portal "
        "reads it, never through this endpoint: SPEC.md section 4 reserves "
        "every status change for the state engine."
    ),
)
def register_container(
    body: NewContainer, conn: psycopg.Connection = Depends(connection)
) -> dict:
    if fetch_one(conn, "SELECT 1 FROM containers WHERE tid = %s", (body.tid,)):
        raise HTTPException(409, f"TID '{body.tid}' is already registered.")

    parent_id = None
    if body.parent_tid:
        parent = _require_container(conn, body.parent_tid)
        parent_id = parent["container_id"]

    for line in body.contents:
        if not fetch_one(conn, "SELECT 1 FROM skus WHERE sku_id = %s", (line.sku_id,)):
            raise HTTPException(
                422, f"No SKU '{line.sku_id}'. Add it to seeds/skus.csv and re-seed."
            )

    with conn.cursor(row_factory=dict_row) as cur:
        # status is deliberately not in this column list.
        cur.execute(
            """INSERT INTO containers (tid, epc, kind, parent_id, reusable)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING container_id, tid, kind, status, reusable, parent_id, epc,
                         created_at, last_seen_at, last_portal""",
            (body.tid, body.epc, body.kind, parent_id, body.reusable),
        )
        created = cur.fetchone()

        for line in body.contents:
            cur.execute(
                """INSERT INTO container_contents
                       (container_id, sku_id, quantity, lot, produced_at, expiry)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    created["container_id"],
                    line.sku_id,
                    line.quantity,
                    line.lot,
                    line.produced_at,
                    line.expiry,
                ),
            )
    conn.commit()
    return created


@router.post(
    "/containers/{tid}/children",
    response_model=ContainerDetail,
    summary="Attach containers to this one (pallet build)",
    description=(
        "Puts boxes onto a pallet by setting their parent. Attaching does "
        "not move anything: the boxes keep their own status until a portal "
        "read moves the pallet, at which point they move with it."
    ),
)
def attach_children(
    tid: str, body: AttachChildren, conn: psycopg.Connection = Depends(connection)
) -> dict:
    parent = _require_container(conn, tid)

    for child_tid in body.child_tids:
        if child_tid == tid:
            raise HTTPException(422, "A container cannot be placed on itself.")
        child = _require_container(conn, child_tid)
        if child["parent_id"] not in (None, parent["container_id"]):
            raise HTTPException(
                409,
                f"'{child_tid}' is already on another container "
                f"(container_id {child['parent_id']}). Detach it first.",
            )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE containers SET parent_id = %s WHERE tid = ANY(%s)",
            (parent["container_id"], body.child_tids),
        )
    conn.commit()
    return _detail(conn, tid)


@router.post(
    "/containers/{tid}/correct",
    response_model=ContainerDetail,
    summary="Put a container's status right by hand",
    description=(
        "The one way a status changes without a portal read, for the cases "
        "the readers cannot resolve: a container that went out but was never "
        "read at the exit, one whose tag has died, or an "
        "`ILLEGAL_TRANSITION` that a person has looked into.\n\n"
        "A reason is required and is recorded on the movement, along with "
        "who made the decision and `source='MANUAL'`, so a hand-typed change "
        "is never mistaken for a read.\n\n"
        "The correction still goes through the state engine — this endpoint "
        "calls into it rather than writing the status itself, so there "
        "remains exactly one place in the system that sets that column.\n\n"
        "Applies to the named container only. A pallet's boxes are left "
        "alone: correct each container that genuinely needs it."
    ),
)
def correct_container(
    tid: str, body: ManualCorrection, conn: psycopg.Connection = Depends(connection)
) -> dict:
    try:
        apply_manual_correction(
            conn,
            tid=tid,
            to_status=body.to_status,
            reason=body.reason,
            operator=body.operator,
        )
    except ContainerNotFound as exc:
        conn.rollback()
        raise HTTPException(404, str(exc)) from exc
    except CorrectionRefused as exc:
        conn.rollback()
        raise HTTPException(409, str(exc)) from exc
    conn.commit()
    return _detail(conn, tid)


@router.get(
    "/containers/{tid}",
    response_model=ContainerDetail,
    summary="Full history of one container",
    description=(
        "Everything the system knows about a container: what it is, what is "
        "in it, what is stacked on it, every state change, and every anomaly "
        "it has been involved in."
    ),
)
def container_detail(
    tid: str, conn: psycopg.Connection = Depends(connection)
) -> dict:
    return _detail(conn, tid)


def _detail(conn: psycopg.Connection, tid: str) -> dict:
    container = _require_container(conn, tid)
    container_id = container["container_id"]
    return {
        "container": container,
        "contents": fetch_all(
            conn,
            """SELECT cc.sku_id, s.name AS sku_name, cc.quantity, cc.lot,
                      cc.produced_at, cc.expiry
               FROM container_contents cc
               LEFT JOIN skus s ON s.sku_id = cc.sku_id
               WHERE cc.container_id = %s ORDER BY cc.id""",
            (container_id,),
        ),
        "children": fetch_all(
            conn,
            """SELECT container_id, tid, kind, status, reusable, parent_id, epc,
                      created_at, last_seen_at, last_portal
               FROM containers WHERE parent_id = %s ORDER BY tid""",
            (container_id,),
        ),
        "movements": fetch_all(
            conn,
            """SELECT from_status, to_status, portal, session_id, occurred_at, source
               FROM movements WHERE container_id = %s
               ORDER BY occurred_at, id""",
            (container_id,),
        ),
        "anomalies": fetch_all(
            conn,
            """SELECT id, tid, container_id, kind, detail, occurred_at,
                      resolved, resolved_by, resolved_at
               FROM anomalies WHERE container_id = %s OR tid = %s
               ORDER BY id""",
            (container_id, tid),
        ),
    }
