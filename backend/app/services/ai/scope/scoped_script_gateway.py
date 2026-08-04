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

A5 ADDS ONE IMPORT THIS MODULE PREVIOUSLY AVOIDED — the scene REPOSITORY.
The note on ``_SORT_ORDER_STEP`` explains why A4 copied an integer rather
than import ``script_shot_repository``: pulling a repository in would drag
its unscoped getters into the agent-tool import graph. That reasoning still
holds for everything A4 needed, and it does NOT hold for
``apply_element_ops``. That method IS the ops channel — one transaction that
version-guards ``content_json``, appends the op AND its inverse to
``script_ops``, and raises the ``VersionConflict`` the editor's own
optimistic-concurrency path already speaks. Re-implementing it here to avoid
an import would create exactly the second, drifting write path spec §5.2
forbids ("agent 的写入应当走同一条 ops 通道,而不是新开一条旁路"). So A5
imports it, and pays for the widened allow-list entry in
``test_scope_resolver_single_choke_point.py`` the same way A4 paid for this
module's: the only repository calls below take ``scene.id`` off an
already-resolved ``ResolvedScene``, never a model-supplied id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import ScriptProjects, ScriptScenes, ScriptShots
from app.repositories.script_scene_repository import (
    VersionConflict,
    get_script_scene_repository,
)
from app.services.script.scene_numbering import (
    derive_shot_label,
    effective_scene_number,
)
from app.services.script.scene_ops import OpError
from app.services.script.version_service import replay_to

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


