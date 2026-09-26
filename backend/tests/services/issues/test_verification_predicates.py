"""Four deterministic checks; each has a satisfied / violated / not_applicable case."""

import pytest

from app.services.issues.verification.evidence import (
    Deliverable,
    EvidenceBundle,
    SceneFacts,
    ShotFacts,
)
from app.services.issues.verification.predicates import run_predicates

pytestmark = pytest.mark.unit


def _bundle(**over) -> EvidenceBundle:
    base = dict(
        issue_id=1,
        run_id="r1",
        deliverables=(),
        shots=(),
        scene_scope=(),
        media_count=0,
        final_text="",
        final_text_truncated=False,
        prior_texts=(),
        errors=(),
    )
    return EvidenceBundle(**{**base, **over})


def _shot(i, scene=1, **over):
    base = dict(
        id=i,
        scene_id=scene,
        shot_number=i,
        shot_type="wide",
        camera_angle="eye",
        description="d",
        image_url=None,
        status="draft",
    )
    return ShotFacts(**{**base, **over})


def _by_name(results):
    return {r.name: r for r in results}


def test_no_trigger_words_means_everything_not_applicable():
    r = _by_name(run_predicates("Write a short poem", _bundle()))
    assert {x.status for x in r.values()} == {"not_applicable"}
    assert set(r) == {
        "shots_exist",
        "scenes_covered",
        "scene_rewritten",
        "image_dispatched",
    }


def test_shots_exist_violated_without_shot_deliverables():
    r = _by_name(run_predicates("Create 2 shots for scene 1", _bundle()))
    assert r["shots_exist"].status == "violated"
    assert r["shots_exist"].facts["shot_deliverables"] == 0


def test_shots_exist_violated_when_a_shot_lacks_fields():
    b = _bundle(
        deliverables=(
            Deliverable("script_shot", "10", 1, "r1", "2026-09-26T00:00:00Z"),
        ),
        shots=(_shot(10, description=""),),
    )
    r = _by_name(run_predicates("分镜两个", b))
    assert r["shots_exist"].status == "violated"
    assert r["shots_exist"].facts["incomplete_shots"] == [10]


def test_shots_exist_satisfied():
    b = _bundle(
        deliverables=(
            Deliverable("script_shot", "10", 1, "r1", "2026-09-26T00:00:00Z"),
        ),
        shots=(_shot(10),),
    )
    assert _by_name(run_predicates("two shots", b))["shots_exist"].status == "satisfied"


def test_scenes_covered_checks_every_unomitted_scene():
    scope = (
        SceneFacts(1, "1", 3, False, 2),
        SceneFacts(2, "2", 1, False, 0),
        SceneFacts(3, "3", 1, True, 0),
    )
    r = _by_name(run_predicates("each scene gets shots", _bundle(scene_scope=scope)))
    assert r["scenes_covered"].status == "violated"
    assert r["scenes_covered"].facts["scenes_without_shots"] == ["2"]
    scope_ok = (SceneFacts(1, "1", 3, False, 2), SceneFacts(2, "2", 1, False, 1))
    assert (
        _by_name(run_predicates("每个场景", _bundle(scene_scope=scope_ok)))[
            "scenes_covered"
        ].status
        == "satisfied"
    )


def test_scene_rewritten_needs_a_scene_deliverable():
    assert (
        _by_name(run_predicates("扩写场景 3", _bundle()))["scene_rewritten"].status
        == "violated"
    )
    b = _bundle(
        deliverables=(
            Deliverable("script_scene", "3", 2, "r1", "2026-09-26T00:00:00Z"),
        )
    )
    assert (
        _by_name(run_predicates("rewrite scene 3", b))["scene_rewritten"].status
        == "satisfied"
    )


def test_image_dispatched_counts_generated_media():
    assert (
        _by_name(run_predicates("generate an image", _bundle()))[
            "image_dispatched"
        ].status
        == "violated"
    )
    assert (
        _by_name(run_predicates("出图", _bundle(media_count=2)))[
            "image_dispatched"
        ].status
        == "satisfied"
    )


def test_a_predicate_exception_is_not_applicable_with_error_fact(monkeypatch):
    from app.services.issues.verification import predicates as p

    def boom(trigger, bundle):
        raise RuntimeError("bad")

    boom.__name__ = "shots_exist"
    monkeypatch.setattr(
        p,
        "PREDICATES",
        (boom,) + tuple(f for f in p.PREDICATES if f.__name__ != "shots_exist"),
    )
    r = _by_name(run_predicates("shots", _bundle()))
    assert r["shots_exist"].status == "not_applicable"
    assert r["shots_exist"].facts["error"] == "RuntimeError"


# ── review fix: ASCII trigger words match whole words only ──


@pytest.mark.parametrize(
    "text,name,fires",
    [
        ("attach a screenshot of the result", "shots_exist", False),
        ("imagine a better ending", "image_dispatched", False),
        ("surrender the scene", "image_dispatched", False),
        ("two shots per scene", "shots_exist", True),
        ("Shot 3 needs a close-up", "shots_exist", True),
        ("分镜两个", "shots_exist", True),
        ("每场一张配图", "image_dispatched", True),
    ],
)
def test_ascii_triggers_are_word_bounded_cjk_are_substrings(text, name, fires):
    status = _by_name(run_predicates(text, _bundle()))[name].status
    assert (status != "not_applicable") is fires
