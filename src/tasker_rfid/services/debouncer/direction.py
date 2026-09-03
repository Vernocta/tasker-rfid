"""Working out which way something went, from IR beam breaks.

SPEC.md section 4, layer 3:

    Exit: IR beam gating. Reader inventories only during a beam-break
    window; break order gives direction.

Two beams a few centimetres apart across the doorway. INNER faces the
warehouse, OUTER faces the street. Break INNER then OUTER and something
left; break OUTER then INNER and something came back in.

Only the exit is gated. The entrance resolves direction from the state
machine alone, so entrance observations keep direction NULL.

Pure functions: given beam events and observation windows, produce
directions. No database, no clock.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ...config import BEAM_INNER, BEAM_OUTER, DIRECTION_IN, DIRECTION_OUT

BEAM_BROKEN = "BROKEN"
BEAM_CLEAR = "CLEAR"

# Which beam breaking first means what.
FIRST_BEAM_DIRECTION = {
    BEAM_INNER: DIRECTION_OUT,  # warehouse side first: on its way out
    BEAM_OUTER: DIRECTION_IN,   # street side first: coming in
}


@dataclass(frozen=True)
class Crossing:
    """Something passing through the gate once."""

    direction: str | None
    """IN, OUT, or None when only one beam ever broke."""

    started_at: datetime
    ended_at: datetime

    @property
    def midpoint(self) -> datetime:
        return self.started_at + (self.ended_at - self.started_at) / 2


def crossings_from_events(
    events: list[tuple[str, str, datetime]], timeout_s: float
) -> list[Crossing]:
    """Reconstruct crossings from (beam, state, occurred_at) in time order.

    A crossing opens when a beam breaks with none already broken. The
    direction is decided by the first beam to break, confirmed when the
    other one follows within `timeout_s`. The crossing closes once both
    beams are clear again.

    If the second beam never breaks in time, the crossing still closes but
    with direction None — something interrupted one beam and did not pass
    through. SPEC.md section 3 calls that a NO_DIRECTION anomaly, which is
    the state engine's to raise; here it simply is not a direction.
    """
    crossings: list[Crossing] = []

    broken: dict[str, datetime] = {}
    first_beam: str | None = None
    started_at: datetime | None = None
    direction: str | None = None

    for beam, state, occurred_at in events:
        if state == BEAM_BROKEN:
            if not broken:
                # Nothing was blocking the gate: a new crossing begins.
                first_beam = beam
                started_at = occurred_at
                direction = None
            elif (
                direction is None
                and first_beam is not None
                and beam != first_beam
                and started_at is not None
                and (occurred_at - started_at).total_seconds() <= timeout_s
            ):
                direction = FIRST_BEAM_DIRECTION[first_beam]
            broken[beam] = occurred_at

        elif state == BEAM_CLEAR:
            broken.pop(beam, None)
            if not broken and started_at is not None:
                crossings.append(
                    Crossing(
                        direction=direction,
                        started_at=started_at,
                        ended_at=occurred_at,
                    )
                )
                first_beam = None
                started_at = None
                direction = None

    return crossings


def direction_for_window(
    first_read: datetime,
    last_read: datetime,
    crossings: list[Crossing],
    tolerance_s: float = 0.0,
) -> str | None:
    """Which crossing, if any, this observation belongs to.

    The RF field reaches well beyond the beams, so a tag is read before and
    after the crossing and the crossing window sits inside the observation
    window. Overlap is therefore the test.

    Two loads through the door in quick succession can both overlap one
    observation, so the nearest by midpoint wins.
    """
    if not crossings:
        return None

    margin = timedelta(seconds=tolerance_s)
    window_start = first_read - margin
    window_end = last_read + margin

    overlapping = [
        crossing
        for crossing in crossings
        if crossing.started_at <= window_end and crossing.ended_at >= window_start
    ]
    if not overlapping:
        return None

    observation_midpoint = first_read + (last_read - first_read) / 2
    nearest = min(
        overlapping,
        key=lambda c: abs((c.midpoint - observation_midpoint).total_seconds()),
    )
    return nearest.direction
