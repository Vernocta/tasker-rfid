"""Every failure mode in SPEC.md section 7, driven by the simulator.

Nothing here is stubbed. Each test publishes real MQTT messages with the
simulator and waits for ingest, the debouncer and the state engine to do
their work, then asserts on what ended up in the database.

Run them with the stack up:

    docker compose up -d
    uv run pytest tests/integration -v
"""

from conftest import (
    a_tid,
    anomalies_for,
    movements_for,
    observation_count,
    open_session,
    raw_read_count,
    register,
    sim,
    status_of,
    wait_for_settled,
    wait_until,
)

# ---------------------------------------------------------------------------
# SPEC.md section 7, row 1:
#   "Stationary stock read continuously"
# ---------------------------------------------------------------------------


def test_a_box_parked_by_an_antenna_for_ten_minutes_changes_state_once(db):
    """Thousands of reads. One state change. Then nothing, forever.

    This is the failure mode that sinks naive RFID portals: a box left
    within range gets read continuously and the stock count runs away.
    Here the reads collapse into one observation, and the second visit
    finds the container already IN_STOCK and does nothing at all.
    """
    tid = a_tid("STRAY")
    register(db, tid)

    # Ten minutes of parked box, compressed 120x so the test finishes.
    # The read timestamps still span the full ten minutes.
    sim("stray", "--tid", tid, "--duration", "600", "--speed", "120",
        "--portal", "ENTRANCE", "--seed", "1")
    wait_for_settled(db, tid, "the parked box to be processed")

    reads = raw_read_count(db, tid)
    assert reads > 1000, f"expected thousands of raw reads, got {reads}"
    assert observation_count(db, tid) == 1, "the reads should collapse to one event"

    assert status_of(db, tid) == "IN_STOCK"
    assert movements_for(db, tid) == [("REGISTERED", "IN_STOCK", "ENTRANCE", None)]

    # Still parked. Still being read. Still nothing happens.
    sim("stray", "--tid", tid, "--duration", "600", "--speed", "120",
        "--portal", "ENTRANCE", "--seed", "2")
    wait_until(
        lambda: observation_count(db, tid) == 2,
        "the second stay to be observed",
    )
    wait_for_settled(db, tid, "the second stay to be processed")

    assert raw_read_count(db, tid) > 2000
    assert len(movements_for(db, tid)) == 1, "a parked box must not move stock"
    assert status_of(db, tid) == "IN_STOCK"


# ---------------------------------------------------------------------------
# SPEC.md section 7, row 3:
#   "Container counted twice -> structurally impossible"
# ---------------------------------------------------------------------------


def test_the_same_container_cannot_be_dispatched_twice(db):
    """The second exit read is an anomaly, not a second dispatch."""
    tid = a_tid("DOUBLE")
    register(db, tid, status="IN_STOCK")
    open_session(db)

    sim("box", "--tid", tid, "--portal", "EXIT", "--seed", "10")
    wait_for_settled(db, tid, "the first dispatch")

    assert status_of(db, tid) == "DISPATCHED"
    assert len(movements_for(db, tid)) == 1

    # Out through the door a second time.
    sim("box", "--tid", tid, "--portal", "EXIT", "--seed", "11")
    wait_until(lambda: observation_count(db, tid) == 2, "the second exit read")
    wait_for_settled(db, tid, "the second exit read to be processed")

    assert len(movements_for(db, tid)) == 1, "stock was dispatched twice"
    assert status_of(db, tid) == "DISPATCHED"

    kinds = [kind for kind, _ in anomalies_for(db, tid)]
    assert "ILLEGAL_TRANSITION" in kinds


# ---------------------------------------------------------------------------
# SPEC.md section 7, row 5:
#   "Exit read, no session open -> NO_SESSION; blocks silent unattributed
#    dispatch"
# ---------------------------------------------------------------------------


def test_an_exit_read_with_no_open_session_does_not_dispatch(db):
    """SPEC.md 2.5: a dispatch without a destination is not a valid state."""
    tid = a_tid("NOSESSION")
    register(db, tid, status="IN_STOCK")
    # Deliberately no open_session() here.

    sim("box", "--tid", tid, "--portal", "EXIT", "--seed", "20")
    wait_for_settled(db, tid, "the unattributed exit read")

    assert status_of(db, tid) == "IN_STOCK", "stock left the building unattributed"
    assert movements_for(db, tid) == []

    kinds = [kind for kind, _ in anomalies_for(db, tid)]
    assert kinds == ["NO_SESSION"]


