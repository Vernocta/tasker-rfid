"""Manual correction of a container's status.

**The one exception to "only the state engine writes containers.status".**

It lives inside the state_engine package deliberately. SPEC.md section 4
says all status changes go through this module, and a correction is still
a status change: the API calls in here rather than issuing an UPDATE of
its own, so there remains exactly one place in the codebase that sets
that column.

A correction exists because some things the portals cannot resolve are
resolvable by a person who walks over and looks. SPEC.md section 7 says
an ILLEGAL_TRANSITION needs "manual correction"; a container missed at a
portal, or one whose tag has died, is found by a cycle count and then has
to be put right. This is how.

Every correction records a movement with source='MANUAL', a reason, and
who made it, so a hand-typed change is never indistinguishable from a
read.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from .transitions import DISPATCHED, IN_STOCK, REGISTERED

VALID_STATUSES = (REGISTERED, IN_STOCK, DISPATCHED)

SOURCE_MANUAL = "MANUAL"

LOCK_CONTAINER_SQL = """
    SELECT container_id, tid, kind, status, reusable
    FROM containers WHERE tid = %s
    FOR UPDATE
"""

UPDATE_STATUS_SQL = """
    UPDATE containers SET status = %s WHERE container_id = %s
"""

INSERT_MOVEMENT_SQL = """
    INSERT INTO movements
        (container_id, from_status, to_status, portal, session_id,
         occurred_at, source, reason, operator)
    VALUES (%s, %s, %s, NULL, NULL, %s, %s, %s, %s)
"""


class ContainerNotFound(Exception):
    """No container is registered against that TID."""


class CorrectionRefused(Exception):
    """The correction asks for something that makes no sense."""


@dataclass(frozen=True)
class Correction:
    """What a correction did, for the caller to report back."""

    container_id: int
    tid: str
    from_status: str
    to_status: str
    reason: str
    operator: str
    occurred_at: datetime


def check_target_status(current_status: str, to_status: str) -> None:
    """Refuse a correction that cannot mean anything. Pure, so it is testable.

    Deliberately permissive about *which* transition: the point of a
    correction is to express something the state machine could not work
    out, so any of the three states is reachable. What it will not accept
    is a status that does not exist, or one the container is already in —
    that would record a movement where nothing moved.
    """
    if to_status not in VALID_STATUSES:
        raise CorrectionRefused(
            f"'{to_status}' is not a status. Expected one of: {', '.join(VALID_STATUSES)}."
        )
    if current_status == to_status:
        raise CorrectionRefused(
            f"The container is already {to_status}. Nothing to correct."
        )


def apply_manual_correction(
    conn: psycopg.Connection,
    *,
    tid: str,
    to_status: str,
    reason: str,
    operator: str,
) -> Correction:
    """Set one container's status by hand, recording why and by whom.

    Does NOT commit: the caller decides the transaction, so a correction
    can be applied in the same breath as resolving the anomaly that
    prompted it.

    Applies to the named container only. Children are left alone, because
    a correction is a targeted decision by a person about one container,
    and quietly moving a pallet's boxes as a side effect would make them
    responsible for changes they did not ask for. Correct each container
    that genuinely needs it.
    """
    occurred_at = datetime.now(timezone.utc)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(LOCK_CONTAINER_SQL, (tid,))
        container = cur.fetchone()
        if container is None:
            raise ContainerNotFound(f"No container registered against TID '{tid}'.")

        check_target_status(container["status"], to_status)

        cur.execute(UPDATE_STATUS_SQL, (to_status, container["container_id"]))
        cur.execute(
            INSERT_MOVEMENT_SQL,
            (
                container["container_id"],
                container["status"],
                to_status,
                occurred_at,
                SOURCE_MANUAL,
                reason,
                operator,
            ),
        )

    return Correction(
        container_id=container["container_id"],
        tid=container["tid"],
        from_status=container["status"],
        to_status=to_status,
        reason=reason,
        operator=operator,
        occurred_at=occurred_at,
    )
