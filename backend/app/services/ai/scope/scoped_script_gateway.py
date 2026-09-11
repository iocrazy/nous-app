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

A5 ADDS ONE IMPORT THIS MODULE PREVIOUSLY AVOIDED — the scene REPOSITORY,
for EXACTLY ONE method. The note on ``_SORT_ORDER_STEP`` explains why A4
copied an integer rather than import ``script_shot_repository``: pulling a
repository in would drag its unscoped getters into the agent-tool import
graph. That reasoning still holds for everything A4 needed, and it does NOT
hold for ``apply_element_ops``. That method IS the ops channel — one
transaction that version-guards ``content_json``, appends the op AND its
inverse to ``script_ops``, and raises the ``VersionConflict`` the editor's
own optimistic-concurrency path already speaks. Re-implementing it here to
avoid an import would create exactly the second, drifting write path spec
§5.2 forbids ("agent 的写入应当走同一条 ops 通道,而不是新开一条旁路").

So A5 imports it, and pays for the widened allow-list entry in
``test_scope_resolver_single_choke_point.py`` with a MECHANICAL check rather
than the prose promise the first cut offered (A5 review, Important 3):
``test_scoped_script_gateway_calls_only_the_ops_channel`` AST-scans this file
and fails if the set of repository methods called here is anything other than
``{apply_element_ops}``. The authorization argument is unchanged from this
module's ORM entry — that call takes ``scene.id`` off an already-resolved
``ResolvedScene``, never a model-supplied id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import ScriptProjects, ScriptScenes, ScriptShotOps, ScriptShots
from app.repositories.script_scene_repository import (
    VersionConflict,
    get_script_scene_repository,
)
from app.services.deliverables.registry import register_deliverable_best_effort
from app.services.script.scene_numbering import (
    derive_shot_label,
    effective_scene_number,
)
from app.services.script.scene_ops import OpError
from app.services.script.version_service import diff_scenes

from .agent_run_scope import AgentRunScope
from .scene_observations import observed_scene, record_scene_read
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


async def _scene_title(scope: AgentRunScope, scene: Any) -> str:
    """「S3 · INT. CAFE - DAY」——场次号 + 场景头，够人在血缘里认出是哪一场。"""
    scene_no = await scene_no_for(scope, scene)
    heading = " ".join(
        str(part)
        for part in (
            getattr(scene, "heading_int_ext", None),
            getattr(scene, "location_text", None),
        )
        if part
    )
    time_of_day = getattr(scene, "time_of_day", None)
    if time_of_day:
        heading = f"{heading} - {time_of_day}" if heading else str(time_of_day)
    return f"S{scene_no} · {heading}" if heading else f"S{scene_no}"


def _shot_title(scene_no: Any, row: Any) -> str:
    """「S3 · Shot 1 · MS」——血缘端点没有标题就只能显示一个裸 id。"""
    parts = [f"S{scene_no}", f"Shot {getattr(row, 'shot_number', '') or '?'}"]
    shot_type = getattr(row, "shot_type", None)
    if shot_type:
        parts.append(str(shot_type))
    return " · ".join(parts)


async def _register_after_write(
    scope: AgentRunScope,
    *,
    kind: str,
    ref_id: Any,
    title: Any,
    step: Optional[int] = None,
) -> None:
    """3a: register a content write as this run's deliverable — AFTER it landed.

    Two things this exists to get right, both about the write having already
    committed by the time we get here:

    - ``title`` may be a coroutine (the scene title needs a read). It is
      awaited INSIDE the guard: a transient read failure here must not turn a
      committed edit into a reported failure, or the agent retries and the
      same change is written twice.
    - No run → return before touching anything. The human editor lane and
      the test sentinel run id both land here; they should cost zero reads.
    """
    if ledger_run_id(scope) is None:
        if hasattr(title, "close"):
            title.close()  # never awaited — don't leak a "never awaited" warning
        return
    try:
        resolved = await title if hasattr(title, "__await__") else title
    except Exception as exc:  # noqa: BLE001 — the write already committed
        logger.error(
            "[scoped_script_gateway] %s %s: title lookup failed (%r) — "
            "registering without a title",
            kind,
            ref_id,
            exc,
        )
        resolved = None
    await register_deliverable_best_effort(
        run_id=scope.run_id,
        kind=kind,
        ref_id=str(ref_id),
        title=resolved,
        turn=1,
        # NOT off ``scope`` — that is the server-bound AUTHORIZATION identity
        # and has no per-call telemetry on it. The step comes down from the
        # tool's run_context, which is where the runner put it.
        step=step,
    )


