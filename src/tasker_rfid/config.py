"""Reads config/tasker.yaml — the runtime tuning file from SPEC.md section 8.

Connection details and secrets live in .env. Everything about how the RF
side behaves lives in the YAML, and this module is the one place that
reads it, so the simulator and the debouncer cannot drift apart on what
"the exit portal" means.

Every value has a default equal to the one in SPEC.md section 8, so a
missing or partial config file degrades to the documented behaviour
instead of crashing.
"""

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import yaml

DEFAULT_QUIET_PERIOD_MS = 2000
DEFAULT_RSSI_FLOOR_DBM = -65
DEFAULT_MIN_READ_COUNT = 3
DEFAULT_ANTENNAS = {"ENTRANCE": [1, 2], "EXIT": [3, 4]}
DEFAULT_GATE_IDS = {"ENTRANCE": None, "EXIT": "GATE-EXIT"}
DEFAULT_IR_GATE_TIMEOUT_MS = 3000

BEAM_INNER = "INNER"
BEAM_OUTER = "OUTER"
BEAMS = (BEAM_INNER, BEAM_OUTER)

DIRECTION_IN = "IN"
DIRECTION_OUT = "OUT"
DIRECTION_UNKNOWN = "UNKNOWN"

PORTALS = ("ENTRANCE", "EXIT")


@dataclass(frozen=True)
class Filters:
    """SPEC.md section 8, `filters:`."""

    quiet_period_ms: int = DEFAULT_QUIET_PERIOD_MS
    rssi_floor_dbm: int = DEFAULT_RSSI_FLOOR_DBM
    min_read_count: int = DEFAULT_MIN_READ_COUNT

    @property
    def quiet_period_s(self) -> float:
        return self.quiet_period_ms / 1000.0


def config_path() -> Path:
    """Where the YAML lives. TASKER_CONFIG_PATH in .env overrides it."""
    from_env = os.getenv("TASKER_CONFIG_PATH")
    if from_env:
        return Path(from_env)
    # src/tasker_rfid/config.py -> repo root
    return Path(__file__).resolve().parents[2] / "config" / "tasker.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load the YAML. Returns an empty dict if it is missing or unreadable."""
    target = path or config_path()
    try:
        loaded = yaml.safe_load(target.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_filters(config: dict[str, Any] | None = None) -> Filters:
    """The RSSI floor, minimum read count and quiet period."""
    section = (config if config is not None else load_config()).get("filters") or {}
    return Filters(
        quiet_period_ms=int(section.get("quiet_period_ms", DEFAULT_QUIET_PERIOD_MS)),
        rssi_floor_dbm=int(section.get("rssi_floor_dbm", DEFAULT_RSSI_FLOOR_DBM)),
        min_read_count=int(section.get("min_read_count", DEFAULT_MIN_READ_COUNT)),
    )


def antennas_for_portal(portal: str, config: dict[str, Any] | None = None) -> list[int]:
    """Which antenna numbers belong to a portal."""
    portal = portal.upper()
    section = (config if config is not None else load_config()).get("portals") or {}
    antennas = (section.get(portal.lower()) or {}).get("antennas")
    if antennas:
        return [int(a) for a in antennas]
    return list(DEFAULT_ANTENNAS[portal])


def portal_by_antenna(config: dict[str, Any] | None = None) -> dict[int, str]:
    """The reverse lookup: antenna number to portal name.

    A reader reports antenna numbers; reads_raw stores antenna numbers.
    "Portal" is a fact about the warehouse layout, and this mapping is
    where the two meet (SPEC.md section 3: observations has a portal
    column, reads_raw does not).
    """
    loaded = config if config is not None else load_config()
    mapping: dict[int, str] = {}
    for portal in PORTALS:
        for antenna in antennas_for_portal(portal, loaded):
            mapping[antenna] = portal
    return mapping


def gate_id_for_portal(portal: str, config: dict[str, Any] | None = None) -> str | None:
    """The IR gate installed at this portal, or None if it has none.

    Only the exit is gated. SPEC.md section 4: the entrance resolves
    direction from the state machine alone, so it needs no beams.
    """
    portal = portal.upper()
    section = (config if config is not None else load_config()).get("portals") or {}
    configured = (section.get(portal.lower()) or {}).get("gate_id")
    if configured:
        return str(configured)
    return DEFAULT_GATE_IDS.get(portal)


def portal_by_gate(config: dict[str, Any] | None = None) -> dict[str, str]:
    """The reverse lookup: gate id to portal name.

    A gate knows it is "GATE-EXIT"; which portal that is belongs to the
    warehouse layout, the same way antenna numbers do.
    """
    loaded = config if config is not None else load_config()
    mapping: dict[str, str] = {}
    for portal in PORTALS:
        gate = gate_id_for_portal(portal, loaded)
        if gate:
            mapping[gate] = portal
    return mapping


def ir_gate_timeout_ms(portal: str, config: dict[str, Any] | None = None) -> int:
    """How long the second beam has to break before the crossing is abandoned."""
    section = (config if config is not None else load_config()).get("portals") or {}
    value = (section.get(portal.lower()) or {}).get("ir_gate_timeout_ms")
    return int(value) if value else DEFAULT_IR_GATE_TIMEOUT_MS


@dataclass(frozen=True)
class Health:
    """SPEC.md section 8, `health:`."""

    no_read_alert_hours: int = 4
    working_hours: str = "08:00-18:00"
    timezone: str = "America/Argentina/Buenos_Aires"

    def working_hours_range(self) -> tuple[time, time]:
        """Parse "08:00-18:00" into two times."""
        try:
            opens, closes = self.working_hours.split("-")
            return (
                time.fromisoformat(opens.strip()),
                time.fromisoformat(closes.strip()),
            )
        except ValueError:
            return time(8, 0), time(18, 0)


def load_health(config: dict[str, Any] | None = None) -> Health:
    section = (config if config is not None else load_config()).get("health") or {}
    defaults = Health()
    return Health(
        no_read_alert_hours=int(
            section.get("no_read_alert_hours", defaults.no_read_alert_hours)
        ),
        working_hours=str(section.get("working_hours", defaults.working_hours)),
        timezone=str(section.get("timezone", defaults.timezone)),
    )
