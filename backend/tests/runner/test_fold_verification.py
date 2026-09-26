import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def test_verification_event_folds_into_view():
    views = rp.apply(
        rp.empty_views(),
        "verification",
        {"verdict": "fail", "attempt": 1, "reason": "r", "retry": True},
    )
    assert views["view"]["verification"] == {
        "verdict": "fail",
        "attempt": 1,
        "reason": "r",
        "retry": True,
    }


def test_empty_is_none_and_bad_verdict_ignored():
    assert rp.empty_views()["view"]["verification"] is None
    assert (
        rp.apply(rp.empty_views(), "verification", {"verdict": "maybe"})["view"][
            "verification"
        ]
        is None
    )
    assert "verification" in rp.registered_types()
