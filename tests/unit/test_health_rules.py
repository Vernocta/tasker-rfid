"""When a silent reader is a fault, and when it is just closing time.

SPEC.md section 7: alert if no read in four hours during working hours.
"""

from tasker_rfid.config import load_health
from tasker_rfid.services.api.routers.health import reader_has_gone_quiet

ALERT_HOURS = 4


def test_a_reader_silent_all_morning_is_a_fault():
    assert reader_has_gone_quiet(5 * 60, within_working_hours=True, no_read_alert_hours=ALERT_HOURS)


def test_a_reader_that_spoke_recently_is_fine():
    assert not reader_has_gone_quiet(10, True, ALERT_HOURS)


def test_the_four_hour_boundary():
    assert not reader_has_gone_quiet(4 * 60, True, ALERT_HOURS)
    assert reader_has_gone_quiet(4 * 60 + 1, True, ALERT_HOURS)


def test_silence_outside_working_hours_is_not_a_fault():
    """The warehouse is shut. Silence is what a healthy reader produces."""
    assert not reader_has_gone_quiet(20 * 60, within_working_hours=False, no_read_alert_hours=ALERT_HOURS)


def test_a_reader_that_has_never_reported_is_handled_separately():
    """No last-read time is not 'stale'; it is 'never seen', warned about elsewhere."""
    assert not reader_has_gone_quiet(None, True, ALERT_HOURS)


def test_the_alert_threshold_comes_from_the_config_file():
    """SPEC.md section 8, health.no_read_alert_hours."""
    assert load_health().no_read_alert_hours == 4
    assert load_health().working_hours == "08:00-18:00"
