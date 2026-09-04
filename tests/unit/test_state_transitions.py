"""The transition table. SPEC.md section 4, layer 4.

    REGISTERED -> IN_STOCK -> DISPATCHED
"""

import pytest

from tasker_rfid.services.state_engine.transitions import (
    DISPATCHED,
    ILLEGAL_TRANSITION,
    IN_STOCK,
    NO_DIRECTION,
    REGISTERED,
    decide,
)


def entrance(status, reusable=False):
    return decide(portal="ENTRANCE", direction=None, status=status, reusable=reusable)


def leaving(status, reusable=False):
    return decide(portal="EXIT", direction="OUT", status=status, reusable=reusable)


def returning(status, reusable=False):
    return decide(portal="EXIT", direction="IN", status=status, reusable=reusable)


# -- The ordinary path ------------------------------------------------------


def test_a_new_container_read_at_the_entrance_goes_into_stock():
    assert entrance(REGISTERED).to_status == IN_STOCK


def test_stock_leaving_by_the_exit_is_dispatched():
    assert leaving(IN_STOCK).to_status == DISPATCHED


# -- Idempotency: the reason double-counting is impossible -------------------


def test_reading_stock_again_at_the_entrance_does_nothing():
    """SPEC.md 2.3. A tag parked near an antenna changes nothing, forever."""
    decision = entrance(IN_STOCK)
    assert decision.is_no_op
    assert decision.to_status is None
    assert decision.anomaly is None


def test_the_same_container_cannot_be_dispatched_twice():
    """SPEC.md section 3 gives DISPATCHED -> DISPATCHED as the example."""
    decision = leaving(DISPATCHED)
    assert decision.to_status is None
    assert decision.anomaly == ILLEGAL_TRANSITION


# -- Things that should never happen quietly --------------------------------


def test_stock_that_never_entered_cannot_simply_leave():
    decision = leaving(REGISTERED)
    assert decision.to_status is None
    assert decision.anomaly == ILLEGAL_TRANSITION


def test_a_dispatched_box_coming_back_is_flagged_not_absorbed():
    """Not modelled as a return. A person decides what happened."""
    decision = entrance(DISPATCHED, reusable=False)
    assert decision.to_status is None
    assert decision.anomaly == ILLEGAL_TRANSITION


def test_a_dispatched_pallet_coming_back_is_ready_to_use_again():
    """SPEC.md section 4: reusable containers return to REGISTERED."""
    assert entrance(DISPATCHED, reusable=True).to_status == REGISTERED
    assert returning(DISPATCHED, reusable=True).to_status == REGISTERED


def test_a_returning_pallet_comes_back_empty():
    """Its boxes are at the customer. They must not follow it home.

    The only case where a container moves and its children do not.
    """
    for decision in (
        entrance(DISPATCHED, reusable=True),
        returning(DISPATCHED, reusable=True),
    ):
        assert decision.to_status == REGISTERED
        assert decision.detach_children is True


def test_nothing_else_detaches_children():
    """A loaded pallet going out takes its boxes with it, as always."""
    assert leaving(IN_STOCK).detach_children is False
    assert entrance(REGISTERED).detach_children is False
    assert entrance(IN_STOCK).detach_children is False
    assert entrance(DISPATCHED, reusable=False).detach_children is False
    assert leaving(DISPATCHED).detach_children is False


# -- Direction at the exit --------------------------------------------------


@pytest.mark.parametrize("direction", ["UNKNOWN", None])
def test_an_exit_read_without_a_direction_moves_nothing(direction):
    """Guessing would dispatch stock still on the floor, or the reverse."""
    decision = decide(
        portal="EXIT", direction=direction, status=IN_STOCK, reusable=False
    )
    assert decision.to_status is None
    assert decision.anomaly == NO_DIRECTION


def test_something_carried_back_in_through_the_exit_is_treated_as_arriving():
    assert returning(REGISTERED).to_status == IN_STOCK
    assert returning(IN_STOCK).is_no_op


def test_the_entrance_needs_no_direction_at_all():
    """SPEC.md section 4: a container entering storage is unambiguous."""
    assert entrance(REGISTERED).to_status == IN_STOCK
    assert decide(
        portal="ENTRANCE", direction="UNKNOWN", status=REGISTERED, reusable=False
    ).to_status == IN_STOCK


def test_an_unrecognised_status_is_never_silently_accepted():
    assert entrance("SOMETHING_ELSE").anomaly == ILLEGAL_TRANSITION
    assert leaving("SOMETHING_ELSE").anomaly == ILLEGAL_TRANSITION


# ---------------------------------------------------------------------------
# Manual correction. SPEC.md section 6: the one status change that does not
# come from a portal read.
# ---------------------------------------------------------------------------


def test_a_correction_can_reach_any_of_the_three_states():
    """The point of a correction is to express what the machine could not."""
    from tasker_rfid.services.state_engine.corrections import check_target_status

    check_target_status(IN_STOCK, DISPATCHED)
    check_target_status(DISPATCHED, IN_STOCK)
    check_target_status(IN_STOCK, REGISTERED)
    check_target_status(REGISTERED, DISPATCHED)


def test_a_correction_to_a_status_that_does_not_exist_is_refused():
    from tasker_rfid.services.state_engine.corrections import (
        CorrectionRefused,
        check_target_status,
    )

    with pytest.raises(CorrectionRefused, match="is not a status"):
        check_target_status(IN_STOCK, "SOLD")


def test_a_correction_that_changes_nothing_is_refused():
    """It would record a movement where nothing moved."""
    from tasker_rfid.services.state_engine.corrections import (
        CorrectionRefused,
        check_target_status,
    )

    with pytest.raises(CorrectionRefused, match="already IN_STOCK"):
        check_target_status(IN_STOCK, IN_STOCK)