def test_the_same_read_dispatches_once_a_session_is_open(db):
    """The control for the test above: with a customer selected, it works."""
    tid = a_tid("WITHSESSION")
    register(db, tid, status="IN_STOCK")
    session_id = open_session(db, customer_id="CUST-0002")

    sim("box", "--tid", tid, "--portal", "EXIT", "--seed", "21")
    wait_for_settled(db, tid, "the attributed dispatch")

    assert status_of(db, tid) == "DISPATCHED"
    assert movements_for(db, tid) == [
        ("IN_STOCK", "DISPATCHED", "EXIT", session_id)
    ], "the dispatch must be attributed to the open session"
    assert anomalies_for(db, tid) == []


# ---------------------------------------------------------------------------
# SPEC.md section 4:
#   "Moving a pallet moves all children in the same transaction"
# ---------------------------------------------------------------------------


def test_moving_a_pallet_moves_every_box_on_it(db):
    """The boxes move because the pallet moved, in one transaction."""
    pallet_tid = a_tid("PALLET")
    pallet_id = register(db, pallet_tid, kind="PALLET", status="IN_STOCK", reusable=True)
    box_tids = [a_tid(f"ONPALLET{n}") for n in range(5)]
    for box_tid in box_tids:
        register(db, box_tid, status="IN_STOCK", parent_id=pallet_id)

    session_id = open_session(db)

    args = ["pallet", "--tid", pallet_tid, "--portal", "EXIT", "--seed", "30"]
    for box_tid in box_tids:
        args += ["--box-tid", box_tid]
    sim(*args)
    wait_for_settled(db, pallet_tid, "the pallet to be dispatched")
    for box_tid in box_tids:
        wait_for_settled(db, box_tid, f"box {box_tid} to be processed")

    assert status_of(db, pallet_tid) == "DISPATCHED"
    for box_tid in box_tids:
        assert status_of(db, box_tid) == "DISPATCHED", f"{box_tid} was left behind"
        moves = movements_for(db, box_tid)
        assert len(moves) == 1, f"{box_tid} moved {len(moves)} times"
        assert moves[0] == ("IN_STOCK", "DISPATCHED", "EXIT", session_id)

    # Every box on the load is attributed to the same customer.
    with db.cursor() as cur:
        cur.execute(
            """SELECT count(DISTINCT m.session_id)
               FROM movements m JOIN containers c USING (container_id)
               WHERE c.tid = ANY(%s) OR c.tid = %s""",
            (box_tids, pallet_tid),
        )
        assert cur.fetchone()[0] == 1


def test_a_pallet_moves_its_boxes_even_when_their_tags_are_never_read(db):
    """A box whose tag fails still leaves on the pallet it is sitting on."""
    pallet_tid = a_tid("SILENTPALLET")
    pallet_id = register(db, pallet_tid, kind="PALLET", status="IN_STOCK", reusable=True)
    unread_tid = a_tid("DEADTAG")
    register(db, unread_tid, status="IN_STOCK", parent_id=pallet_id)

    open_session(db)
    sim("box", "--tid", pallet_tid, "--portal", "EXIT", "--seed", "31")
    wait_for_settled(db, pallet_tid, "the pallet to be dispatched")

    assert status_of(db, pallet_tid) == "DISPATCHED"
    assert status_of(db, unread_tid) == "DISPATCHED", "the box was left in stock"
    assert raw_read_count(db, unread_tid) == 0, "this tag was never read at all"


# ---------------------------------------------------------------------------
# SPEC.md section 7, rows 2 and 9:
#   "Pallet read but boxes missed -> SHORT_PALLET"
# ---------------------------------------------------------------------------


def test_a_pallet_with_fewer_boxes_than_declared_raises_short_pallet(db):
    """Five boxes attached, three read. The shortfall is recorded."""
    pallet_tid = a_tid("SHORTPALLET")
    pallet_id = register(db, pallet_tid, kind="PALLET", status="IN_STOCK", reusable=True)
    box_tids = [a_tid(f"SHORTBOX{n}") for n in range(5)]
    for box_tid in box_tids:
        register(db, box_tid, status="IN_STOCK", parent_id=pallet_id)

    open_session(db)

    # Only three of the five boxes answer at the portal.
    args = ["pallet", "--tid", pallet_tid, "--portal", "EXIT", "--seed", "40"]
    for box_tid in box_tids[:3]:
        args += ["--box-tid", box_tid]
    sim(*args)
    wait_for_settled(db, pallet_tid, "the short pallet to be processed")

    anomalies = [a for a in anomalies_for(db, pallet_tid) if a[0] == "SHORT_PALLET"]
    assert anomalies, "a short pallet went out without being flagged"

    detail = anomalies[0][1]
    assert detail["declared_children"] == 5
    assert detail["children_read"] == 3
    assert detail["missing"] == 2

    # The load still left, and every box on it went with it.
    assert status_of(db, pallet_tid) == "DISPATCHED"
    for box_tid in box_tids:
        assert status_of(db, box_tid) == "DISPATCHED"


