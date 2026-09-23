"""scope_resolver — the SINGLE choke point for turning a model-supplied
screenwriting id into an authorized row (A2, screenwriting agent layer — see
docs/superpowers/plans/2026-08-04-episode-workflow-and-agent-layer.md and
docs/superpowers/specs/2026-08-04-screenwriting-agent-layer-design.md §3.2).

``script_scenes`` (and ``script_shots``, one hop further) carry NO tenant
column — authorization requires the join
``script_id -> script_projects -> project_id/team_id``. Every future
screenwriting tool (A4: ListScenes/ReadScene/CreateShot/UpdateShot/...) needs
this join; writing it once here — rather than once per tool — is the whole
point (spec: "那个 join 只写一遍、只测一遍"). ``test_scope_resolver_single_choke_point.py``
is the durable guard: it fails the build if any NEW file outside this module
touches ``ScriptScenes``/``ScriptShots``/``ScriptProjects``/``Episodes``
directly, or calls the scene/shot/episode repository getters directly,
forcing new tool code through ``resolve_scene`` / ``resolve_shot`` /
``resolve_episode`` instead of re-implementing (and likely forgetting) the
join.

Contract:
    - A model-supplied id is a SELECTOR within an already-authorized set,
      never an authorization input. These functions take the id and the
      run's own bound ``AgentRunScope`` (see ``agent_run_scope.py``) and
      return either the resolved row or ``Denied`` — never raise for an
      out-of-scope id (only for a truly unexpected DB failure, which the
      caller should treat as fail-closed too).
    - ``Denied.reason`` is DELIBERATELY generic (never distinguishes "does
      not exist" from "exists but not in this run's scope") — mirrors
      ``AgentRunsRepository.get_by_id``'s documented anti-enumeration
      discipline ("a stray id from another user reads as 404, not 403, to
      avoid leaking existence"). A tool that echoes ``Denied.reason`` back
      into the model's context must not leak which project a scene actually
      belongs to. The MORE detailed internal reason (``detail_code``) is
      audit-only — it lands in the denied audit's ``alert_history.message``,
      never in anything handed back to the model.
    - Every DENIED call is audited into ``alert_history`` (the admin Alerts
      page). Grants are not recorded: until mig 487 both went to
      ``agent_run_events``, which in seven weeks had 28 granted rows, 0
      denied and zero readers. See ``_audit`` below.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Union

from sqlalchemy import insert, select

from app.db.session import read_scope, write_scope
from app.models import (
    AlertHistory,
    Episodes,
    ScriptProjects,
    ScriptScenes,
    ScriptShots,
)
from app.services.alerting.anchor_rule import ensure_anchor_rule

from .agent_run_scope import AgentRunScope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Denied:
    """A resolution attempt that must not proceed. See module docstring for
    why ``reason`` is generic and ``detail_code`` is audit-only."""

    resource_type: str
    requested_id: str
    reason: str = "not found or not accessible in this run"
    detail_code: str = "unspecified"


@dataclass(frozen=True)
class ResolvedScene:
    id: int
    script_id: int
    project_id: int
    team_id: Optional[int]
    episode_id: Optional[int]
    heading_int_ext: Optional[str]
    location_text: Optional[str]
    time_of_day: Optional[str]
    content_json: Any
    content_version: int


@dataclass(frozen=True)
class ResolvedShot:
    id: int
    scene_id: int
    project_id: int
    team_id: Optional[int]
    episode_id: Optional[int]
    shot_number: Optional[int]
    shot_type: Optional[str]
    status: str


@dataclass(frozen=True)
class ResolvedEpisode:
    id: int
    project_id: int
    title: str
    sort_order: int


SceneResult = Union[ResolvedScene, Denied]
ShotResult = Union[ResolvedShot, Denied]
EpisodeResult = Union[ResolvedEpisode, Denied]


def _bigint(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _scope_check(
    *,
    scope: AgentRunScope,
    resource_type: str,
    requested_id: str,
    row_project_id: Optional[int],
    row_team_id: Optional[int] = None,
    row_episode_id: Optional[int] = None,
) -> Optional[Denied]:
    """Shared scope-comparison kernel — the actual join query differs per
    resource type, but the "is it in THIS run's scope" comparison is
    identical everywhere, so it lives in exactly one place too.

    Explicit decision (A2 review, Important — this was previously an
    unstated side effect of the comparison below, not a considered choice):
    when a run IS episode-scoped (``scope.episode_id is not None``) and the
    resource's own episode is unassigned (``row_episode_id is None`` — a
    legacy scene/shot whose ``script_projects.episode_id`` predates B1, or
    an episode-agnostic script), this DENIES. ``None != scope.episode_id``
    is true, so it falls out of the comparison naturally, but the reasoning
    is deliberate, not incidental: an episode-scoped run should not
    transparently see episode-unassigned data just because that data has no
    episode to conflict with — that would let episode-scoped tools quietly
    reach legacy content the dispatcher never intended to expose to this
    run. If a future need arises for an episode-scoped run to ALSO read
    unassigned legacy scenes, that must be a new, explicit scope flag (e.g.
    ``AgentRunScope.include_unassigned_episode``), not a loosening of this
    comparison — see ``test_scope_resolver.py``'s
    ``test_episode_scoped_run_denies_scene_with_unassigned_episode``."""
    if not scope.is_bound():
        return Denied(resource_type, requested_id, detail_code="scope_unbound")
    if row_project_id != scope.project_id:
        return Denied(resource_type, requested_id, detail_code="project_mismatch")
    if (
        scope.team_id is not None
        and row_team_id is not None
        and row_team_id != scope.team_id
    ):
        return Denied(resource_type, requested_id, detail_code="team_mismatch")
    if scope.episode_id is not None and row_episode_id != scope.episode_id:
        return Denied(resource_type, requested_id, detail_code="episode_mismatch")
    return None


async def resolve_scene(scene_id: Any, scope: AgentRunScope) -> SceneResult:
    """The ONLY place ``script_scenes -> script_projects`` is joined for
    authorization. See module docstring for the full contract."""
    result, detail_code = await _resolve_scene_inner(scene_id, scope)
    await _audit(scope, "scene", str(scene_id), result, detail_code)
    return result


async def _resolve_scene_inner(scene_id: Any, scope: AgentRunScope):
    rid = _bigint(scene_id)
    if rid is None:
        denied = Denied("scene", str(scene_id), detail_code="malformed_id")
        return denied, denied.detail_code

    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    ScriptScenes.id,
                    ScriptScenes.script_id,
                    ScriptScenes.heading_int_ext,
                    ScriptScenes.location_text,
                    ScriptScenes.time_of_day,
                    ScriptScenes.content_json,
                    ScriptScenes.content_version,
                    ScriptProjects.project_id,
                    ScriptProjects.team_id,
                    ScriptProjects.episode_id,
                )
                .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
                .where(ScriptScenes.id == rid)
                .limit(1)
            )
        ).first()

    if row is None:
        denied = Denied("scene", str(scene_id), detail_code="not_found")
        return denied, denied.detail_code

    denied = _scope_check(
        scope=scope,
        resource_type="scene",
        requested_id=str(scene_id),
        row_project_id=row.project_id,
        row_team_id=row.team_id,
        row_episode_id=row.episode_id,
    )
    if denied is not None:
        return denied, denied.detail_code

    return (
        ResolvedScene(
            id=row.id,
            script_id=row.script_id,
            project_id=row.project_id,
            team_id=row.team_id,
            episode_id=row.episode_id,
            heading_int_ext=row.heading_int_ext,
            location_text=row.location_text,
            time_of_day=row.time_of_day,
            content_json=row.content_json,
            content_version=row.content_version,
        ),
        "granted",
    )


async def resolve_shot(shot_id: Any, scope: AgentRunScope) -> ShotResult:
    """The ONLY place ``script_shots -> script_scenes -> script_projects``
    is joined for authorization."""
    result, detail_code = await _resolve_shot_inner(shot_id, scope)
    await _audit(scope, "shot", str(shot_id), result, detail_code)
    return result


async def _resolve_shot_inner(shot_id: Any, scope: AgentRunScope):
    rid = _bigint(shot_id)
    if rid is None:
        denied = Denied("shot", str(shot_id), detail_code="malformed_id")
        return denied, denied.detail_code

    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    ScriptShots.id,
                    ScriptShots.scene_id,
                    ScriptShots.shot_number,
                    ScriptShots.shot_type,
                    ScriptShots.status,
                    ScriptProjects.project_id,
                    ScriptProjects.team_id,
                    ScriptProjects.episode_id,
                )
                .join(ScriptScenes, ScriptScenes.id == ScriptShots.scene_id)
                .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
                .where(ScriptShots.id == rid)
                .limit(1)
            )
        ).first()

    if row is None:
        denied = Denied("shot", str(shot_id), detail_code="not_found")
        return denied, denied.detail_code

    denied = _scope_check(
        scope=scope,
        resource_type="shot",
        requested_id=str(shot_id),
        row_project_id=row.project_id,
        row_team_id=row.team_id,
        row_episode_id=row.episode_id,
    )
    if denied is not None:
        return denied, denied.detail_code

    return (
        ResolvedShot(
            id=row.id,
            scene_id=row.scene_id,
            project_id=row.project_id,
            team_id=row.team_id,
            episode_id=row.episode_id,
            shot_number=row.shot_number,
            shot_type=row.shot_type,
            status=row.status,
        ),
        "granted",
    )


async def resolve_episode(episode_id: Any, scope: AgentRunScope) -> EpisodeResult:
    """The ONLY place ``episodes`` is checked for authorization.

    ``episodes`` carries ``project_id`` directly (no join needed) — no
    ``team_id`` column exists on this table, so only the project-id check
    applies (the shared kernel's team check is a no-op here since
    ``row_team_id`` is never passed).

    A2 review fix (Important): the episode being resolved passes ITS OWN
    id as ``row_episode_id`` to the shared scope kernel — an episode-scoped
    run resolving its own episode must be granted. The bug this fixes:
    omitting it meant the kernel compared ``None != scope.episode_id``,
    which denies EVERY episode-scoped resolution unconditionally, including
    the one the run is legitimately scoped to. Only caught because a
    reviewer executed the B1-readiness path end to end — the shipped test
    only exercised ``resolve_scene``'s episode dimension, not
    ``resolve_episode``'s own."""
    result, detail_code = await _resolve_episode_inner(episode_id, scope)
    await _audit(scope, "episode", str(episode_id), result, detail_code)
    return result


