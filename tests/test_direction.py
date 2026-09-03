"""Turning IR beam breaks into a direction. SPEC.md section 4, layer 3.

INNER faces the warehouse, OUTER faces the street. Break INNER then OUTER
and something left; break OUTER then INNER and something came back in.
"""

from datetime import datetime, timedelta, timezone

from tasker_rfid.services.debouncer.direction import (
    crossings_from_events,
    direction_for_window,
)

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
TIMEOUT_S = 3.0


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def outbound(start=0.0, separation=0.04, blocked=0.35):
    """The four events of something leaving: INNER first."""
    return [
        ("INNER", "BROKEN", at(start)),
        ("OUTER", "BROKEN", at(start + separation)),
        ("INNER", "CLEAR", at(start + blocked)),
        ("OUTER", "CLEAR", at(start + separation + blocked)),
    ]


def inbound(start=0.0, separation=0.04, blocked=0.35):
    """The same thing coming back in: OUTER first."""
    return [
        ("OUTER", "BROKEN", at(start)),
        ("INNER", "BROKEN", at(start + separation)),
        ("OUTER", "CLEAR", at(start + blocked)),
        ("INNER", "CLEAR", at(start + separation + blocked)),
    ]


def test_inner_beam_first_means_something_left():
    crossings = crossings_from_events(outbound(), TIMEOUT_S)
    assert len(crossings) == 1
    assert crossings[0].direction == "OUT"


def test_outer_beam_first_means_something_came_in():
    crossings = crossings_from_events(inbound(), TIMEOUT_S)
    assert len(crossings) == 1
    assert crossings[0].direction == "IN"


def test_the_crossing_spans_first_break_to_last_clear():
    crossings = crossings_from_events(outbound(separation=0.04, blocked=0.35), TIMEOUT_S)
    crossing = crossings[0]
    assert crossing.started_at == at(0.0)
    assert crossing.ended_at == at(0.39)


def test_two_loads_through_the_door_are_two_crossings():
    events = outbound(start=0.0) + inbound(start=10.0)
    crossings = crossings_from_events(events, TIMEOUT_S)
    assert [c.direction for c in crossings] == ["OUT", "IN"]


def test_one_beam_broken_alone_gives_no_direction():
    """Someone reached through the doorway. Nothing passed."""
    events = [("INNER", "BROKEN", at(0.0)), ("INNER", "CLEAR", at(0.5))]
    crossings = crossings_from_events(events, TIMEOUT_S)
    assert len(crossings) == 1
    assert crossings[0].direction is None


def test_the_second_beam_arriving_too_late_gives_no_direction():
    """Past ir_gate_timeout_ms these are not one movement."""
    events = [
        ("INNER", "BROKEN", at(0.0)),
        ("OUTER", "BROKEN", at(TIMEOUT_S + 1.0)),
        ("INNER", "CLEAR", at(TIMEOUT_S + 2.0)),
        ("OUTER", "CLEAR", at(TIMEOUT_S + 3.0)),
    ]
    crossings = crossings_from_events(events, TIMEOUT_S)
    assert crossings[0].direction is None


def test_no_events_at_all_is_no_crossings():
    assert crossings_from_events([], TIMEOUT_S) == []


# ---------------------------------------------------------------------------
# Matching a crossing to an observation
# ---------------------------------------------------------------------------


def test_a_tag_read_across_the_crossing_takes_its_direction():
    """The RF field is wider than the beams, so reads bracket the crossing."""
    crossings = crossings_from_events(outbound(start=0.7), TIMEOUT_S)
    assert direction_for_window(at(0.0), at(1.8), crossings) == "OUT"


def test_every_tag_on_one_pallet_gets_the_same_direction():
    """One pallet is one crossing however many tags ride on it."""
    crossings = crossings_from_events(outbound(start=0.8, blocked=1.0), TIMEOUT_S)
    windows = [(at(0.0), at(1.9)), (at(0.1), at(1.8)), (at(0.05), at(2.0))]
    assert [direction_for_window(a, b, crossings) for a, b in windows] == ["OUT"] * 3


def test_an_observation_with_no_crossing_gets_nothing():
    """A tag read at the portal that never went through the door."""
    crossings = crossings_from_events(outbound(start=100.0), TIMEOUT_S)
    assert direction_for_window(at(0.0), at(1.8), crossings) is None


def test_the_nearer_crossing_wins_when_two_are_close():
    events = outbound(start=1.0) + inbound(start=3.0)
    crossings = crossings_from_events(events, TIMEOUT_S)
    # An observation centred on the first crossing.
    assert direction_for_window(at(0.4), at(2.0), crossings) == "OUT"
    # And one centred on the second.
    assert direction_for_window(at(2.6), at(4.2), crossings) == "IN"


def test_a_crossing_where_the_gate_could_not_say_returns_none():
    events = [("INNER", "BROKEN", at(1.0)), ("INNER", "CLEAR", at(1.5))]
    crossings = crossings_from_events(events, TIMEOUT_S)
    assert direction_for_window(at(0.0), at(2.5), crossings) is None