def test_a_complete_pallet_raises_nothing(db):
    """The control: all boxes read, no anomaly."""
    pallet_tid = a_tid("FULLPALLET")
    pallet_id = register(db, pallet_tid, kind="PALLET", status="IN_STOCK", reusable=True)
    box_tids = [a_tid(f"FULLBOX{n}") for n in range(3)]
    for box_tid in box_tids:
        register(db, box_tid, status="IN_STOCK", parent_id=pallet_id)

    open_session(db)
    args = ["pallet", "--tid", pallet_tid, "--portal", "EXIT", "--seed", "41"]
    for box_tid in box_tids:
        args += ["--box-tid", box_tid]
    sim(*args)
    wait_for_settled(db, pallet_tid, "the full pallet to be processed")

    assert anomalies_for(db, pallet_tid) == []


# ---------------------------------------------------------------------------
# SPEC.md section 4 layer 3 / section 3 anomaly kinds:
#   "NO_DIRECTION: portal could not resolve direction"
# ---------------------------------------------------------------------------


def test_an_exit_read_the_gate_cannot_explain_does_not_dispatch(db):
    """Read at the exit, but nothing crossed the beams. Nothing moves.

    Guessing here would dispatch stock that is still on the floor.
    """
    tid = a_tid("NODIRECTION")
    register(db, tid, status="IN_STOCK")
    open_session(db)

    # A tag sitting inside the exit antenna's range that never goes through
    # the doorway, so there is no beam crossing to match it to.
    sim("stray", "--tid", tid, "--duration", "30", "--speed", "15",
        "--portal", "EXIT", "--seed", "50")
    wait_for_settled(db, tid, "the directionless exit read")

    with db.cursor() as cur:
        cur.execute(
            "SELECT direction FROM observations WHERE tid = %s ORDER BY id", (tid,)
        )
        assert cur.fetchone()[0] == "UNKNOWN"

    assert status_of(db, tid) == "IN_STOCK", "stock was dispatched on a guess"
    assert movements_for(db, tid) == []
    assert [kind for kind, _ in anomalies_for(db, tid)] == ["NO_DIRECTION"]


# ---------------------------------------------------------------------------
# SPEC.md section 7, row 4:
#   "Carried back out the entrance -> ILLEGAL_TRANSITION + manual correction"
# ---------------------------------------------------------------------------


def test_a_dispatched_box_reappearing_is_flagged_not_absorbed(db):
    tid = a_tid("CAMEBACK")
    register(db, tid, status="DISPATCHED")

    sim("reverse", "--tid", tid, "--portal", "ENTRANCE", "--seed", "60")
    wait_for_settled(db, tid, "the returning box")

    assert status_of(db, tid) == "DISPATCHED", "stock changed without a person deciding"
    assert movements_for(db, tid) == []
    assert [kind for kind, _ in anomalies_for(db, tid)] == ["ILLEGAL_TRANSITION"]


def test_an_empty_pallet_coming_back_is_ready_to_use_again(db):
    """SPEC.md section 4: reusable containers return to REGISTERED."""
    tid = a_tid("EMPTYPALLET")
    register(db, tid, kind="PALLET", status="DISPATCHED", reusable=True)

    sim("box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "61")
    wait_for_settled(db, tid, "the pallet coming back")

    assert status_of(db, tid) == "REGISTERED"
    assert movements_for(db, tid) == [("DISPATCHED", "REGISTERED", "ENTRANCE", None)]
    assert anomalies_for(db, tid) == []


# ---------------------------------------------------------------------------
# SPEC.md section 4:
#   "Unknown TID -> UNKNOWN_TID anomaly, not counted"
# ---------------------------------------------------------------------------


def test_a_tag_with_no_container_record_is_flagged_and_not_counted(db):
    tid = a_tid("GHOST")
    # Deliberately never registered.

    sim("box", "--tid", tid, "--portal", "ENTRANCE", "--seed", "70")
    wait_for_settled(db, tid, "the unknown tag")

    assert status_of(db, tid) is None
    assert [kind for kind, _ in anomalies_for(db, tid)] == ["UNKNOWN_TID"]

    with db.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM movements m JOIN containers c USING (container_id)
               WHERE c.tid = %s""",
            (tid,),
        )
        assert cur.fetchone()[0] == 0
