"""What ingest accepts, what it refuses, and why.

The bias throughout: reject only what genuinely cannot be stored. A read
kept with an odd value can be reprocessed from reads_raw later; a rejected
read is gone forever (SPEC.md 2.4).
"""

from datetime import timezone

import pytest

from tasker_rfid.services.ingest.validation import InvalidRead, parse_read

GOOD = {
    "tid": "E28011702000A7F312345678",
    "epc": "3034F4A2B1C80D0000000001",
    "reader_id": "READER-EXIT",
    "antenna_id": 3,
    "rssi": -52,
    "read_at": "2026-09-03T14:22:31.482000+00:00",
}


def test_a_well_formed_read_becomes_a_row():
    read, warnings = parse_read(GOOD)
    assert warnings == []
    assert read.tid == GOOD["tid"]
    assert read.antenna_id == 3
    assert read.rssi == -52
    assert read.read_at.tzinfo is not None
    # Column order of the INSERT.
    assert read.as_row()[:5] == (GOOD["tid"], GOOD["epc"], "READER-EXIT", 3, -52)


@pytest.mark.parametrize("field", ["tid", "reader_id", "antenna_id", "read_at"])
def test_a_missing_required_field_is_refused(field):
    payload = {k: v for k, v in GOOD.items() if k != field}
    with pytest.raises(InvalidRead, match="missing required field"):
        parse_read(payload)


def test_epc_and_rssi_are_optional():
    """SPEC.md section 3 makes both nullable; a reader may not report RSSI."""
    payload = GOOD | {"epc": None, "rssi": None}
    read, _ = parse_read(payload)
    assert read.epc is None
    assert read.rssi is None


def test_blank_and_whitespace_ids_are_refused():
    with pytest.raises(InvalidRead):
        parse_read(GOOD | {"tid": "   "})


def test_ids_are_stripped_so_stray_spaces_do_not_make_a_new_tag():
    read, _ = parse_read(GOOD | {"tid": "  A7F3  "})
    assert read.tid == "A7F3"


def test_values_too_big_for_the_column_are_refused():
    """antenna_id and rssi are SMALLINT; these cannot physically be stored."""
    with pytest.raises(InvalidRead, match="outside the range"):
        parse_read(GOOD | {"antenna_id": 40000})
    with pytest.raises(InvalidRead, match="outside the range"):
        parse_read(GOOD | {"rssi": -40000})


def test_a_boolean_is_not_a_number():
    """True is an int in Python. It is not an antenna."""
    with pytest.raises(InvalidRead, match="whole number"):
        parse_read(GOOD | {"antenna_id": True})


def test_a_non_numeric_antenna_is_refused():
    with pytest.raises(InvalidRead, match="whole number"):
        parse_read(GOOD | {"antenna_id": "three"})


def test_an_unparseable_timestamp_is_refused():
    with pytest.raises(InvalidRead, match="ISO 8601"):
        parse_read(GOOD | {"read_at": "last tuesday"})


def test_a_timestamp_without_a_timezone_is_kept_and_flagged():
    """Assumed UTC, with a warning. Keeping a questionable time beats
    losing the read, because reads_raw can be replayed."""
    read, warnings = parse_read(GOOD | {"read_at": "2026-09-03T14:22:31"})
    assert read.read_at.tzinfo == timezone.utc
    assert len(warnings) == 1
    assert "no timezone" in warnings[0]


def test_unknown_extra_fields_are_ignored():
    """A firmware update that adds a field must not stop the line."""
    read, warnings = parse_read(GOOD | {"temperature_c": 21, "phase": 145})
    assert read.tid == GOOD["tid"]
    assert warnings == []


def test_a_payload_that_is_not_an_object_is_refused():
    with pytest.raises(InvalidRead, match="JSON object"):
        parse_read([GOOD])