async def _resolve_episode_inner(episode_id: Any, scope: AgentRunScope):
    rid = _bigint(episode_id)
    if rid is None:
        denied = Denied("episode", str(episode_id), detail_code="malformed_id")
        return denied, denied.detail_code

    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    Episodes.id,
                    Episodes.project_id,
                    Episodes.title,
                    Episodes.sort_order,
                )
                .where(Episodes.id == rid)
                .limit(1)
            )
        ).first()

    if row is None:
        denied = Denied("episode", str(episode_id), detail_code="not_found")
        return denied, denied.detail_code

    denied = _scope_check(
        scope=scope,
        resource_type="episode",
        requested_id=str(episode_id),
        row_project_id=row.project_id,
        row_episode_id=row.id,
    )
    if denied is not None:
        return denied, denied.detail_code

    return (
        ResolvedEpisode(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            sort_order=row.sort_order,
        ),
        "granted",
    )


# ---------------------------------------------------------------------- #
# Audit — denied resolution attempts land on the admin Alerts page.
# ---------------------------------------------------------------------- #


async def _audit(
    scope: AgentRunScope,
    resource_type: str,
    requested_id: str,
    result: Union[ResolvedScene, ResolvedShot, ResolvedEpisode, Denied],
    detail_code: str,
) -> None:
    """Record a resolution attempt — see ``audit_resolution`` for what is kept.

    History: until mig 487 every attempt, granted or denied, was one row in
    ``agent_run_events`` (mig 155, the retired CostAuditorHook's table). That
    table was dropped with zero readers; the denied half of the audit moved
    to ``alert_history`` because the admin Alerts page is the one audit
    surface anyone actually reads. ``agent_run_transcript_events`` was never
    an option: its UNIQUE(run_id, seq) is owned by ``RunRecorder``'s
    in-memory counter, and a second writer computing its own seq is the bug
    class mig 397 documents.
    """
    await audit_resolution(
        scope,
        resource_type,
        requested_id,
        granted=not isinstance(result, Denied),
        detail_code=detail_code,
    )


