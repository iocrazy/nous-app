"""scoped_script_gateway — the scope-bound data operations the A4
screenwriting tools run on, once ``scope_resolver`` has already authorized
the ids involved.

WHY THIS FILE IS ALLOWED TO TOUCH script_scenes / script_shots /
script_projects / episodes (it carries the only non-resolver entry in
``tests/test_scope_resolver_single_choke_point.py``'s ``ORM_ALLOWED_PATHS``):

``scope_resolver`` answers "may this run touch id X?" for a SINGLE id. Tools
need two more things it deliberately does not do — enumerate what is in
scope in the first place (``ListScenes`` has no id to resolve; that is the
point of listing), and WRITE (``CreateShot`` / ``UpdateShot``). Both need
the same tables. Putting them in the resolver would turn a file whose whole
job is one authorization decision into a general data-access layer; putting
them in the tool files would re-create exactly the per-tool join the guard
exists to prevent.

So this module takes the third path, and pays for the allow-list entry with
a MECHANICAL invariant rather than a promise:

    Every public function here takes the run's ``AgentRunScope`` as its
    first parameter, and any ENTITY it operates on arrives as an
    already-resolved ``ResolvedScene`` / ``ResolvedShot`` / ``ResolvedEpisode``
    handle — never a raw, model-supplied id.

Those ``Resolved*`` dataclasses are frozen and are constructed in exactly one
place: inside ``scope_resolver``'s resolvers, AFTER the scope check passed.
There is therefore no way to reach a write here without having gone through
the choke point first — the type system carries the authorization proof.
``test_scoped_script_gateway_takes_only_resolved_handles`` enforces the
invariant by introspecting every public signature, so a future function that
takes a bare ``scene_id: int`` fails the build rather than quietly opening
the bypass the guard was written to close.

The one function that does NOT take a handle — ``list_scenes_in_scope`` —
takes no entity id at all: it derives everything from ``scope`` itself
(server-bound) plus an OPTIONAL already-resolved episode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import ScriptProjects, ScriptScenes, ScriptShots
from app.services.script.scene_numbering import (
    derive_shot_label,
    effective_scene_number,
)

from .agent_run_scope import AgentRunScope
from .scope_resolver import ResolvedEpisode, ResolvedScene, ResolvedShot

logger = logging.getLogger(__name__)

# Sparse-ordering step, mirroring script_shot_repository.STEP. Duplicated as a
# module constant rather than imported because importing the repository module
# for one integer would drag its ORM surface (and its unscoped getters) into
# the agent-tool import graph for no benefit. If the repository's ladder ever
# changes, both must move together — the two write shot rows into the same
# column.
_SORT_ORDER_STEP = 1000

# Columns CreateShot/UpdateShot may write. Deliberately the same whitelist as
# ``script_shot_repository._UPDATE_FIELDS`` MINUS ``shot_number`` (the tools
# never let a model choose a shot's number — see ``create_shot``) and minus
# every status / produced-media URL column: an agent writing a shot card must
# not be able to declare that card already rendered. Those flow through the
# generate workflow's own lane (``update_status``), which A6 wires.
_WRITABLE_SHOT_FIELDS = (
    "shot_type",
    "camera_angle",
    "camera_movement",
    "focal_length",
    "lighting",
    "description",
)

# Hard ceiling on one ListScenes response, independent of what the model asks
# for. An episode of television is tens of scenes; a whole project is
# hundreds. This bounds the tool result that gets pasted back into the
# model's context window, which is a cost and a context-budget concern rather
# than a security one (everything returned is already in scope).
MAX_LIST_SCENES = 200


@dataclass(frozen=True)
class SceneSummary:
    """One row of a ``ListScenes`` result — spec §5.1's "号/标题/内外景/地点/
    日夜/是否有内容"."""

    scene_id: int
    script_id: int
    episode_id: Optional[int]
    scene_no_in_episode: Optional[str]
    heading_int_ext: Optional[str]
    location_text: Optional[str]
    time_of_day: Optional[str]
    has_content: bool
    omitted: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": str(self.scene_id),
            "script_id": str(self.script_id),
            "episode_id": str(self.episode_id) if self.episode_id else None,
            "scene_no_in_episode": self.scene_no_in_episode,
            "heading_int_ext": self.heading_int_ext,
            "location_text": self.location_text,
            "time_of_day": self.time_of_day,
            "has_content": self.has_content,
            "omitted": self.omitted,
        }


