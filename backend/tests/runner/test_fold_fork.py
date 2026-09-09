import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def test_fork_event_folds_into_view_fork():
    views = rp.apply(
        rp.empty_views(), "fork", {"of_run_id": 42, "at_seq": 7, "steer": True}
    )
    assert views["view"]["fork"] == {"of_run_id": 42, "at_seq": 7}


def test_empty_views_has_fork_none_and_bad_payload_is_ignored():
    assert rp.empty_views()["view"]["fork"] is None
    views = rp.apply(rp.empty_views(), "fork", {"of_run_id": "x"})
    assert views["view"]["fork"] is None
    assert "fork" in rp.registered_types()


def test_every_registered_fold_type_is_in_the_orm_check_allowlist():
    """A fold for an event type the DB rejects is dead code: events.emit
    swallows the CHECK violation with a warning, so view.fork would stay
    None forever with no error surfacing. The ORM literal mirrors the
    latest migration (tests/models/test_transcript_event_types_phase2a)."""
    from app.services.ai.runner.run_projection import (
        LOCAL_FOLD_TYPES,
        registered_types,
    )
    from tests.models.test_transcript_event_types_phase2a import (
        _literals,
        _orm_check_sql,
    )

    # Local folds (fold_local, no event row) are the one legitimate exception.
    missing = set(registered_types()) - LOCAL_FOLD_TYPES - _literals(_orm_check_sql())
    assert not missing, f"fold registered for non-allowlisted types: {missing}"