_DENIED_RULE_NAME = "Scope denied (system)"
_DENIED_METRIC = "scope_denied"


async def audit_resolution(
    scope: AgentRunScope,
    resource_type: str,
    requested_id: str,
    *,
    granted: bool,
    detail_code: str,
) -> None:
    """The audit itself, callable by resolution steps that are NOT one of
    the three id resolvers above.

    A5's ``script_selection.resolve_selection`` is the first such caller: a
    selection is resolved in two stages — the scene id (audited by
    ``resolve_scene``) and then the ELEMENT ids inside it, which no id
    resolver covers because they are not rows. A foreign or fabricated
    element id is exactly the kind of attempt the plan wants on the record,
    so it goes through here rather than a second, differently-shaped path.

    - ``granted=True`` writes nothing.
    - ``granted=False`` logs a WARNING and adds one ``alert_history`` row
      under the ``Scope denied (system)`` anchor rule, created on the first
      denial (not at startup). The message carries the detailed
      ``detail_code`` — audit-only, never handed back to the model.

    Best-effort like every other telemetry write in this codebase: a failed
    write is logged and swallowed, never raised — an audit outage must not
    break a resolution decision that has already been made.
    """
    try:
        rid = int(str(scope.run_id))
    except (TypeError, ValueError, AttributeError):
        return
    if rid == 0:
        # Sentinel run_id used in test paths without a real RunRecorder
        # (the convention came from CostAuditorHook, retired in 3c §3.2).
        return
    if granted:
        return

    message = (
        f"Scope denied: {resource_type} id={requested_id} "
        f"user={scope.user_id} run={rid} reason={detail_code} "
        f"(scope project={scope.project_id} team={scope.team_id} "
        f"episode={scope.episode_id})"
    )
    logger.warning("[scope_resolver] %s", message)
    try:
        rule_id = await ensure_anchor_rule(
            name=_DENIED_RULE_NAME,
            metric_type=_DENIED_METRIC,
            threshold=0.0,
            is_active=True,
        )
        async with write_scope() as session:
            await session.execute(
                insert(AlertHistory).values(
                    rule_id=rule_id,
                    rule_name=_DENIED_RULE_NAME,
                    metric_type=_DENIED_METRIC,
                    metric_value=1.0,
                    threshold=0.0,
                    condition="gte",
                    message=message,
                    notified=False,
                )
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "[scope_resolver] audit write failed (run=%s resource=%s id=%s)",
            scope.run_id,
            resource_type,
            requested_id,
        )


__all__ = [
    "Denied",
    "ResolvedScene",
    "ResolvedShot",
    "ResolvedEpisode",
    "audit_resolution",
    "resolve_scene",
    "resolve_shot",
    "resolve_episode",
]