async def _scene_no(scene_id: int, script_id: int) -> Optional[str]:
    """``scene_no_in_episode`` for one scene, keyed on ids the CALLER has
    already had authorized. Private on purpose — the two public wrappers
    below are the only entry points, and each of them takes a ``Resolved*``
    handle, so this never sees a model-supplied id (see the module
    docstring's invariant)."""
    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    ScriptProjects.numbering_locked_at,
                ).where(ScriptProjects.id == script_id)
            )
        ).first()
        is_locked = row is not None and row.numbering_locked_at is not None

        stored = (
            await session.execute(
                select(ScriptScenes.scene_number).where(ScriptScenes.id == scene_id)
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
                    .where(ScriptScenes.script_id == script_id)
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
        index = list(ids).index(scene_id)
    except ValueError:  # pragma: no cover — scene vanished between queries
        return None
    return effective_scene_number(None, index, is_locked)


async def scene_no_for(scope: AgentRunScope, scene: ResolvedScene) -> Optional[str]:
    """``scene_no_in_episode`` for one already-resolved scene.

    Same rule as ``list_scenes_in_scope`` (both end at
    ``effective_scene_number``); separate because ReadScene / CreateShot know
    exactly one scene and shouldn't pay for enumerating its siblings unless
    the number actually has to be derived — which it only does while the
    script is unlocked."""
    return await _scene_no(scene.id, scene.script_id)


async def scene_no_for_shot(scope: AgentRunScope, shot: ResolvedShot) -> Optional[str]:
    """``scene_no_in_episode`` for the scene an already-resolved shot hangs
    off.

    ``ResolvedShot`` carries ``scene_id`` but not ``script_id`` (the
    resolver has no reason to project it), so this costs one extra lookup —
    which is why ``UpdateShot`` is the only caller. No authorization
    concern: a ``ResolvedShot`` can only exist if ``resolve_shot`` already
    proved the whole shot -> scene -> script chain is in this run's scope.

    Exists because ``UpdateShot`` used to return ``shot_label: null`` while
    create and list returned a real label — the model would revise a card
    and watch its own reference disappear (A4 review, Minor)."""
    async with read_scope() as session:
        script_id = (
            await session.execute(
                select(ScriptScenes.script_id).where(ScriptScenes.id == shot.scene_id)
            )
        ).scalar()
    if script_id is None:  # pragma: no cover — resolver proved the chain
        return None
    return await _scene_no(shot.scene_id, script_id)


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


# --------------------------------------------------------------------------- #
# A5 — the edit-safety contract (spec §5.2).
#
# GRANULARITY DECISION, MEASURED (the plan's "第一个待实测项", which explicitly
# says not to pre-assume element-level is needed). Measured on production
# 2026-08-04:
#
#   * agent turn length (``agent_runs``, n=99): p50 4.9s, p90 26.4s, max 122s
#     — this is the exposure window between the agent's read and its write;
#   * editor op inter-arrival WITHIN one scene (``script_ops``, 409 gaps):
#     median 1.4s, and 328/409 (80%) of gaps are under 30s. A writer mid-scene
#     emits ops at typing cadence, so a p90-length turn overlaps ~18 of them;
#   * but writing is LOCALISED: 407/423 (96%) of op rows touch exactly ONE
#     element, and 81/108 (75%) of per-minute windows touch exactly one
#     (89% touch at most two).
#
# Read together those say the whole-scene watermark IS too strict — not
# marginally, but in exactly the situation the feature exists for. Asking the
# agent for help is something a writer does WHILE writing, and a whole-scene
# precondition would then refuse essentially every agent write, while the
# thing the writer actually cares about — the keystrokes they just made — is
# 96% of the time in a DIFFERENT element from the one they handed over.
# Refusing there protects nothing and trains the user to ignore the refusal.
#
# So the precondition is element-level. What it is NOT is a second write
# channel: the write still goes through ``apply_element_ops`` with a
# ``content_version``, still lands one ``script_ops`` row with its inverse,
# still raises the existing ``VersionConflict``. The element-level part is a
# PRECONDITION computed from the existing ledger via the existing, already
# exhaustively-tested ``replay_to`` — no new column, no new table, no
# migration, and nothing the editor's own path has to learn about.
#
# The shape is a bounded rebase, applied at most once:
#   base == current                -> submit at base (nothing moved)
#   base != current, targets same  -> submit at CURRENT (rebase; the writer's
#                                     edits elsewhere in the scene survive)
#   base != current, a target moved-> REFUSE, explicitly
# "At most once" is deliberate. Retrying the rebase until it sticks would
# livelock against a fast typist (median gap 1.4s), and each silent retry is
# another chance to land on content nobody re-read. One rebase, then refuse.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EditApplied:
    """An agent edit that landed. ``rebased_from`` is non-None when the scene
    had moved since the agent read it and the write was rebased onto the
    newer version (the targeted elements were verified untouched first)."""

    scene_id: int
    element_ids: tuple[str, ...]
    content_version: int
    rebased_from: Optional[int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": str(self.scene_id),
            "element_ids": list(self.element_ids),
            "content_version": self.content_version,
            "rebased_from": self.rebased_from,
        }


@dataclass(frozen=True)
class EditRefused:
    """An agent edit that was NOT applied, and why.

    Both audiences are served by one object on purpose (spec §5.2: 冲突必须
    明示). ``code`` is what the model branches on; ``message`` is a sentence
    a human can act on; ``current_elements`` is the passage AS IT IS NOW, so
    the model can rebase without a second round trip and the panel can show
    the writer what actually changed under them."""

    code: str
    message: str
    scene_id: int
    element_ids: tuple[str, ...]
    base_content_version: Optional[int]
    current_version: int
    conflicting_element_ids: tuple[str, ...] = ()
    current_elements: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "applied": False,
            "error_code": self.code,
            "error": self.message,
            "scene_id": str(self.scene_id),
            "element_ids": list(self.element_ids),
            "base_content_version": self.base_content_version,
            "current_content_version": self.current_version,
            "conflicting_element_ids": list(self.conflicting_element_ids),
            "current_elements": [dict(el) for el in self.current_elements],
        }


EditOutcome = Any  # EditApplied | EditRefused — narrowed by isinstance at use


def _by_id(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {el["id"]: el for el in elements if el.get("id")}


def _conflict_message(changed: list[str], gone: list[str]) -> str:
    """The sentence a human reads. Names what moved rather than saying
    'conflict', because the writer's next action differs: a changed passage
    can be re-read and rebased, a deleted one cannot."""
    parts = []
    if changed:
        parts.append(
            f"{len(changed)} of the passages you were rewriting "
            f"({', '.join(changed[:5])}) changed after you read them"
        )
    if gone:
        parts.append(
            f"{len(gone)} ({', '.join(gone[:5])}) no longer exist in that scene"
        )
    return (
        "Nothing was written. "
        + " and ".join(parts)
        + ". Re-read the scene and rebase your edit on the current text before "
        "offering it again."
    )


async def _current_scene_state(scene_id: int) -> tuple[int, list[dict[str, Any]]]:
    """``(content_version, elements)`` read fresh at write time.

    Deliberately NOT taken off ``ResolvedScene``: the resolver's snapshot was
    correct when the id was authorized, and the whole point of this module is
    that time passes between then and the write. Any drift between THIS read
    and the UPDATE is still caught — ``apply_element_ops`` re-guards on
    ``content_version`` inside its own transaction, so there is no TOCTOU
    window this read could open."""
    async with read_scope() as session:
        row = (
            await session.execute(
                select(ScriptScenes.content_version, ScriptScenes.content_json).where(
                    ScriptScenes.id == scene_id
                )
            )
        ).first()
    if row is None:  # pragma: no cover — resolver proved the row exists
        return 0, []
    return int(row.content_version or 0), _elements(row.content_json)


async def apply_element_edit(
    scope: AgentRunScope,
    scene: ResolvedScene,
    edits: dict[str, str],
    *,
    base_content_version: int,
    actor: str,
) -> EditOutcome:
    """Rewrite the text of specific elements of an already-resolved scene,
    under the element-level precondition documented above.

    ``edits`` maps element_id -> replacement text; every id must have been
    validated against the scene by the caller (``ProposeEdit`` does this via
    ``read_scene_elements``, so an id the model invented never reaches here).
    The ops emitted are ``update`` ops carrying only ``text``, so an element's
    ``type`` / ``character_id`` survive — an agent rewriting a line of
    dialogue must not be able to turn it into an action line.

    Returns ``EditApplied`` or ``EditRefused``; never raises for a conflict.
    """
    target_ids = tuple(edits)
    current_version, current_elements = await _current_scene_state(scene.id)
    current_by_id = _by_id(current_elements)

    rebased_from: Optional[int] = None
    expected = base_content_version

    if base_content_version > current_version:
        # The agent quoted a version that does not exist yet. Nothing sane to
        # rebase onto, and applying would mean trusting a number the model
        # produced over what the table says.
        return EditRefused(
            code="version_conflict",
            message=(
                "Nothing was written. The content_version you quoted "
                f"({base_content_version}) is newer than the scene's actual "
                f"version ({current_version}) — re-read the scene and use the "
                "content_version it returns."
            ),
            scene_id=scene.id,
            element_ids=target_ids,
            base_content_version=base_content_version,
            current_version=current_version,
            current_elements=tuple(
                current_by_id[e] for e in target_ids if e in current_by_id
            ),
        )

    if base_content_version != current_version:
        # The scene moved while the agent was thinking. Whether that matters
        # depends ENTIRELY on whether it moved under the passages being
        # rewritten — see the granularity note above.
        repo = get_script_scene_repository()
        at_base = _by_id(
            replay_to(await repo.list_ops_by_scene(str(scene.id)), base_content_version)
        )
        gone = [e for e in target_ids if e not in current_by_id]
        changed = [
            e
            for e in target_ids
            if e in current_by_id and current_by_id[e] != at_base.get(e)
        ]
        if gone or changed:
            logger.info(
                "[scoped_script_gateway] edit refused run=%s scene=%s "
                "base=%s current=%s changed=%s gone=%s",
                scope.run_id,
                scene.id,
                base_content_version,
                current_version,
                changed,
                gone,
            )
            return EditRefused(
                code="version_conflict",
                message=_conflict_message(changed, gone),
                scene_id=scene.id,
                element_ids=target_ids,
                base_content_version=base_content_version,
                current_version=current_version,
                conflicting_element_ids=tuple(changed + gone),
                current_elements=tuple(
                    current_by_id[e] for e in target_ids if e in current_by_id
                ),
            )
        # Every targeted element is byte-identical to what the agent read.
        # The writer's concurrent work is elsewhere in the scene; rebase onto
        # it rather than throwing away a good edit.
        rebased_from = base_content_version
        expected = current_version

    ops = [
        {"op": "update", "element_id": eid, "payload": {"text": text}}
        for eid, text in edits.items()
    ]
    repo = get_script_scene_repository()
    try:
        result = await repo.apply_element_ops(
            str(scene.id), ops, expected_version=expected, actor=actor
        )
    except VersionConflict as conflict:
        # A writer won the race between _current_scene_state and the UPDATE.
        # This is the SAME exception the editor's own path raises; we do not
        # retry (see the "at most once" note above).
        fresh = _by_id([el for el in conflict.elements if isinstance(el, dict)])
        return EditRefused(
            code="version_conflict",
            message=(
                "Nothing was written. That scene was edited in the moment "
                "between reading it and writing — re-read it and try again."
            ),
            scene_id=scene.id,
            element_ids=target_ids,
            base_content_version=base_content_version,
            current_version=conflict.current_version,
            conflicting_element_ids=target_ids,
            current_elements=tuple(fresh[e] for e in target_ids if e in fresh),
        )
    except OpError as op_error:
        # The protocol rejected the batch (e.g. the element vanished between
        # our precondition check and the transaction). Surfaced with its own
        # code so the model does not read a malformed-op bug as a conflict.
        return EditRefused(
            code="op_rejected",
            message=(
                "Nothing was written. The script rejected the edit: "
                f"{op_error.message}"
            ),
            scene_id=scene.id,
            element_ids=target_ids,
            base_content_version=base_content_version,
            current_version=current_version,
        )

    logger.info(
        "[scoped_script_gateway] edit applied run=%s scene=%s v%s->%s "
        "elements=%s rebased_from=%s",
        scope.run_id,
        scene.id,
        expected,
        result["content_version"],
        list(target_ids),
        rebased_from,
    )
    return EditApplied(
        scene_id=scene.id,
        element_ids=target_ids,
        content_version=int(result["content_version"]),
        rebased_from=rebased_from,
    )


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
    return _shot_dict(row, await scene_no_for_shot(scope, shot))


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
    "EditApplied",
    "EditRefused",
    "SceneSummary",
    "apply_element_edit",
    "create_shot",
    "episode_id_for_script",
    "list_scenes_in_scope",
    "list_shots_for_scene",
    "read_scene_elements",
    "scene_no_for",
    "scene_no_for_shot",
    "update_shot",
]
