"""The anomaly queue. SPEC.md section 6.

Everything the system could not resolve on its own ends up here rather
than being silently dropped. Working this queue is the daily job that
keeps the stock figures honest.
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb

from ....services.state_engine.corrections import (
    ContainerNotFound,
    CorrectionRefused,
    apply_manual_correction,
)
from ..db import connection, fetch_all, fetch_one
from ..schemas import AnomalySummary, ResolveAnomaly

router = APIRouter(prefix="/anomalies", tags=["anomalies"])

KINDS = (
    "UNKNOWN_TID",
    "ILLEGAL_TRANSITION",
    "NO_DIRECTION",
    "NO_SESSION",
    "SHORT_PALLET",
    "COUNT_MISMATCH",
)


@router.get(
    "",
    response_model=list[AnomalySummary],
    summary="The anomaly queue",
    description=(
        "Unresolved anomalies, newest first. Kinds, from SPEC.md section 3:\n\n"
        "- `UNKNOWN_TID` — a tag with no container record\n"
        "- `ILLEGAL_TRANSITION` — a movement the state machine does not allow\n"
        "- `NO_DIRECTION` — the exit gate could not say which way it went\n"
        "- `NO_SESSION` — an exit read with no customer selected\n"
        "- `SHORT_PALLET` — a pallet with fewer boxes read than declared\n"
        "- `COUNT_MISMATCH` — a cycle count variance"
    ),
)
def anomaly_queue(
    resolved: bool = Query(default=False, description="Show resolved ones instead."),
    kind: str | None = Query(default=None, description=f"One of: {', '.join(KINDS)}"),
    limit: int = Query(default=100, ge=1, le=1000),
    conn: psycopg.Connection = Depends(connection),
) -> list[dict]:
    if kind and kind not in KINDS:
        raise HTTPException(422, f"Unknown anomaly kind '{kind}'. Expected one of: {', '.join(KINDS)}")
    return fetch_all(
        conn,
        """SELECT id, tid, container_id, kind, detail, occurred_at,
                  resolved, resolved_by, resolved_at
           FROM anomalies
           WHERE resolved = %s
             AND (%s::text IS NULL OR kind = %s::text)
           ORDER BY occurred_at DESC, id DESC
           LIMIT %s""",
        (resolved, kind, kind, limit),
    )


@router.post(
    "/{anomaly_id}/resolve",
    response_model=AnomalySummary,
    summary="Disposition an anomaly",
    description=(
        "Marks an anomaly as dealt with and records who dealt with it. "
        "This does not change any container's status — if stock needs "
        "correcting, that is a separate physical and manual decision."
    ),
)
def resolve(
    anomaly_id: int,
    body: ResolveAnomaly,
    conn: psycopg.Connection = Depends(connection),
) -> dict:
    existing = fetch_one(
        conn,
        "SELECT id, tid, container_id, resolved, detail FROM anomalies WHERE id = %s",
        (anomaly_id,),
    )
    if existing is None:
        raise HTTPException(404, f"No anomaly {anomaly_id}.")
    if existing["resolved"]:
        raise HTTPException(409, f"Anomaly {anomaly_id} is already resolved.")

    detail = dict(existing["detail"] or {})
    if body.note:
        detail["resolution_note"] = body.note

    # Optionally put the container right in the same transaction, so a
    # decision and the change it implies cannot come apart.
    if body.correct_to_status:
        if not existing["tid"]:
            raise HTTPException(
                422,
                f"Anomaly {anomaly_id} names no tag, so there is nothing to correct.",
            )
        if not existing["container_id"]:
            raise HTTPException(
                422,
                f"Tag '{existing['tid']}' has no container record, so its status "
                "cannot be corrected. Register it first.",
            )
        try:
            correction = apply_manual_correction(
                conn,
                tid=existing["tid"],
                to_status=body.correct_to_status,
                reason=body.note or f"Correction while resolving anomaly {anomaly_id}",
                operator=body.resolved_by,
            )
        except ContainerNotFound as exc:
            conn.rollback()
            raise HTTPException(404, str(exc)) from exc
        except CorrectionRefused as exc:
            conn.rollback()
            raise HTTPException(409, str(exc)) from exc
        detail["corrected_to"] = correction.to_status
        detail["corrected_from"] = correction.from_status

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE anomalies
               SET resolved = TRUE, resolved_by = %s, resolved_at = now(), detail = %s
               WHERE id = %s""",
            (body.resolved_by, Jsonb(detail), anomaly_id),
        )
    conn.commit()
    return fetch_one(
        conn,
        """SELECT id, tid, container_id, kind, detail, occurred_at,
                  resolved, resolved_by, resolved_at
           FROM anomalies WHERE id = %s""",
        (anomaly_id,),
    )
