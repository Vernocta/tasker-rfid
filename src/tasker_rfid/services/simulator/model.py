"""Turns a physical event into the individual reads a real reader would report.

A real UHF reader does not report "a box went past". It interrogates its
antennas dozens of times a second and reports every single time a tag
answers. One box walking through a portal in under two seconds produces
somewhere between fifty and three hundred separate reads, and the signal
strength of those reads rises as the box approaches the antenna and falls
as it leaves. Everything downstream of the reader exists to collapse that
noise back into one business event, so the simulator has to produce the
noise faithfully or we are testing against a fiction.

Everything in this module is a pure function: no MQTT, no clock, no
sleeping. Given the same seeded random number generator it produces
exactly the same reads, which is what lets the tests assert on it.
"""

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# The numbers that shape a read burst.
#
# These are deliberately module constants and not command-line flags. They
# describe how RF physics behaves, not how a particular test is set up.
# Adjust them here when the real reader arrives and we can measure it.
# ---------------------------------------------------------------------------

PASS_DURATION_S = 1.8
"""How long a tag stays inside the antenna field walking through a portal."""

PEAK_RSSI_DBM = -45
"""Signal strength at the closest point of approach, for an unobstructed tag."""

FALLOFF_DB = 25
"""How far the signal drops between the closest point and the edge of the field."""

SENSITIVITY_DBM = -75
"""Below this the tag does not harvest enough power to answer at all."""

CONFIDENT_DBM = -45
"""At or above this the tag answers almost every time it is asked."""

MAX_READ_PROBABILITY = 0.95
"""Even a tag sitting on the antenna misses the occasional interrogation."""

BASE_ATTEMPT_HZ = 150.0
"""Interrogations per second aimed at a single tag when it is alone in the field."""

READER_ATTEMPT_CAP_HZ = 1600.0
"""Total interrogations per second the reader can issue across all tags.

A reader time-slices between the tags it can see. One tag alone gets
interrogated constantly; fifty tags on a pallet each get a fraction of the
reader's attention. Without this cap a fifty-box pallet would generate an
impossible number of reads and the throughput tests would be meaningless.
"""

MIN_ATTEMPT_HZ = 8.0
"""Floor, so a tag in a very crowded field still gets looked at occasionally."""

ANTENNA_LEAD_S = 0.15
"""How much earlier the first antenna peaks than the last one.

The antennas of a portal are physically separated along the direction of
travel, so a tag reaches one before the other. This is small but it is the
only thing in the raw read stream that distinguishes forwards from
backwards, which is what `sim reverse` depends on.
"""

RSSI_NOISE_DB = 2.0
"""Reflections, people walking past, the tag rotating. Real RSSI is never smooth."""

STRAY_ATTEMPT_HZ = 12.0
"""Interrogation rate for a tag parked at the edge of the field, not moving through."""

STRAY_RSSI_DBM = -58
"""A parked box is off to the side, so it is heard steadily but not loudly."""

STRAY_DRIFT_DB = 4.0
"""Slow swing in a parked tag's signal as the environment around it changes."""

STRAY_DRIFT_PERIOD_S = 17.0


@dataclass(frozen=True)
class Read:
    """One interrogation that a tag answered. Mirrors a row of reads_raw."""

    tid: str
    epc: str | None
    reader_id: str
    antenna_id: int
    rssi: int
    read_at: datetime


@dataclass(frozen=True)
class Tag:
    """A tag the simulator is pretending exists."""

    tid: str
    epc: str | None = None
    attenuation_db: float = 0.0
    """How much weaker this tag reads than an unobstructed one.

    A box in the middle of a shrink-wrapped pallet is shadowed by the boxes
    around it. That is why pallets lose boxes, and why SHORT_PALLET is a
    failure mode the spec calls out.
    """


def rssi_at(offset_s: float, half_width_s: float, peak_rssi: float) -> float:
    """Signal strength this many seconds away from the closest point of approach.

    A parabola: strongest in the middle of the pass, FALLOFF_DB weaker at
    the edges of the field. Real antenna patterns are messier than this, but
    the shape that matters downstream — rise, peak, fall — is right.
    """
    normalised = offset_s / half_width_s
    return peak_rssi - FALLOFF_DB * normalised * normalised


def read_probability(rssi: float) -> float:
    """Chance that a tag answers a single interrogation at this signal strength.

    Passive tags run on the energy in the reader's own signal. Weak signal
    means the chip may not power up at all, which is why the edges of a pass
    are sparse and the middle is dense.
    """
    if rssi <= SENSITIVITY_DBM:
        return 0.0
    ramp = (rssi - SENSITIVITY_DBM) / (CONFIDENT_DBM - SENSITIVITY_DBM)
    return min(ramp, MAX_READ_PROBABILITY)


def attempt_hz_per_tag(tag_count: int) -> float:
    """Split the reader's fixed interrogation budget across the tags in the field."""
    if tag_count <= 0:
        return BASE_ATTEMPT_HZ
    shared = READER_ATTEMPT_CAP_HZ / tag_count
    return max(MIN_ATTEMPT_HZ, min(BASE_ATTEMPT_HZ, shared))


