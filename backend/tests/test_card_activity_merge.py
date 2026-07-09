from app.services.library.projects_service import (
    STAGE_STALL_THRESHOLDS,
    _merge_activity,
)


def test_file_newer_than_stage_wins():
    stage = {
        "stage_name": "Storyboarding",
        "actor": "lm",
        "entered_at": "2026-07-01T00:00:00",
    }
    file = {"kind": "file", "actor": "hg", "created_at": "2026-07-08T00:00:00"}
    out = _merge_activity(
        stage, file, stage_slug="storyboard", now="2026-07-08T12:00:00"
    )
    assert out["kind"] == "file" and out["actor"] == "hg"


def test_stage_newer_than_file_wins():
    stage = {"stage_name": "Review", "actor": "lm", "entered_at": "2026-07-08T09:00:00"}
    file = {"kind": "file", "actor": "hg", "created_at": "2026-07-02T00:00:00"}
    out = _merge_activity(stage, file, stage_slug="review", now="2026-07-08T10:00:00")
    assert out["kind"] == "stage"


def test_review_stalls_after_3_days():
    stage = {"stage_name": "Review", "actor": "lm", "entered_at": "2026-07-01T00:00:00"}
    out = _merge_activity(stage, None, stage_slug="review", now="2026-07-08T00:00:00")
    assert out["stalled"] is True


def test_other_stage_not_stalled_before_7_days():
    stage = {
        "stage_name": "Planning",
        "actor": "lm",
        "entered_at": "2026-07-05T00:00:00",
    }
    out = _merge_activity(stage, None, stage_slug="planning", now="2026-07-08T00:00:00")
    assert out["stalled"] is False
