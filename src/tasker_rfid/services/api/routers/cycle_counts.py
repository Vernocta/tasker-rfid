"""Cycle counts. SPEC.md section 6.

The reconciliation that catches what the portals missed: a container that
slipped past a reader, or a tag that has stopped answering (SPEC.md
section 7). Scan everything on the floor, close the count, and the
variance is the difference between what is there and what the system
believed.

A cycle count never changes a container's status. It records the
discrepancy as COUNT_MISMATCH anomalies for a person to disposition,
because the system cannot know whether a missing box was stolen,
misplaced, or simply has a dead tag.
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..db import connection, fetch_all, fetch_one
from ..schemas import CycleScan, NewCycleCount, VarianceReport

router = APIRouter(prefix="/cycle-counts", tags=["cycle counts"])

COUNT_MISMATCH = "COUNT_MISMATCH"


def _require_open_count(conn: psycopg.Connection, cycle_id: int) -> dict:
    count = fetch_one(
        conn,
        "SELECT id, started_at, finished_at, operator FROM cycle_counts WHERE id = %s",
        (cycle_id,),
    )
    if count is None:
        raise HTTPException(404, f"No cycle count {cycle_id}.")
    if count["finished_at"] is not None:
        raise HTTPException(
            409, f"Cycle count {cycle_id} was closed at {count['finished_at']}."
        )
    return count


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Start a cycle count",
    description="Opens a count. Scan tags into it, then close it for the variance report.",
)
def start_count(
    body: NewCycleCount, conn: psycopg.Connection = Depends(connection)
) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO cycle_counts (operator, notes) VALUES (%s, %s)
               RETURNING id AS cycle_id, started_at, operator, notes""",
            (body.operator, body.notes),
        )
        created = cur.fetchone()
    conn.commit()
    return created


@router.post(
    "/{cycle_id}/scan",
    summary="Record a tag seen on the floor",
    description=(
        "Scanning the same tag twice is harmless: a tag is either found or "
        "it is not, so repeats change nothing."
    ),
)
def scan(
    cycle_id: int, body: CycleScan, conn: psycopg.Connection = Depends(connection)
) -> dict:
    _require_open_count(conn, cycle_id)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cycle_count_items (cycle_id, tid, found)
               VALUES (%s, %s, TRUE)
               ON CONFLICT (cycle_id, tid) DO UPDATE SET found = TRUE""",
            (cycle_id, body.tid),
        )
        cur.execute(
            "SELECT count(*) FROM cycle_count_items WHERE cycle_id = %s AND found",
            (cycle_id,),
        )
        scanned = cur.fetchone()[0]
    conn.commit()
    return {"cycle_id": cycle_id, "tid": body.tid, "scanned_so_far": scanned}


@router.post(
    "/{cycle_id}/close",
    response_model=VarianceReport,
    summary="Close the count and get the variance",
    description=(
        "Compares what was scanned against every container the system "
        "believes is IN_STOCK.\n\n"
        "**Missing**: believed in stock but not scanned — a missed portal "
        "read, a dead tag, or genuinely gone.\n\n"
        "**Unexpected**: scanned but not believed in stock — dispatched "
        "stock still on the floor, or a container never registered.\n\n"
        "Each discrepancy becomes a COUNT_MISMATCH anomaly. Nothing is "
        "corrected automatically: a person decides what actually happened."
    ),
)
def close_count(
    cycle_id: int, conn: psycopg.Connection = Depends(connection)
) -> dict:
    _require_open_count(conn, cycle_id)

    expected = fetch_all(
        conn,
        """SELECT container_id, tid, kind, last_seen_at, last_portal
           FROM containers WHERE status = 'IN_STOCK'""",
    )
    scanned_tids = {
        row["tid"]
        for row in fetch_all(
            conn,
            "SELECT tid FROM cycle_count_items WHERE cycle_id = %s AND found",
            (cycle_id,),
        )
    }

    expected_tids = {row["tid"] for row in expected}
    missing = [row for row in expected if row["tid"] not in scanned_tids]
    unexpected_tids = sorted(scanned_tids - expected_tids)

    unexpected = []
    for tid in unexpected_tids:
        known = fetch_one(
            conn, "SELECT container_id, tid, kind, status FROM containers WHERE tid = %s", (tid,)
        )
        unexpected.append(known or {"tid": tid, "status": "NOT REGISTERED"})

    raised = 0
    with conn.cursor() as cur:
        # A container believed in stock that nobody could find is recorded
        # against the count too, so the count itself is a complete record.
        for row in missing:
            cur.execute(
                """INSERT INTO cycle_count_items (cycle_id, tid, found)
                   VALUES (%s, %s, FALSE)
                   ON CONFLICT (cycle_id, tid) DO NOTHING""",
                (cycle_id, row["tid"]),
            )
            cur.execute(
                """INSERT INTO anomalies (tid, container_id, kind, detail)
                   VALUES (%s, %s, %s, %s)""",
                (
                    row["tid"],
                    row["container_id"],
                    COUNT_MISMATCH,
                    Jsonb(
                        {
                            "cycle_id": cycle_id,
                            "variance": "missing",
                            "reason": "believed IN_STOCK but not scanned",
                            "last_portal": row["last_portal"],
                        }
                    ),
                ),
            )
            raised += 1

        for row in unexpected:
            cur.execute(
                """INSERT INTO anomalies (tid, container_id, kind, detail)
                   VALUES (%s, %s, %s, %s)""",
                (
                    row["tid"],
                    row.get("container_id"),
                    COUNT_MISMATCH,
                    Jsonb(
                        {
                            "cycle_id": cycle_id,
                            "variance": "unexpected",
                            "reason": "scanned but not believed in stock",
                            "status": row.get("status"),
                        }
                    ),
                ),
            )
            raised += 1

        cur.execute(
            "UPDATE cycle_counts SET finished_at = now() WHERE id = %s", (cycle_id,)
        )
    conn.commit()

    return {
        "cycle_id": cycle_id,
        "expected": len(expected),
        "found": len(expected_tids & scanned_tids),
        "missing": [
            {"tid": r["tid"], "container_id": r["container_id"], "kind": r["kind"],
             "last_portal": r["last_portal"]}
            for r in missing
        ],
        "unexpected": unexpected,
        "anomalies_raised": raised,
    }