def pass_reads(
    tag: Tag,
    reader_id: str,
    antennas: list[int],
    start: datetime,
    rng: random.Random,
    *,
    duration_s: float = PASS_DURATION_S,
    attempt_hz: float = BASE_ATTEMPT_HZ,
    reverse: bool = False,
) -> list[Read]:
    """Every read produced by one tag moving through one portal.

    `reverse` swaps which antenna the tag reaches first, which is what
    "carried back out of the entrance" looks like in the raw read stream.
    """
    half_width = duration_s / 2
    centre = duration_s / 2
    peak = PEAK_RSSI_DBM - tag.attenuation_db
    per_antenna_hz = attempt_hz / len(antennas)
    step = 1.0 / per_antenna_hz
    last_index = len(antennas) - 1

    reads: list[Read] = []
    for index, antenna_id in enumerate(antennas):
        # Spread the antennas either side of the middle of the pass, so the
        # first one the tag reaches peaks earliest.
        lead = ANTENNA_LEAD_S * (index - last_index / 2)
        if reverse:
            lead = -lead
        antenna_centre = centre + lead

        # A reader interrogates one antenna at a time, so stagger their
        # attempts rather than firing them all on the same instant.
        elapsed = (index / len(antennas)) * step
        while elapsed < duration_s:
            rssi = rssi_at(elapsed - antenna_centre, half_width, peak)
            if rng.random() < read_probability(rssi):
                reads.append(
                    Read(
                        tid=tag.tid,
                        epc=tag.epc,
                        reader_id=reader_id,
                        antenna_id=antenna_id,
                        rssi=round(rssi + rng.gauss(0, RSSI_NOISE_DB)),
                        read_at=start + timedelta(seconds=elapsed),
                    )
                )
            elapsed += step

    reads.sort(key=lambda r: r.read_at)
    return reads


def stray_reads(
    tag: Tag,
    reader_id: str,
    antennas: list[int],
    start: datetime,
    duration_s: float,
    rng: random.Random,
) -> list[Read]:
    """A box parked within range of an antenna and never moving.

    This is the failure mode at the top of SPEC.md section 7. The tag is not
    passing anything, so there is no rise and fall — just a steady signal
    that drifts slowly, for as long as the box sits there. Nothing about
    these reads says "stationary"; that is exactly the problem the RSSI
    floor and state idempotency have to solve downstream.
    """
    step = 1.0 / STRAY_ATTEMPT_HZ
    base = STRAY_RSSI_DBM - tag.attenuation_db

    reads: list[Read] = []
    elapsed = 0.0
    tick = 0
    while elapsed < duration_s:
        drift = STRAY_DRIFT_DB * math.sin(2 * math.pi * elapsed / STRAY_DRIFT_PERIOD_S)
        rssi = base + drift
        if rng.random() < read_probability(rssi):
            reads.append(
                Read(
                    tid=tag.tid,
                    epc=tag.epc,
                    reader_id=reader_id,
                    antenna_id=antennas[tick % len(antennas)],
                    rssi=round(rssi + rng.gauss(0, RSSI_NOISE_DB)),
                    read_at=start + timedelta(seconds=elapsed),
                )
            )
        elapsed += step
        tick += 1

    return reads


def merge(*read_lists: list[Read]) -> list[Read]:
    """Combine reads from several tags into one time-ordered stream."""
    combined: list[Read] = []
    for reads in read_lists:
        combined.extend(reads)
    combined.sort(key=lambda r: r.read_at)
    return combined


# ---------------------------------------------------------------------------
# The IR beam gate at the exit portal.
#
# Two beams a few centimetres apart across the doorway. INNER faces the
# warehouse, OUTER faces the street. Whatever passes breaks one, then the
# other, and the order says which way it went. SPEC.md section 4 layer 3.
# ---------------------------------------------------------------------------

BEAM_SEPARATION_S = 0.04
"""Time between the two beams breaking.

A few centimetres apart, crossed at roughly walking pace. Small, but it
is the entire direction signal, so the gap has to be modelled honestly.
"""

BEAM_SEPARATION_JITTER_S = 0.015
"""People and forklifts do not travel at a constant speed."""

BOX_BEAM_BLOCKED_S = 0.35
"""How long a single box keeps a beam broken as it passes through."""

PALLET_BEAM_BLOCKED_S = 1.0
"""A loaded pallet is longer, so it blocks each beam for longer."""

BEAM_INNER = "INNER"
BEAM_OUTER = "OUTER"
BEAM_BROKEN = "BROKEN"
BEAM_CLEAR = "CLEAR"


@dataclass(frozen=True)
class GateEvent:
    """One beam changing state, as the gate controller would report it."""

    gate_id: str
    beam: str
    state: str
    occurred_at: datetime


def gate_crossing(
    gate_id: str,
    start: datetime,
    rng: random.Random,
    *,
    duration_s: float = PASS_DURATION_S,
    blocked_s: float = BOX_BEAM_BLOCKED_S,
    reverse: bool = False,
) -> list[GateEvent]:
    """The four beam events produced by something crossing the gate.

    Timed to the middle of the tag pass, because the beams sit at the
    centre of the portal while the RF field reaches well beyond it. That
    is why a crossing window always falls inside its observation window.

    Going out, INNER breaks first. `reverse` swaps them, which is what
    something being carried back in through the exit looks like.
    """
    centre = duration_s / 2
    separation = max(
        0.005, BEAM_SEPARATION_S + rng.gauss(0, BEAM_SEPARATION_JITTER_S)
    )

    first, second = (BEAM_INNER, BEAM_OUTER)
    if reverse:
        first, second = second, first

    first_at = centre - separation / 2
    second_at = centre + separation / 2

    def event(beam: str, state: str, offset_s: float) -> GateEvent:
        return GateEvent(
            gate_id=gate_id,
            beam=beam,
            state=state,
            occurred_at=start + timedelta(seconds=offset_s),
        )

    events = [
        event(first, BEAM_BROKEN, first_at),
        event(second, BEAM_BROKEN, second_at),
        event(first, BEAM_CLEAR, first_at + blocked_s),
        event(second, BEAM_CLEAR, second_at + blocked_s),
    ]
    events.sort(key=lambda e: e.occurred_at)
    return events