def _elements(content_json: Any) -> list[dict[str, Any]]:
    """``script_scenes.content_json`` as a list of element dicts.

    The column is JSONB and nullable, and legacy rows can hold ``{}`` or a
    non-list. Anything that isn't a list of dicts reads as "no elements"
    rather than raising — a malformed row must degrade a tool result, not
    fail a whole turn."""
    if not isinstance(content_json, list):
        return []
    return [el for el in content_json if isinstance(el, dict)]


async def list_scenes_in_scope(
    scope: AgentRunScope,
    *,
    episode: Optional[ResolvedEpisode] = None,
    limit: int = MAX_LIST_SCENES,
) -> list[SceneSummary]:
    """Every scene the run may read, in canonical order, with A3's stable
    ``scene_no_in_episode`` on each.

    Takes NO model-supplied id: the project comes from ``scope`` (server-
    bound) and ``episode`` — when narrowing further — is a handle already
    returned by ``resolve_episode``. An unbound scope returns ``[]``, which
    is the same fail-closed answer the resolvers give.

    Canonical order is per SCRIPT (``chapter_id`` NULLS LAST, then
    ``sort_order``) — byte-identical to ``ScriptSceneRepository.list_by_script``,
    because ``scene_no_in_episode``'s writing-phase derivation is positional
    and MUST agree with what the editor shows. Scripts themselves are ordered
    by episode then id so a multi-script project reads deterministically.
    """
    if not scope.is_bound():
        return []

    episode_filter: Optional[int] = None
    if episode is not None:
        episode_filter = episode.id
    elif scope.episode_id is not None:
        # A run narrowed to an episode never lists outside it, even when the
        # caller passed no episode argument — mirrors _scope_check, which
        # would deny each of those scenes individually anyway.
        episode_filter = scope.episode_id

    async with read_scope() as session:
        script_q = select(
            ScriptProjects.id,
            ScriptProjects.episode_id,
            ScriptProjects.numbering_locked_at,
        ).where(
            ScriptProjects.project_id == scope.project_id,
            ScriptProjects.status != "deleted",
        )
        if scope.team_id is not None:
            script_q = script_q.where(
                (ScriptProjects.team_id == scope.team_id)
                | (ScriptProjects.team_id.is_(None))
            )
        if episode_filter is not None:
            script_q = script_q.where(ScriptProjects.episode_id == episode_filter)
        scripts = (await session.execute(script_q.order_by(ScriptProjects.id))).all()

        out: list[SceneSummary] = []
        for script in scripts:
            if len(out) >= limit:
                break
            rows = (
                await session.execute(
                    select(
                        ScriptScenes.id,
                        ScriptScenes.scene_number,
                        ScriptScenes.heading_int_ext,
                        ScriptScenes.location_text,
                        ScriptScenes.time_of_day,
                        ScriptScenes.content_json,
                        ScriptScenes.omitted_at,
                    )
                    .where(ScriptScenes.script_id == script.id)
                    .order_by(
                        ScriptScenes.chapter_id.asc().nulls_last(),
                        ScriptScenes.sort_order.asc(),
                    )
                )
            ).all()
            is_locked = script.numbering_locked_at is not None
            for index, row in enumerate(rows):
                if len(out) >= limit:
                    break
                out.append(
                    SceneSummary(
                        scene_id=row.id,
                        script_id=script.id,
                        episode_id=script.episode_id,
                        # Derived from the FULL ordered list's index, not the
                        # truncated output's — a `limit` must never change a
                        # scene's number.
                        scene_no_in_episode=effective_scene_number(
                            row.scene_number, index, is_locked
                        ),
                        heading_int_ext=row.heading_int_ext,
                        location_text=row.location_text,
                        time_of_day=row.time_of_day,
                        has_content=bool(_elements(row.content_json)),
                        omitted=row.omitted_at is not None,
                    )
                )
    return out


