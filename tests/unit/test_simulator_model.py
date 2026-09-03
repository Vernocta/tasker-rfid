"""Checks that the simulator produces reads shaped like a real reader's.

These are the claims the rest of the project rests on: if the simulator is
not realistic, every test written against it is testing a fiction.
"""

import random
from datetime import datetime, timezone

from tasker_rfid.services.simulator.model import (
    PEAK_RSSI_DBM,
    SENSITIVITY_DBM,
    Tag,
    attempt_hz_per_tag,
    merge,
    pass_reads,
    read_probability,
    stray_reads,
)

START = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
ANTENNAS = [3, 4]


def a_tag(tid="E28011702000AAAABBBBCCCC", attenuation_db=0.0):
    return Tag(tid=tid, epc="3034" + tid[-20:], attenuation_db=attenuation_db)


def test_one_pass_produces_dozens_to_hundreds_of_reads():
    """SPEC.md section 5.1: a real pass is not one message per tag."""
    for seed in range(20):
        reads = pass_reads(a_tag(), "READER-EXIT", ANTENNAS, START, random.Random(seed))
        assert 50 <= len(reads) <= 400, f"seed {seed} gave {len(reads)} reads"


def test_reads_span_the_whole_pass_and_stay_in_order():
    reads = pass_reads(a_tag(), "READER-EXIT", ANTENNAS, START, random.Random(1))
    span = (reads[-1].read_at - reads[0].read_at).total_seconds()
    assert 1.0 <= span <= 3.0
    assert reads == sorted(reads, key=lambda r: r.read_at)


def test_signal_rises_to_a_peak_then_falls():
    """RSSI must climb as the tag approaches and drop as it leaves."""
    reads = pass_reads(a_tag(), "READER-EXIT", ANTENNAS, START, random.Random(2))
    third = len(reads) // 3
    start_mean = sum(r.rssi for r in reads[:third]) / third
    middle_mean = sum(r.rssi for r in reads[third:-third]) / (len(reads) - 2 * third)
    end_mean = sum(r.rssi for r in reads[-third:]) / third

    assert middle_mean > start_mean + 5
    assert middle_mean > end_mean + 5


def test_both_antennas_of_the_portal_report():
    reads = pass_reads(a_tag(), "READER-EXIT", ANTENNAS, START, random.Random(3))
    assert {r.antenna_id for r in reads} == set(ANTENNAS)


def test_reverse_flips_which_antenna_peaks_first():
    """This is the only thing separating 'came in' from 'carried back out'."""

    def peak_time(reads, antenna_id):
        matching = [r for r in reads if r.antenna_id == antenna_id]
        strongest = max(matching, key=lambda r: r.rssi)
        return (strongest.read_at - START).total_seconds()

    rng_forward, rng_back = random.Random(7), random.Random(7)
    forward = pass_reads(a_tag(), "R", ANTENNAS, START, rng_forward)
    backward = pass_reads(a_tag(), "R", ANTENNAS, START, rng_back, reverse=True)

    assert peak_time(forward, 3) < peak_time(forward, 4)
    assert peak_time(backward, 4) < peak_time(backward, 3)


def test_a_shadowed_tag_reads_weaker_and_less_often():
    """Boxes buried in a pallet are why SHORT_PALLET exists."""
    clear = pass_reads(a_tag(), "R", ANTENNAS, START, random.Random(4))
    buried = pass_reads(
        a_tag(attenuation_db=12.0), "R", ANTENNAS, START, random.Random(4)
    )
    assert len(buried) < len(clear)
    assert max(r.rssi for r in buried) < max(r.rssi for r in clear)


def test_reader_splits_its_attention_across_a_crowded_field():
    """One tag alone gets read constantly; fifty tags each get a fraction."""
    assert attempt_hz_per_tag(1) > attempt_hz_per_tag(50)
    assert attempt_hz_per_tag(500) >= 8.0  # never starves completely


def test_a_pallet_of_boxes_stays_within_reader_throughput():
    hz = attempt_hz_per_tag(51)
    streams = [
        pass_reads(a_tag(f"TID{n:021d}"), "R", ANTENNAS, START, random.Random(n), attempt_hz=hz)
        for n in range(51)
    ]
    reads = merge(*streams)
    span = (reads[-1].read_at - reads[0].read_at).total_seconds()
    assert len(reads) / span < 1500, "more reads per second than a real reader manages"


def test_a_parked_box_reads_steadily_for_as_long_as_it_sits_there():
    """SPEC.md section 7, first row: stationary stock read continuously."""
    reads = stray_reads(a_tag(), "R", ANTENNAS, START, 120, random.Random(5))
    span = (reads[-1].read_at - reads[0].read_at).total_seconds()
    assert span > 110
    assert len(reads) > 100

    # No rise and fall — that is what makes it indistinguishable from a real
    # pass without the RSSI floor and the state machine.
    half = len(reads) // 2
    first_mean = sum(r.rssi for r in reads[:half]) / half
    second_mean = sum(r.rssi for r in reads[half:]) / (len(reads) - half)
    assert abs(first_mean - second_mean) < 8


def test_no_read_is_reported_below_the_tag_sensitivity_floor():
    assert read_probability(SENSITIVITY_DBM) == 0.0
    assert read_probability(SENSITIVITY_DBM - 10) == 0.0
    assert read_probability(PEAK_RSSI_DBM) > 0.9


def test_same_seed_gives_the_same_reads():
    """Tests downstream need to be able to repeat a run exactly."""
    first = pass_reads(a_tag(), "R", ANTENNAS, START, random.Random(99))
    second = pass_reads(a_tag(), "R", ANTENNAS, START, random.Random(99))
    assert first == second
