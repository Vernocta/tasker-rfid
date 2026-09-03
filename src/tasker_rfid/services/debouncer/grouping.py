"""Collapsing raw reads into observations. SPEC.md section 4, layer 2.

    Group by (tid, portal). Emit one observation when a TID has been
    absent for quiet_period_ms (default 2000).

One box through a portal produces a couple of hundred rows in reads_raw.
All of them describe a single physical event. This module turns the many
back into the one.

Pure: no database, no MQTT, no wall clock of its own. A group keeps only
running totals, never the reads themselves, so a tag parked in the field
for an hour costs the same memory as one that walked past.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class OpenGroup:
    """Reads seen so far for one (tid, portal) that has not gone quiet yet."""

    tid: str
    portal: str
    first_read: datetime
    last_read: datetime
    read_count: int
    peak_rssi: int | None
    last_touched: float
    """Monotonic clock reading when this group last received a read.

    Kept alongside last_read so a group can be closed either because the
    reads themselves moved on (normal running, and replay of old data) or
    because nothing has arrived for a while (the reads simply stopped).
    """

    def add(self, read_at: datetime, rssi: int | None, monotonic_now: float) -> None:
        # reads_raw is ordered by insertion, which is very nearly but not
        # exactly ordered by read_at, so take the extremes rather than
        # assuming the last row seen is the latest.
        if read_at < self.first_read:
            self.first_read = read_at
        if read_at > self.last_read:
            self.last_read = read_at
        self.read_count += 1
        if rssi is not None and (self.peak_rssi is None or rssi > self.peak_rssi):
            self.peak_rssi = rssi
        self.last_touched = monotonic_now


@dataclass(frozen=True)
class Observation:
    """One physical event, ready to insert into observations."""

    tid: str
    portal: str
    first_read: datetime
    last_read: datetime
    read_count: int
    peak_rssi: int | None

    @property
    def duration_s(self) -> float:
        return (self.last_read - self.first_read).total_seconds()


def start_group(
    tid: str, portal: str, read_at: datetime, rssi: int | None, monotonic_now: float
) -> OpenGroup:
    return OpenGroup(
        tid=tid,
        portal=portal,
        first_read=read_at,
        last_read=read_at,
        read_count=1,
        peak_rssi=rssi,
        last_touched=monotonic_now,
    )


def starts_new_session(
    group: OpenGroup, read_at: datetime, quiet_period_s: float
) -> bool:
    """Does this read belong to a new event rather than the open one?

    The quiet period is measured per (tid, portal), against that group's
    own last read. A tag that goes quiet at a portal and comes back later
    has done two separate things, and gets two observations.

    Deliberately per-group and not against a shared "latest read seen"
    clock. A shared clock would let one reader with a wrong clock, or one
    stream of timestamps spaced differently from their arrival, decide
    that every other tag had gone quiet — which shatters each pass into
    fragments.
    """
    return (read_at - group.last_read).total_seconds() > quiet_period_s


def has_gone_quiet(
    group: OpenGroup, monotonic_now: float, quiet_period_s: float
) -> bool:
    """Has nothing arrived for this group in the quiet period?

    This is what closes the last group of a burst, where there is no
    following read to show that the tag has moved on.
    """
    return (monotonic_now - group.last_touched) > quiet_period_s


def to_observation(group: OpenGroup) -> Observation:
    return Observation(
        tid=group.tid,
        portal=group.portal,
        first_read=group.first_read,
        last_read=group.last_read,
        read_count=group.read_count,
        peak_rssi=group.peak_rssi,
    )


def rejection_reason(
    observation: Observation, rssi_floor_dbm: int, min_read_count: int
) -> str | None:
    """Why this observation should be thrown away, or None to keep it.

    SPEC.md section 4: reject when peak_rssi is below the floor or
    read_count is below the minimum. Both exist to discard tags that were
    never really at the portal — a pallet two aisles away catching a
    stray reflection, or a single spurious read.

    A tag that reported no RSSI at all cannot be judged against the floor,
    so it is kept and left to the read count. Silently discarding it would
    be worse: a read we cannot assess is not the same as a read we have
    assessed and rejected.
    """
    if observation.read_count < min_read_count:
        return (
            f"read_count {observation.read_count} below minimum {min_read_count}"
        )
    if observation.peak_rssi is not None and observation.peak_rssi < rssi_floor_dbm:
        return (
            f"peak_rssi {observation.peak_rssi} dBm below floor {rssi_floor_dbm} dBm"
        )
    return None
