"""What a read at a portal should do to a container. SPEC.md section 4, layer 4.

    REGISTERED -> IN_STOCK -> DISPATCHED

Pure decision logic: given where a container was seen, which way it was
going, and what state it is in now, return what should happen. No
database, no clock, so every rule below is directly testable.

The single most important rule is that reading a container that is
already in the state it would move to does nothing at all. That is what
makes double-counting structurally impossible rather than something we
try to detect (SPEC.md 2.3).
"""

from dataclasses import dataclass

REGISTERED = "REGISTERED"
IN_STOCK = "IN_STOCK"
DISPATCHED = "DISPATCHED"

ENTRANCE = "ENTRANCE"
EXIT = "EXIT"

DIRECTION_IN = "IN"
DIRECTION_OUT = "OUT"
DIRECTION_UNKNOWN = "UNKNOWN"

# SPEC.md section 3, anomaly kinds.
UNKNOWN_TID = "UNKNOWN_TID"
ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
NO_DIRECTION = "NO_DIRECTION"
NO_SESSION = "NO_SESSION"
SHORT_PALLET = "SHORT_PALLET"


@dataclass(frozen=True)
class Decision:
    """What to do about one observation of one container."""

    to_status: str | None = None
    """The status to move to, or None to leave the container alone."""

    anomaly: str | None = None
    """An anomaly kind to record, or None."""

    reason: str = ""
    """Plain words for the log and the anomaly detail."""

    @property
    def is_no_op(self) -> bool:
        return self.to_status is None and self.anomaly is None


def inbound(status: str, reusable: bool) -> Decision:
    """Something arriving: through the entrance, or back in through the exit."""
    if status == REGISTERED:
        return Decision(to_status=IN_STOCK, reason="arrived into stock")

    if status == IN_STOCK:
        # Already in stock. Being read again means it is sitting near an
        # antenna, or someone walked past with it. Nothing has changed.
        return Decision(reason="already in stock")

    if status == DISPATCHED:
        if reusable:
            # SPEC.md section 4: reusable containers return to REGISTERED on
            # re-entry rather than being consumed. An empty pallet coming
            # back is normal and expected.
            return Decision(
                to_status=REGISTERED, reason="reusable container returned empty"
            )
        # A box that left the building is back. That may be a customer
        # return, which this system does not model, so it is surfaced for
        # a person to sort out rather than silently changing stock.
        return Decision(
            anomaly=ILLEGAL_TRANSITION,
            reason="dispatched container came back in; needs manual correction",
        )

    return Decision(
        anomaly=ILLEGAL_TRANSITION, reason=f"unrecognised status '{status}'"
    )


def outbound(status: str) -> Decision:
    """Something leaving through the exit."""
    if status == IN_STOCK:
        return Decision(to_status=DISPATCHED, reason="dispatched")

    if status == DISPATCHED:
        # SPEC.md section 3 gives this as the example of an illegal
        # transition. It has already gone; it cannot go again.
        return Decision(
            anomaly=ILLEGAL_TRANSITION, reason="already dispatched; read at exit again"
        )

    if status == REGISTERED:
        # Never booked into stock, yet on its way out of the building.
        # Possible in practice, but it skips a state, so a person should
        # look rather than have the system quietly invent the missing step.
        return Decision(
            anomaly=ILLEGAL_TRANSITION,
            reason="leaving without ever having been booked into stock",
        )

    return Decision(
        anomaly=ILLEGAL_TRANSITION, reason=f"unrecognised status '{status}'"
    )


def decide(*, portal: str, direction: str | None, status: str, reusable: bool) -> Decision:
    """The whole transition table in one place.

    The entrance carries no direction (SPEC.md section 4: a container
    entering storage is unambiguous, so the state machine decides alone).
    The exit does, from its IR beam gate, and without one nothing moves.
    """
    if portal == EXIT:
        if direction == DIRECTION_OUT:
            return outbound(status)
        if direction == DIRECTION_IN:
            return inbound(status, reusable)
        # UNKNOWN, or missing. The gate could not say which way it went, so
        # neither can we. Never guess: a wrong direction dispatches stock
        # that is still on the floor, or un-dispatches stock that has gone.
        return Decision(
            anomaly=NO_DIRECTION,
            reason=f"exit read with direction {direction!r}; nothing moved",
        )

    return inbound(status, reusable)