def ledger_run_id(scope: AgentRunScope) -> Optional[int]:
    """账本/归属用的 BIGINT run id；测试路径的 sentinel run_id="0"（见
    agent_run_scope._SENTINEL_RUN_ID）没有对应 agent_runs 行，写了会 FK
    违约——返回 None 表示这次写入不记账、不归属。"""
    try:
        rid = int(str(scope.run_id))
    except (TypeError, ValueError):
        return None
    return rid if rid > 0 else None


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
    ``ProposeEdit`` both address elements by.

    THIS IS ALSO THE ONE PLACE THAT RECORDS WHAT A RUN HAS SEEN (A5). The
    record has to be written exactly where scene content is handed to a
    model, or it stops describing what the model actually read — which is the
    single thing A5's precondition trusts. Recording the RAW elements, not
    the projection below: the write-time comparison runs ``diff_scenes`` over
    whole elements, and storing the trimmed view would blind it to any key
    the projection drops."""
    raw = _elements(scene.content_json)
    record_scene_read(scope.run_id, scene.id, scene.content_version, raw)
    return [
        {
            "element_id": el.get("id"),
            "type": el.get("type"),
            "text": el.get("text"),
            "character_id": el.get("character_id"),
        }
        for el in raw
        if el.get("id")
    ]


# --------------------------------------------------------------------------- #
# A5 — the edit-safety contract (spec §5.2).
#
# GRANULARITY: element-level, not the whole-scene watermark. The plan made this
# the implementer's call and told them to measure first, so here is what was
# measured (production, 2026-08-04) and — as importantly — what it does and
# does not establish.
#
#   * agent turn length (``agent_runs``, n=99): p50 4.9s, p90 26.4s, max 122s
#     — the exposure window between the agent's read and its write;
#   * editor op inter-arrival WITHIN one scene (``script_ops``, 409 gaps):
#     median 1.4s, 328/409 (80%) under 30s;
#   * op rows touching exactly one element: 407/423 (96%).
#
# WHAT THAT SUPPORTS: a whole-scene watermark would refuse OFTEN during active
# writing. The exposure window is an order of magnitude longer than the gap
# between keystroke-debounced ops, so a writer who is mid-scene — which is
# precisely when they ask for help — moves ``content_version`` under nearly
# every agent turn.
#
# WHAT IT DOES NOT SUPPORT, and the comment previously overclaimed (A5 review,
# Important 2): it does NOT establish that element-level granularity is
# sufficient, or even that it helps much in this corpus.
#   - of 18 scenes, 6 hold exactly ONE element and 3 hold two; where a scene
#     has one element, element-level IS whole-scene and buys nothing;
#   - "96% of ops touch one element" is close to tautological when ops are
#     keystroke-debounce granularity and scenes average 2.1 elements;
#   - op gaps are conditional on an op having happened; they cannot answer
#     "was the author typing WHILE the agent ran";
#   - 417/423 ops come from a single actor, so the corpus contains no
#     human-agent (and no human-human) concurrency sample at all.
#
# Element-level is kept because the fail-closed direction is right and it is
# ~100 lines, not because the data proves it necessary. Revisit with real
# concurrency data.
#
# WHAT IT IS NOT is a second write channel: the write goes through
# ``apply_element_ops`` with a ``content_version``, lands one ``script_ops``
# row with its inverse, and raises the existing ``VersionConflict``. No new
# column, no new table, no migration, and nothing the editor's own path has to
# learn about.
#
# THE PRECONDITION'S INPUT IS THE SERVER'S OWN RECORD, NEVER THE MODEL'S
# (A5 review, Critical). The first cut compared the scene against a
# ``base_content_version`` the MODEL supplied, and skipped the element check
# entirely when that number equalled the current version — while ProposeEdit
# and every conflict refusal helpfully handed the model exactly that number.
# Quoting it back turned the guard off and overwrote the author in silence.
# See ``scene_observations`` for the full write-up; the short version is that
# spec §5.2's "content_version is already a watermark" reasoning holds for the
# editor (which necessarily submits the version it read) and not for an LLM
# (which can emit any integer). Same mechanism, different trust boundary.
#
# So: ``ReadScene`` records what it showed this run, and the comparison runs
# against THAT. The shape is a bounded rebase, applied at most once:
#   nothing this run read     -> REFUSE ("read the scene first")
#   targets unchanged since   -> submit at CURRENT (rebase; the author's edits
#                                elsewhere in the scene survive)
#   any target changed/moved/ -> REFUSE, explicitly
#     removed since
# "At most once" is deliberate. Retrying until it sticks would livelock
# against a fast typist, and each silent retry is another chance to land on
# content nobody re-read. One rebase, then refuse.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EditApplied:
    """An agent edit that landed.

    ``rebased_from`` is non-None when the scene had moved since the agent read
    it and the write was rebased onto the newer version (every targeted
    element having been verified unchanged first). ``quoted_base_version`` is
    what the MODEL claimed it read — carried for observability only; see
    ``apply_element_edit`` on why it is not load-bearing."""

    scene_id: int
    element_ids: tuple[str, ...]
    content_version: int
    rebased_from: Optional[int]
    observed_version: int
    quoted_base_version: Optional[int] = None

    @property
    def quoted_base_mismatch(self) -> bool:
        """The model quoted a version other than the one the server last
        showed it. Not an error — it may simply have read twice — but worth
        surfacing, because a persistent mismatch means the model is
        synthesising the number rather than reporting one."""
        return (
            self.quoted_base_version is not None
            and self.quoted_base_version != self.observed_version
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": str(self.scene_id),
            "element_ids": list(self.element_ids),
            "content_version": self.content_version,
            "rebased_from": self.rebased_from,
            "observed_content_version": self.observed_version,
            "quoted_base_mismatch": self.quoted_base_mismatch,
        }


@dataclass(frozen=True)
class EditRefused:
    """An agent edit that was NOT applied, and why.

    Both audiences are served by one object on purpose (spec §5.2: 冲突必须
    明示). ``code`` is what the model branches on; ``message`` is a sentence a
    human can act on; ``current_elements`` is the passage AS IT IS NOW, so the
    panel can show the writer what changed under them.

    Handing back the current text is safe precisely BECAUSE the precondition
    no longer reads anything the model says: the model cannot turn this
    payload into a successful retry, since the server's record still holds the
    old text until ``ReadScene`` refreshes it. "Re-read the scene" is therefore
    literal, not advisory."""

    code: str
    message: str
    scene_id: int
    element_ids: tuple[str, ...]
    observed_version: Optional[int]
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
            "observed_content_version": self.observed_version,
            "current_content_version": self.current_version,
            "conflicting_element_ids": list(self.conflicting_element_ids),
            "current_elements": [dict(el) for el in self.current_elements],
        }


EditOutcome = Any  # EditApplied | EditRefused — narrowed by isinstance at use

# How a diff_scenes change class reads to a human. "moved" is in here rather
# than being tolerated (A5 review, M1): the previous cut compared element
# DICTS, which carry no position, so an element the author reordered looked
# untouched. ``version_service.diff_scenes`` — the same classifier the version
# history uses — treats a genuine reorder as a change, and this defers to it
# rather than keeping a second, looser opinion.
_CHANGE_WORDING = {
    "changed": "changed after you read them",
    "moved": "were moved to a different place in the scene",
    "removed": "no longer exist in that scene",
    "added": "changed after you read them",
}


def _by_id(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {el["id"]: el for el in elements if el.get("id")}


def _conflict_message(conflicts: dict[str, str]) -> str:
    """The sentence a human reads. Groups by WHAT happened rather than saying
    "conflict", because the writer's next action differs: a changed passage
    can be re-read and rebased, a deleted one cannot."""
    parts = []
    for kind in ("changed", "moved", "removed", "added"):
        ids = sorted(e for e, k in conflicts.items() if k == kind)
        if ids:
            parts.append(
                f"{len(ids)} of the passages you were rewriting "
                f"({', '.join(ids[:5])}) {_CHANGE_WORDING[kind]}"
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
    quoted_base_version: Optional[int] = None,
    actor: str,
    step: Optional[int] = None,
) -> EditOutcome:
    """Rewrite the text of specific elements of an already-resolved scene,
    under the element-level precondition documented above.

    ``edits`` maps element_id -> replacement text; every id must have been
    validated against the scene by the caller (``ProposeEdit`` / ``ApplyEdit``
    do this via ``resolve_selection``, so an id the model invented never
    reaches here). The ops emitted are ``update`` ops carrying only ``text``,
    so an element's ``type`` / ``character_id`` survive — an agent rewriting a
    line of dialogue must not be able to turn it into an action line.

    ``quoted_base_version`` is what the model SAYS it read. It is recorded and
    reported, never used to decide anything: the precondition compares against
    ``scene_observations``, i.e. what this run was actually shown. See the
    Critical note above for what happened when it was load-bearing.

    Returns ``EditApplied`` or ``EditRefused``; never raises for a conflict.
    """
    target_ids = tuple(edits)
    current_version, current_elements = await _current_scene_state(scene.id)
    current_by_id = _by_id(current_elements)

    observation = observed_scene(scope.run_id, scene.id)
    if observation is None:
        # "You cannot write what you never read." Also the fail-closed answer
        # when a record was evicted or the run spans processes — the model
        # re-reads and proceeds, which is exactly what it should do anyway.
        return EditRefused(
            code="scene_not_read",
            message=(
                "Nothing was written. You have not read that scene in this "
                "run, so there is no record of the text you are rewriting. "
                "Call ReadScene first, then edit what it returns."
            ),
            scene_id=scene.id,
            element_ids=target_ids,
            observed_version=None,
            current_version=current_version,
            current_elements=tuple(
                current_by_id[e] for e in target_ids if e in current_by_id
            ),
        )

    # The comparison, against what the server showed — never against a number
    # the model produced. diff_scenes classifies added/removed/changed/moved
    # over the whole element arrays; a target appearing in ANY class means the
    # passage moved under the agent.
    changes = {
        change["id"]: change["kind"]
        for change in diff_scenes(list(observation.elements), current_elements)
    }
    conflicts = {eid: changes[eid] for eid in target_ids if eid in changes}
    if conflicts:
        logger.info(
            "[scoped_script_gateway] edit refused run=%s scene=%s "
            "observed=%s current=%s conflicts=%s",
            scope.run_id,
            scene.id,
            observation.content_version,
            current_version,
            conflicts,
        )
        return EditRefused(
            code="version_conflict",
            message=_conflict_message(conflicts),
            scene_id=scene.id,
            element_ids=target_ids,
            observed_version=observation.content_version,
            current_version=current_version,
            conflicting_element_ids=tuple(sorted(conflicts)),
            current_elements=tuple(
                current_by_id[e] for e in target_ids if e in current_by_id
            ),
        )

    # Every targeted element is exactly as this run was shown it. Anything the
    # author changed is elsewhere in the scene, so rebase onto their work
    # rather than throwing away a good edit.
    rebased_from = (
        observation.content_version
        if observation.content_version != current_version
        else None
    )
    if quoted_base_version is not None and quoted_base_version != (
        observation.content_version
    ):
        logger.info(
            "[scoped_script_gateway] quoted base %s != observed %s "
            "(run=%s scene=%s) — observation governs",
            quoted_base_version,
            observation.content_version,
            scope.run_id,
            scene.id,
        )

    ops = [
        {"op": "update", "element_id": eid, "payload": {"text": text}}
        for eid, text in edits.items()
    ]
    repo = get_script_scene_repository()
    try:
        result = await repo.apply_element_ops(
            str(scene.id), ops, expected_version=current_version, actor=actor
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
            observed_version=observation.content_version,
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
            observed_version=observation.content_version,
            current_version=current_version,
        )

    logger.info(
        "[scoped_script_gateway] edit applied run=%s scene=%s v%s->%s "
        "elements=%s rebased_from=%s",
        scope.run_id,
        scene.id,
        current_version,
        result["content_version"],
        list(target_ids),
        rebased_from,
    )
    # 3a：只有真正写进去的那条路登记——上面每个 EditRefused 都是「什么都没写」，
    # 给一个不存在的版本登记比不登记更糟。
    await _register_after_write(
        scope,
        kind="script_scene",
        ref_id=scene.id,
        title=_scene_title(scope, scene),  # awaited inside the guard
        step=step,
    )
    return EditApplied(
        scene_id=scene.id,
        element_ids=target_ids,
        content_version=int(result["content_version"]),
        rebased_from=rebased_from,
        observed_version=observation.content_version,
        quoted_base_version=quoted_base_version,
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
    scope: AgentRunScope,
    scene: ResolvedScene,
    fields: dict[str, Any],
    *,
    step: Optional[int] = None,
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
        rid = ledger_run_id(scope)
        if rid is not None:
            values["created_by_agent_run_id"] = rid
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
                    ScriptShots.lighting,
                    ScriptShots.description,
                    ScriptShots.status,
                )
            )
        ).first()
        if row is None:  # pragma: no cover — RETURNING on a successful insert
            raise RuntimeError("insert into script_shots returned no row")
        if rid is not None:
            # 同事务——写入失败不留账，记账失败连卡一起回滚。
            await session.execute(
                insert(ScriptShotOps).values(
                    run_id=rid,
                    shot_id=row.id,
                    scene_id=scene.id,
                    action="create",
                    before_json=None,
                    after_json={f: getattr(row, f) for f in _WRITABLE_SHOT_FIELDS},
                )
            )
    scene_no = await scene_no_for(scope, scene)
    logger.info(
        "[scoped_script_gateway] CreateShot run=%s scene=%s shot=%s",
        scope.run_id,
        scene.id,
        row.id,
    )
    # 3a：产出登记。run_id 为空（人手车道 / 测试 sentinel）时是 no-op。
    await _register_after_write(
        scope,
        kind="script_shot",
        ref_id=row.id,
        title=_shot_title(scene_no, row),
        step=step,
    )
    return _shot_dict(row, scene_no)


async def update_shot(
    scope: AgentRunScope,
    shot: ResolvedShot,
    fields: dict[str, Any],
    *,
    step: Optional[int] = None,
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
    rid = ledger_run_id(scope)
    async with write_scope() as session:
        # Locked read of the pre-update values, over the full writable set
        # (not just `values`) — cheap and lets before_json below select
        # only the touched columns without a second query.
        old = (
            await session.execute(
                select(*[getattr(ScriptShots, f) for f in _WRITABLE_SHOT_FIELDS])
                .where(ScriptShots.id == shot.id)
                .with_for_update()
            )
        ).first()
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
                    ScriptShots.lighting,
                    ScriptShots.description,
                    ScriptShots.status,
                )
            )
        ).first()
        if rid is not None and old is not None and row is not None:
            await session.execute(
                insert(ScriptShotOps).values(
                    run_id=rid,
                    shot_id=shot.id,
                    scene_id=shot.scene_id,
                    action="update",
                    before_json={f: getattr(old, f) for f in values},
                    after_json={f: getattr(row, f) for f in values},
                )
            )
    if row is None:  # pragma: no cover — resolver already proved it exists
        return None
    logger.info(
        "[scoped_script_gateway] UpdateShot run=%s shot=%s fields=%s",
        scope.run_id,
        shot.id,
        sorted(values),
    )
    scene_no = await scene_no_for_shot(scope, shot)
    # 3a：改内容 = 新版本。``set_shot_status`` 刻意不在此列。
    await _register_after_write(
        scope,
        kind="script_shot",
        ref_id=shot.id,
        title=_shot_title(scene_no, row),
        step=step,
    )
    return _shot_dict(row, scene_no)


async def set_shot_status(
    scope: AgentRunScope, shot: ResolvedShot, status: str
) -> None:
    """Flip an already-resolved shot's ``status`` column. Dispatch-lifecycle
    use only — see ``generate_shot_image`` in ``screenwriting_tools.py`` (A6
    review fix).

    This is a DIFFERENT write surface from ``update_shot`` above, not a
    loosening of it. ``update_shot``'s docstring reserves ``status`` (and the
    image/video URLs) from the fields it accepts because THAT function backs
    the model-facing ``UpdateShot`` tool — a parameter edit must never let the
    model declare a card "done" or swap in its own url. It says nothing about
    the DISPATCH path itself flipping a transient 'generating' flag before
    kicking off the real generation, which is a distinct, narrower concern
    with its own precedent: ``script_shots_router.py``'s human
    ``/shots/{shot_id}/generate`` REST endpoint does exactly this (`repo.
    update_status(shot_id, "generating")` before dispatch, rolled back to
    ``"empty"`` on dispatch failure) to close the same same-shot race this
    function exists to close for the agent path. Mirroring that shape here —
    via a raw ORM statement against ``ScriptShots``, like ``create_shot``/
    ``update_shot`` already do in this file, rather than a new
    ``get_script_shot_repository()`` call — keeps the write inside this
    file's existing ORM-allowed surface instead of adding a new repository
    dependency for one column.

    No return value and no field whitelist: the caller decides the exact
    status string, there is nothing here for a model to influence (the
    handler passes a literal ``"generating"``/``"empty"``, never anything
    from ``args``)."""
    async with write_scope() as session:
        await session.execute(
            update(ScriptShots).where(ScriptShots.id == shot.id).values(status=status)
        )
    # B4 回流点:镜头置 done 可能让本集 storyboard 判据变真(spec §5)。agent
    # 车道绕过 repo,必须显式接(「触发路径必须类型化回显」同族教训)。
    # fire_* 永不 raise,不影响主写。
    if status == "done":
        from app.services.workflow.surface_completion import fire_surface_sync_for_shot

        await fire_surface_sync_for_shot(str(shot.id))


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
    # Public since 3a: the shot-generate dispatcher needs the SAME reading of
    # "is there a real run behind this" as the ledger does. Two copies of that
    # rule would drift, and the sentinel run_id "0" is exactly the case a
    # second copy gets wrong.
    "ledger_run_id",
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
    "set_shot_status",
    "update_shot",
]
