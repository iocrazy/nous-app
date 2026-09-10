import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def test_empty_views_start_with_no_wakeups():
    assert rp.empty_views()["view"]["wakeups"] == []
    assert "schedule_set" in rp.registered_types()


def test_two_wakeups_accumulate_in_order():
    views = rp.apply(
        rp.empty_views(),
        "schedule_set",
        {"schedule_id": "s1", "fire_at": "2026-09-11T09:00:00+00:00", "note": "one"},
    )
    views = rp.apply(
        views,
        "schedule_set",
        {"schedule_id": "s2", "fire_at": "2026-09-12T09:00:00+00:00", "note": "two"},
    )
    assert views["view"]["wakeups"] == [
        {
            "schedule_id": "s1",
            "fire_at": "2026-09-11T09:00:00+00:00",
            "note": "one",
        },
        {
            "schedule_id": "s2",
            "fire_at": "2026-09-12T09:00:00+00:00",
            "note": "two",
        },
    ]


def test_a_payload_without_a_fire_time_leaves_the_views_untouched():
    """``apply`` hands back the SAME object when a fold has nothing to say —
    that identity is what lets the writer skip a mirror write."""
    views = rp.empty_views()
    assert rp.apply(views, "schedule_set", {"schedule_id": "s1"}) is views
    assert rp.apply(views, "schedule_set", {"fire_at": "2026-09-11T09:00:00Z"}) is views
