"""The rules that turn many raw reads into one observation.

SPEC.md section 4 layer 2: group by (tid, portal), emit one observation
once the tag has been absent for the quiet period, and reject what falls
below the RSSI floor or the minimum read count.
"""

from datetime import datetime, timedelta, timezone

from tasker_rfid.services.debouncer.grouping import (
    Observation,
    has_gone_quiet,
    rejection_reason,
    start_group,
    starts_new_session,
    to_observation,
)

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
QUIET_S = 2.0


def a_group(reads=((0.0, -50),), tid="A7F3", portal="EXIT", monotonic=100.0):
    """Build a group from (seconds_after_T0, rssi) pairs."""
    first_offset, first_rssi = reads[0]
    group = start_group(tid, portal, T0 + timedelta(seconds=first_offset), first_rssi, monotonic)
    for offset, rssi in reads[1:]:
        group.add(T0 + timedelta(seconds=offset), rssi, monotonic)
    return group


def test_many_reads_become_one_observation():
    """The whole point of layer 2."""
    group = a_group([(i * 0.01, -50) for i in range(200)])
    observation = to_observation(group)
    assert observation.read_count == 200
    assert observation.first_read == T0
    assert abs(observation.duration_s - 1.99) < 0.001


def test_the_observation_keeps_the_strongest_signal_not_the_last():
    group = a_group([(0.0, -70), (0.5, -44), (1.0, -68)])
    assert to_observation(group).peak_rssi == -44


def test_reads_arriving_slightly_out_of_order_still_bound_the_event():
    """Rows come back ordered by insertion, which is not exactly read time."""
    group = a_group([(1.0, -50), (0.2, -50), (2.0, -50), (0.1, -50)])
    observation = to_observation(group)
    assert observation.first_read == T0 + timedelta(seconds=0.1)
    assert observation.last_read == T0 + timedelta(seconds=2.0)


def test_a_read_inside_the_quiet_period_continues_the_same_event():
    group = a_group([(0.0, -50)])
    assert not starts_new_session(group, T0 + timedelta(seconds=1.0), QUIET_S)


def test_a_read_after_the_quiet_period_starts_a_new_event():
    """Same tag, same portal, but it went away and came back."""
    group = a_group([(0.0, -50)])
    assert starts_new_session(group, T0 + timedelta(seconds=5.0), QUIET_S)


def test_the_quiet_period_boundary_is_exclusive():
    """Exactly the quiet period is still the same event; past it is not."""
    group = a_group([(0.0, -50)])
    assert not starts_new_session(group, T0 + timedelta(seconds=QUIET_S), QUIET_S)
    assert starts_new_session(group, T0 + timedelta(seconds=QUIET_S + 0.01), QUIET_S)


def test_a_read_arriving_out_of_order_does_not_split_the_event():
    group = a_group([(0.0, -50), (1.0, -50)])
    assert not starts_new_session(group, T0 + timedelta(seconds=0.5), QUIET_S)


def test_a_group_closes_when_nothing_arrives_at_all():
    """The last tag of a burst has no following read to end it."""
    group = a_group([(0.0, -50)], monotonic=100.0)
    assert not has_gone_quiet(group, 100.5, QUIET_S)
    assert has_gone_quiet(group, 103.0, QUIET_S)


def test_one_tags_timestamps_cannot_close_another_tags_group():
    """Regression: a shared 'latest read seen' clock shattered every pass.

    A reader with a wrong clock, or reads whose timestamps are spaced
    differently from their arrival (replaying a recording quickly), used
    to drag a global watermark forward and close everyone else's groups.
    Grouping is per (tid, portal), so this cannot happen.
    """
    quiet_tag = a_group([(0.0, -50)], tid="NORMAL", monotonic=100.0)
    far_future_read = T0 + timedelta(hours=1)

    # Another tag's wildly future timestamp is irrelevant to this group:
    # the only questions are its own next read, and its own silence.
    assert not starts_new_session(quiet_tag, T0 + timedelta(seconds=0.5), QUIET_S)
    assert not has_gone_quiet(quiet_tag, 100.1, QUIET_S)

    # And that future read only starts a new session for its OWN tag.
    its_own_group = a_group([(0.0, -50)], tid="BROKEN-CLOCK")
    assert starts_new_session(its_own_group, far_future_read, QUIET_S)


def test_a_strong_pass_is_kept():
    observation = Observation("A7F3", "EXIT", T0, T0 + timedelta(seconds=1.8), 190, -45)
    assert rejection_reason(observation, rssi_floor_dbm=-65, min_read_count=3) is None


def test_a_faint_tag_two_aisles_away_is_rejected():
    observation = Observation("FAR", "EXIT", T0, T0 + timedelta(seconds=1), 40, -78)
    reason = rejection_reason(observation, rssi_floor_dbm=-65, min_read_count=3)
    assert reason is not None and "below floor" in reason


def test_a_single_spurious_read_is_rejected():
    observation = Observation("BLIP", "EXIT", T0, T0, 1, -40)
    reason = rejection_reason(observation, rssi_floor_dbm=-65, min_read_count=3)
    assert reason is not None and "below minimum" in reason


def test_the_read_count_floor_is_inclusive():
    """min_read_count 3 means three reads is enough."""
    at_limit = Observation("X", "EXIT", T0, T0, 3, -40)
    assert rejection_reason(at_limit, rssi_floor_dbm=-65, min_read_count=3) is None


def test_the_rssi_floor_is_inclusive():
    """Exactly at the floor is not below it."""
    at_floor = Observation("X", "EXIT", T0, T0, 50, -65)
    assert rejection_reason(at_floor, rssi_floor_dbm=-65, min_read_count=3) is None


def test_a_reader_that_reports_no_rssi_is_judged_on_read_count_alone():
    """A read we cannot assess is not the same as one we have rejected."""
    no_rssi = Observation("X", "EXIT", T0, T0 + timedelta(seconds=1), 50, None)
    assert rejection_reason(no_rssi, rssi_floor_dbm=-65, min_read_count=3) is None

    no_rssi_and_too_few = Observation("X", "EXIT", T0, T0, 1, None)
    assert rejection_reason(no_rssi_and_too_few, rssi_floor_dbm=-65, min_read_count=3)


def test_a_parked_tag_never_going_quiet_costs_no_extra_memory():
    """Groups hold running totals, not the reads themselves."""
    group = a_group([(i * 0.08, -58) for i in range(5000)])
    observation = to_observation(group)
    assert observation.read_count == 5000
    assert observation.peak_rssi == -58