async def scene_no_for(scope: AgentRunScope, scene: ResolvedScene) -> Optional[str]:
    """``scene_no_in_episode`` for one already-resolved scene.

    Same rule as ``list_scenes_in_scope`` (both call
    ``effective_scene_number``); separate because ReadScene / CreateShot know
    exactly one scene and shouldn't pay for enumerating its siblings unless
    the number actually has to be derived — which it only does while the
    script is unlocked."""
    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    ScriptProjects.numbering_locked_at,
                ).where(ScriptProjects.id == scene.script_id)
            )
        ).first()
        is_locked = row is not None and row.numbering_locked_at is not None

        stored = (
            await session.execute(
                select(ScriptScenes.scene_number).where(ScriptScenes.id == scene.id)
            )
        ).scalar()
        if stored is not None:
            return stored
        if is_locked:
            return None
        ids = (
            (
                await session.execute(
                    select(ScriptScenes.id)
                    .where(ScriptScenes.script_id == scene.script_id)
                    .order_by(
                        ScriptScenes.chapter_id.asc().nulls_last(),
                        ScriptScenes.sort_order.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
    try:
        index = list(ids).index(scene.id)
    except ValueError:  # pragma: no cover — scene vanished between queries
        return None
    return effective_scene_number(None, index, is_locked)


async def read_scene_elements(
    scope: AgentRunScope, scene: ResolvedScene
) -> list[dict[str, Any]]:
    """The scene's elements as ``{element_id, type, text}`` triples.

    Reads straight off the ``ResolvedScene`` the resolver already fetched —
    no second query, and no chance of reading a DIFFERENT row than the one
    that was authorized. ``element_id`` is surfaced under that name (not the
    raw ``id`` key) because it is the anchor A5's edit contract and
    ``ProposeEdit`` both address elements by."""
    return [
        {
            "element_id": el.get("id"),
            "type": el.get("type"),
            "text": el.get("text"),
            "character_id": el.get("character_id"),
        }
        for el in _elements(scene.content_json)
        if el.get("id")
    ]


async def list_shots_for_scene(
    scope: AgentRunScope, scene: ResolvedScene
) -> list[dict[str, Any]]:
    """Existing shot cards for one already-resolved scene, in board order."""
    scene_no = await scene_no_for(scope, scene)
    async with read_scope() as session:
        rows = (
            await session.execute(
                select(
                    ScriptShots.id,
                    ScriptShots.shot_number,
                    ScriptShots.shot_type,
                    ScriptShots.camera_angle,
                    ScriptShots.camera_movement,
                    ScriptShots.focal_length,
                    ScriptShots.description,
                    ScriptShots.status,
                )
                .where(ScriptShots.scene_id == scene.id)
                .order_by(ScriptShots.sort_order.asc(), ScriptShots.id.asc())
            )
        ).all()
    return [_shot_dict(row, scene_no) for row in rows]


def _shot_dict(row: Any, scene_no: Optional[str]) -> dict[str, Any]:
    return {
        "shot_id": str(row.id),
        "shot_number": row.shot_number,
        "shot_label": derive_shot_label(scene_no, row.shot_number),
        "shot_type": row.shot_type,
        "camera_angle": row.camera_angle,
        "camera_movement": row.camera_movement,
        "focal_length": row.focal_length,
        "description": row.description,
        "status": row.status,
    }


def _writable(fields: dict[str, Any]) -> dict[str, Any]:
    """Whitelist + normalise the caller's field dict. Unknown keys are
    dropped silently (REST-parity with the repository's ``update``); ``None``
    values are dropped too, so a partial UpdateShot never blanks a column the
    model simply didn't mention."""
    out: dict[str, Any] = {}
    for key in _WRITABLE_SHOT_FIELDS:
        value = fields.get(key)
        if value is None:
            continue
        out[key] = str(value) if not isinstance(value, str) else value
    return out


async def create_shot(
    scope: AgentRunScope, scene: ResolvedScene, fields: dict[str, Any]
) -> dict[str, Any]:
    """Append one shot card to an already-resolved scene.

    ``shot_number`` is assigned server-side as ``MAX(shot_number)+1`` WITHIN
    THE SCENE and ``sort_order`` as ``MAX+STEP`` — byte-identical to
    ``ScriptShotRepository.create_many``'s rule, so agent-created and Auto-
    Storyboard-created cards share one ladder instead of colliding. The model
    never supplies either (see ``_WRITABLE_SHOT_FIELDS``): letting it choose
    a number is how duplicate shot ids happen.

    ``status`` is left at the column default (``'empty'``) — creation never
    declares a produced state.
    """
    values = _writable(fields)
    async with write_scope() as session:
        base_num = (
            await session.scalar(
                select(func.max(ScriptShots.shot_number)).where(
                    ScriptShots.scene_id == scene.id
                )
            )
        ) or 0
        base_sort = (
            await session.scalar(
                select(func.max(ScriptShots.sort_order)).where(
                    ScriptShots.scene_id == scene.id
                )
            )
        ) or 0
        values["scene_id"] = scene.id
        values["shot_number"] = base_num + 1
        values["sort_order"] = base_sort + _SORT_ORDER_STEP
        row = (
            await session.execute(
                insert(ScriptShots)
                .values(**values)
                .returning(
                    ScriptShots.id,
                    ScriptShots.shot_number,
                    ScriptShots.shot_type,
                    ScriptShots.camera_angle,
                    ScriptShots.camera_movement,
                    ScriptShots.focal_length,
                    ScriptShots.description,
                    ScriptShots.status,
                )
            )
        ).first()
    if row is None:  # pragma: no cover — RETURNING on a successful insert
        raise RuntimeError("insert into script_shots returned no row")
    scene_no = await scene_no_for(scope, scene)
    logger.info(
        "[scoped_script_gateway] CreateShot run=%s scene=%s shot=%s",
        scope.run_id,
        scene.id,
        row.id,
    )
    return _shot_dict(row, scene_no)


async def update_shot(
    scope: AgentRunScope, shot: ResolvedShot, fields: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Update the parameter tags / description of an already-resolved shot.

    Never touches ``status`` or the image/thumbnail/video URLs (the generate
    workflow's lane), never moves the card (``sort_order``), and never
    renumbers it. Returns ``None`` when the caller supplied nothing writable,
    so the tool can say "no fields to update" instead of reporting a
    successful no-op."""
    values = _writable(fields)
    if not values:
        return None
    async with write_scope() as session:
        row = (
            await session.execute(
                update(ScriptShots)
                .where(ScriptShots.id == shot.id)
                .values(**values)
                .returning(
                    ScriptShots.id,
                    ScriptShots.shot_number,
                    ScriptShots.shot_type,
                    ScriptShots.camera_angle,
                    ScriptShots.camera_movement,
                    ScriptShots.focal_length,
                    ScriptShots.description,
                    ScriptShots.status,
                )
            )
        ).first()
    if row is None:  # pragma: no cover — resolver already proved it exists
        return None
    logger.info(
        "[scoped_script_gateway] UpdateShot run=%s shot=%s fields=%s",
        scope.run_id,
        shot.id,
        sorted(values),
    )
    return _shot_dict(row, None)


async def episode_id_for_script(script_id: Any) -> Optional[int]:
    """The ``episode_id`` of one script, for DISPATCH-TIME scope derivation
    only (``scope_binding.resolve_dispatch_scope``).

    Deliberately NOT scope-checked and deliberately NOT exported to tools:
    it runs BEFORE a run exists, on a ``script_id`` the server itself read
    out of ``conversation_ai_meta`` — never a model-supplied value. It can
    only narrow the scope that is about to be created (see
    ``agent_run_scope.scope_for_run``'s note on why a wrong episode is
    self-limiting), so it has no authorization surface of its own.

    Name-suffixed ``_for_script`` and kept out of ``__all__``'s tool-facing
    group so a future reader doesn't mistake it for something a tool may
    call with a model argument."""
    try:
        sid = int(str(script_id))
    except (TypeError, ValueError):
        return None
    async with read_scope() as session:
        return (
            await session.execute(
                select(ScriptProjects.episode_id).where(ScriptProjects.id == sid)
            )
        ).scalar()


__all__ = [
    "MAX_LIST_SCENES",
    "SceneSummary",
    "create_shot",
    "episode_id_for_script",
    "list_scenes_in_scope",
    "list_shots_for_scene",
    "read_scene_elements",
    "scene_no_for",
    "update_shot",
]
