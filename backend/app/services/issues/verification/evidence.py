"""Evidence bundle — what the verifier is allowed to look at (spec §5.2).

Facts only: deliverable registrations, the script rows they point at, the
generated-media count, and the assistant text. No agent reasoning, no tool
trace, no FinishIssue reason (spec §9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from loguru import logger

FINAL_TEXT_MAX_CHARS = 12_000
PRIOR_TEXT_MAX_CHARS = 4_000
PRIOR_TEXTS_MAX = 3


@dataclass(frozen=True)
class Deliverable:
    kind: str
    ref_id: str
    version: int
    run_id: Optional[str]
    created_at: str


@dataclass(frozen=True)
class ShotFacts:
    id: int
    scene_id: int
    shot_number: Optional[int]
    shot_type: Optional[str]
    camera_angle: Optional[str]
    description: Optional[str]
    image_url: Optional[str]
    status: str

    @property
    def complete(self) -> bool:
        return bool(
            (self.description or "").strip()
            and (self.shot_type or "").strip()
            and (self.camera_angle or "").strip()
        )


@dataclass(frozen=True)
class SceneFacts:
    id: int
    scene_number: Optional[str]
    content_version: int
    omitted: bool
    shot_count: int


@dataclass(frozen=True)
class EvidenceBundle:
    issue_id: int
    run_id: Optional[str]
    deliverables: tuple[Deliverable, ...]
    shots: tuple[ShotFacts, ...]
    scene_scope: tuple[SceneFacts, ...]
    media_count: int
    final_text: str
    final_text_truncated: bool
    prior_texts: tuple[str, ...]
    errors: tuple[str, ...]

    def of_kind(self, kind: str) -> tuple[Deliverable, ...]:
        return tuple(d for d in self.deliverables if d.kind == kind)


async def _load_deliverables(issue_id: int) -> tuple[Deliverable, ...]:
    from app.repositories.run_deliverables_repository import (
        get_run_deliverables_repository,
    )

    rows = await get_run_deliverables_repository().list_for_issue(issue_id)
    return tuple(
        Deliverable(
            kind=str(r.get("kind")),
            ref_id=str(r.get("ref_id")),
            version=int(r.get("version") or 0),
            run_id=str(r["run_id"]) if r.get("run_id") is not None else None,
            created_at=str(r.get("created_at") or ""),
        )
        for r in rows
    )


async def _load_shots(shot_ids: list[int]) -> tuple[ShotFacts, ...]:
    if not shot_ids:
        return ()
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models.scripts import ScriptShots

    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(ScriptShots).where(ScriptShots.id.in_(shot_ids))
                )
            )
            .scalars()
            .all()
        )
    return tuple(
        ShotFacts(
            id=int(s.id),
            scene_id=int(s.scene_id),
            shot_number=s.shot_number,
            shot_type=s.shot_type,
            camera_angle=s.camera_angle,
            description=s.description,
            image_url=s.image_url,
            status=str(s.status),
        )
        for s in rows
    )


async def _load_scene_scope(scene_ids: list[int]) -> tuple[SceneFacts, ...]:
    """Every un-omitted scene of the scripts the issue touched, with shot counts.

    The shot count is bounded to those scenes — a GROUP BY over the whole
    ``script_shots`` table would grow with every user's scripts."""
    if not scene_ids:
        return ()
    from sqlalchemy import func, select

    from app.db.session import read_scope
    from app.models.scripts import ScriptScenes, ScriptShots

    async with read_scope() as session:
        script_ids = (
            (
                await session.execute(
                    select(ScriptScenes.script_id)
                    .where(ScriptScenes.id.in_(scene_ids))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if not script_ids:
            return ()
        # Columns only: the scene body (content / content_json) can be large
        # and the checks never read it.
        scenes = (
            await session.execute(
                select(
                    ScriptScenes.id,
                    ScriptScenes.script_id,
                    ScriptScenes.scene_number,
                    ScriptScenes.content_version,
                    ScriptScenes.omitted_at,
                ).where(ScriptScenes.script_id.in_(script_ids))
            )
        ).all()
        if not scenes:
            return ()
        counts = dict(
            (
                await session.execute(
                    select(ScriptShots.scene_id, func.count(ScriptShots.id))
                    .where(ScriptShots.scene_id.in_([int(s.id) for s in scenes]))
                    .group_by(ScriptShots.scene_id)
                )
            ).all()
        )
    return tuple(
        SceneFacts(
            id=int(s.id),
            scene_number=s.scene_number,
            content_version=int(s.content_version or 0),
            omitted=s.omitted_at is not None,
            shot_count=int(counts.get(s.id, 0)),
        )
        for s in scenes
    )


async def _load_prior_texts(
    session_id: Optional[str], user_id: Optional[str], final_text: str
) -> tuple[str, ...]:
    """Earlier assistant messages of the issue session — the last
    ``PRIOR_TEXTS_MAX`` before the final one, each capped, oldest first
    (``get_messages`` is chronological; the judge prints these before the
    worker's final text)."""
    if not session_id or not user_id:
        return ()
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    msgs = await AILibraryChatService().get_messages(
        session_id, user_id=UUID(str(user_id)), limit=12, newest=True
    )
    texts = [
        str(m.get("content") or "")
        for m in msgs
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    if texts and texts[-1] == final_text:
        texts = texts[:-1]
    return tuple(t[:PRIOR_TEXT_MAX_CHARS] for t in texts[-PRIOR_TEXTS_MAX:])


def _scene_id_of(
    shots: tuple[ShotFacts, ...], scene_deliverables: tuple[Deliverable, ...]
) -> list[int]:
    ids = {s.scene_id for s in shots}
    for d in scene_deliverables:
        try:
            ids.add(int(d.ref_id))
        except ValueError:
            continue
    return sorted(ids)


async def build_evidence_bundle(
    *,
    issue_id: int,
    run_id: Optional[str],
    final_text: str,
    session_id: Optional[str],
    user_id: Optional[str],
) -> EvidenceBundle:
    """Every source is independent and best-effort: a failed read lands in
    ``errors`` (the judge sees it as a fact) and never hides the others."""
    errors: list[str] = []
    deliverables: tuple[Deliverable, ...] = ()
    shots: tuple[ShotFacts, ...] = ()
    scope: tuple[SceneFacts, ...] = ()
    prior: tuple[str, ...] = ()
    try:
        deliverables = await _load_deliverables(issue_id)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"deliverables: {exc.__class__.__name__}")
        logger.warning(
            f"[verification] issue {issue_id}: deliverables read failed: {exc!r}"
        )
    shot_ids = [
        int(d.ref_id)
        for d in deliverables
        if d.kind == "script_shot" and d.ref_id.isdigit()
    ]
    try:
        shots = await _load_shots(sorted(set(shot_ids)))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"shots: {exc.__class__.__name__}")
        logger.warning(f"[verification] issue {issue_id}: shots read failed: {exc!r}")
    try:
        scope = await _load_scene_scope(
            _scene_id_of(
                shots, tuple(d for d in deliverables if d.kind == "script_scene")
            )
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"scenes: {exc.__class__.__name__}")
        logger.warning(f"[verification] issue {issue_id}: scenes read failed: {exc!r}")
    try:
        prior = await _load_prior_texts(session_id, user_id, final_text)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"prior_texts: {exc.__class__.__name__}")
        logger.warning(
            f"[verification] issue {issue_id}: prior texts read failed: {exc!r}"
        )
    truncated = len(final_text) > FINAL_TEXT_MAX_CHARS
    return EvidenceBundle(
        issue_id=int(issue_id),
        run_id=run_id,
        deliverables=deliverables,
        shots=shots,
        scene_scope=scope,
        media_count=len(
            {d.ref_id for d in deliverables if d.kind == "generated_media"}
        ),
        final_text=final_text[:FINAL_TEXT_MAX_CHARS],
        final_text_truncated=truncated,
        prior_texts=prior,
        errors=tuple(errors),
    )
