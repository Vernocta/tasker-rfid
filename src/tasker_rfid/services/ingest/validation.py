"""Turns an MQTT payload into a row for reads_raw, or rejects it.

SPEC.md section 4, layer 1: "Subscribe to MQTT, validate, insert into
reads_raw. Nothing else."

This module is the "validate" half. It is pure — no database, no network,
no clock — so every rule below can be tested directly.

The guiding rule when deciding whether to reject: a lost read is
unrecoverable (SPEC.md 2.4), but reads_raw is an append-only replay log,
so a read stored with a slightly odd value can always be reprocessed
later. So we reject only what genuinely cannot be stored, and accept and
warn about everything else.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Postgres SMALLINT bounds. antenna_id and rssi are SMALLINT in SPEC.md
# section 3, so anything outside this range cannot physically be stored.
SMALLINT_MIN = -32768
SMALLINT_MAX = 32767

REQUIRED_FIELDS = ("tid", "reader_id", "antenna_id", "read_at")
GATE_REQUIRED_FIELDS = ("gate_id", "beam", "state", "occurred_at")

BEAMS = ("INNER", "OUTER")
BEAM_STATES = ("BROKEN", "CLEAR")


class InvalidRead(Exception):
    """The payload cannot become a reads_raw row. Says why in plain words."""


@dataclass(frozen=True)
class RawRead:
    """One validated read, ready to insert. Mirrors a row of reads_raw."""

    tid: str
    epc: str | None
    reader_id: str
    antenna_id: int
    rssi: int | None
    read_at: datetime

    def as_row(self) -> tuple:
        """The values, in the column order the INSERT uses."""
        return (self.tid, self.epc, self.reader_id, self.antenna_id, self.rssi, self.read_at)


def _text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise InvalidRead(f"{field} must be text, got {type(value).__name__}")
    value = value.strip()
    if not value:
        raise InvalidRead(f"{field} is empty")
    return value


def _optional_text(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidRead(f"{field} must be text or null, got {type(value).__name__}")
    value = value.strip()
    return value or None


def _smallint(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    # bool is a subclass of int in Python, so check it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRead(f"{field} must be a whole number, got {type(value).__name__}")
    if not SMALLINT_MIN <= value <= SMALLINT_MAX:
        raise InvalidRead(f"{field} {value} is outside the range the column can store")
    return value


def _optional_smallint(payload: dict[str, Any], field: str) -> int | None:
    if payload.get(field) is None:
        return None
    return _smallint(payload, field)


def _timestamp(payload: dict[str, Any], field: str) -> tuple[datetime, str | None]:
    """Parse an ISO 8601 timestamp. Returns the value and any warning about it."""
    value = payload.get(field)
    if not isinstance(value, str):
        raise InvalidRead(f"{field} must be an ISO 8601 timestamp string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InvalidRead(f"{field} '{value}' is not a valid ISO 8601 timestamp") from None

    if parsed.tzinfo is None:
        # A reader sending timestamps with no timezone is misconfigured. We
        # assume UTC rather than reject, because a read with a questionable
        # timestamp can be corrected by replaying reads_raw, while a rejected
        # read is gone forever.
        return parsed.replace(tzinfo=timezone.utc), (
            f"{field} '{value}' has no timezone; stored as UTC. "
            "Check the reader's clock configuration."
        )
    return parsed, None


def parse_read(payload: dict[str, Any]) -> tuple[RawRead, list[str]]:
    """Validate a decoded MQTT payload.

    Returns the row to insert plus any non-fatal warnings worth logging.
    Raises InvalidRead if it cannot become a row at all.

    Unknown extra fields are ignored on purpose, so a future reader
    firmware that adds a field does not stop the line.
    """
    if not isinstance(payload, dict):
        raise InvalidRead(f"payload must be a JSON object, got {type(payload).__name__}")

    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise InvalidRead(f"missing required field(s): {', '.join(missing)}")

    read_at, warning = _timestamp(payload, "read_at")
    read = RawRead(
        tid=_text(payload, "tid"),
        epc=_optional_text(payload, "epc"),
        reader_id=_text(payload, "reader_id"),
        antenna_id=_smallint(payload, "antenna_id"),
        rssi=_optional_smallint(payload, "rssi"),
        read_at=read_at,
    )
    return read, [warning] if warning else []


@dataclass(frozen=True)
class GateEvent:
    """One IR beam changing state. Mirrors a row of gate_events."""

    gate_id: str
    beam: str
    state: str
    occurred_at: datetime

    def as_row(self) -> tuple:
        return (self.gate_id, self.beam, self.state, self.occurred_at)


def _one_of(payload: dict[str, Any], field: str, allowed: tuple[str, ...]) -> str:
    value = _text(payload, field).upper()
    if value not in allowed:
        raise InvalidRead(f"{field} must be one of {', '.join(allowed)}, got '{value}'")
    return value


def parse_gate_event(payload: dict[str, Any]) -> tuple[GateEvent, list[str]]:
    """Validate a beam-break message.

    Same bias as reads: reject only what cannot become a row. A beam name
    or state outside the known set is refused, because guessing which beam
    broke would invent a direction, and a wrong direction is worse than no
    direction at all.
    """
    if not isinstance(payload, dict):
        raise InvalidRead(f"payload must be a JSON object, got {type(payload).__name__}")

    missing = [f for f in GATE_REQUIRED_FIELDS if f not in payload]
    if missing:
        raise InvalidRead(f"missing required field(s): {', '.join(missing)}")

    occurred_at, warning = _timestamp(payload, "occurred_at")
    event = GateEvent(
        gate_id=_text(payload, "gate_id"),
        beam=_one_of(payload, "beam", BEAMS),
        state=_one_of(payload, "state", BEAM_STATES),
        occurred_at=occurred_at,
    )
    return event, [warning] if warning else []
