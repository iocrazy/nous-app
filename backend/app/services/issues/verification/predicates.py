"""Deterministic completion checks (spec §5.2). Pure functions of
``(trigger_text, bundle)``; keyword tables, no NLP. Any predicate that
raises is reported as ``not_applicable`` with ``facts.error`` — never a
verdict on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from app.services.issues.verification.evidence import EvidenceBundle

Status = Literal["satisfied", "violated", "not_applicable"]

_SHOT_WORDS = ("shot", "shots", "分镜", "镜头", "storyboard")
_EACH_SCENE_WORDS = (
    "each scene",
    "every scene",
    "per scene",
    "all scenes",
    "每个场景",
    "每场",
    "每一场",
    "各场景",
    "所有场景",
)
_REWRITE_WORDS = (
    "rewrite",
    "rewritten",
    "expand",
    "expanded",
    "revise",
    "revised",
    "扩写",
    "改写",
    "重写",
    "润色",
)
_IMAGE_WORDS = (
    "image",
    "images",
    "picture",
    "render",
    "renders",
    "出图",
    "生成图",
    "配图",
    "图片",
)


@dataclass(frozen=True)
class PredicateResult:
    name: str
    status: Status
    facts: dict[str, Any] = field(default_factory=dict)


def mentions(text: str, words: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(w in low for w in words)


def shots_exist(trigger: str, bundle: EvidenceBundle) -> PredicateResult:
    if not mentions(trigger, _SHOT_WORDS):
        return PredicateResult("shots_exist", "not_applicable")
    n = len({d.ref_id for d in bundle.of_kind("script_shot")})
    incomplete = sorted(s.id for s in bundle.shots if not s.complete)
    facts = {"shot_deliverables": n, "incomplete_shots": incomplete}
    if n == 0 or incomplete:
        return PredicateResult("shots_exist", "violated", facts)
    return PredicateResult("shots_exist", "satisfied", facts)


def scenes_covered(trigger: str, bundle: EvidenceBundle) -> PredicateResult:
    if not mentions(trigger, _EACH_SCENE_WORDS):
        return PredicateResult("scenes_covered", "not_applicable")
    live = [s for s in bundle.scene_scope if not s.omitted]
    without = [str(s.scene_number or s.id) for s in live if s.shot_count == 0]
    facts = {"scenes": len(live), "scenes_without_shots": without}
    if not live or without:
        return PredicateResult("scenes_covered", "violated", facts)
    return PredicateResult("scenes_covered", "satisfied", facts)


def scene_rewritten(trigger: str, bundle: EvidenceBundle) -> PredicateResult:
    if not mentions(trigger, _REWRITE_WORDS):
        return PredicateResult("scene_rewritten", "not_applicable")
    n = len({d.ref_id for d in bundle.of_kind("script_scene")})
    return PredicateResult(
        "scene_rewritten", "satisfied" if n else "violated", {"scene_deliverables": n}
    )


def image_dispatched(trigger: str, bundle: EvidenceBundle) -> PredicateResult:
    if not mentions(trigger, _IMAGE_WORDS):
        return PredicateResult("image_dispatched", "not_applicable")
    return PredicateResult(
        "image_dispatched",
        "satisfied" if bundle.media_count else "violated",
        {"generated_media": bundle.media_count},
    )


PREDICATES: tuple[Callable[[str, EvidenceBundle], PredicateResult], ...] = (
    shots_exist,
    scenes_covered,
    scene_rewritten,
    image_dispatched,
)


def run_predicates(
    trigger_text: str, bundle: EvidenceBundle
) -> tuple[PredicateResult, ...]:
    out: list[PredicateResult] = []
    for fn in PREDICATES:
        try:
            out.append(fn(trigger_text, bundle))
        except (
            Exception
        ) as exc:  # noqa: BLE001 — one bad check must not decide the verdict
            out.append(
                PredicateResult(
                    fn.__name__, "not_applicable", {"error": exc.__class__.__name__}
                )
            )
    return tuple(out)


__all__ = ["PREDICATES", "PredicateResult", "mentions", "run_predicates"]
