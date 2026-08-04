"""A4 screenwriting tool handlers — ListScenes / ReadScene / CreateShot /
UpdateShot / ProposeEdit (agent-layer spec §5.1).

Every handler follows the same three steps, in this order, with no
exceptions:

    1. ``scope_for_run(run_id)`` — the run's server-bound scope, re-derived
       fresh from ``agent_runs`` (A2). No handler accepts a scope, project or
       episode as a tool ARGUMENT; a model-supplied scope is not a scope.
    2. ``resolve_scene`` / ``resolve_shot`` / ``resolve_episode`` — turn the
       model's id into an authorized row, or a ``Denied``. These handlers
       never query ``script_scenes`` / ``script_shots`` / ``script_projects``
       themselves; ``tests/test_scope_resolver_single_choke_point.py`` fails
       the build if they start to.
    3. ``scoped_script_gateway`` — the actual read/write, taking the
       ``Resolved*`` handle from step 2 (never a raw id).

Two things this module deliberately does NOT do:

- **It does not check capabilities.** Write grading (read / propose / write)
  is enforced by ``HighRiskCapabilityGateHook`` in the PreToolUse chain, i.e.
  in the executor, before a handler is entered at all (spec §3.1 ③: the
  enforcement point must not be reachable from anything the model can say).
  A capability check duplicated here would be a second, drifting source of
  truth — and worse, it would suggest the hook is optional.
- **It does not raise into the agent loop.** Every failure comes back as a
  ``{"ok": false, "error": ...}`` tool result the model can read and react
  to, matching the house convention in ``generate_media_tools.py``. A denial
  must be VISIBLE to the model and to the transcript — a silent empty result
  would teach the model that the scene simply has no content.

``Denied.reason`` is echoed verbatim and is deliberately generic ("not found
or not accessible in this run"): distinguishing "doesn't exist" from "exists
elsewhere" would let a run enumerate other tenants' ids through the model's
context. The specific reason is in the audit row the resolver already wrote.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.ai.scope import scoped_script_gateway as gateway
from app.services.ai.scope.agent_run_scope import AgentRunScope, scope_for_run
from app.services.ai.scope.scope_resolver import (
    Denied,
    resolve_episode,
    resolve_scene,
    resolve_shot,
)

logger = logging.getLogger(__name__)

# Returned when the run carries no bound scope at all. Distinct from a
# resolver denial on purpose: this one means "this turn has no screenwriting
# context", which is actionable for the user (open the agent from a project /
# script) whereas a denial means "that particular id isn't yours".
_UNBOUND = {
    "ok": False,
    "error": (
        "No script project is bound to this run, so screenwriting tools are "
        "unavailable here. Start the agent from within a project or a script "
        "conversation."
    ),
    "error_code": "scope_unbound",
}

_MAX_ELEMENT_IDS = 50


def _denied(result: Denied) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"{result.resource_type} {result.requested_id}: {result.reason}",
        "error_code": "scope_denied",
    }


async def _bound_scope(run_context: dict) -> Optional[AgentRunScope]:
    """The run's server-bound scope, or ``None`` when there is nothing to
    scope against. ``scope_for_run`` already returns ``None`` for a missing/
    unverifiable run; the extra ``is_bound()`` check collapses "row exists
    but carries no project" into the same answer, so every handler has ONE
    unbound case to handle instead of two."""
    scope = await scope_for_run(run_context.get("run_id"))
    if scope is None or not scope.is_bound():
        return None
    return scope


class ScreenwritingTools:
    """Handlers for the five A4 tools. Stateless — one instance per turn is
    fine, and so is a module-level singleton; all per-run state comes from
    ``run_context``."""

    async def list_scenes(self, args: dict, run_context: dict) -> dict:
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        episode = None
        raw_episode = args.get("episode_id")
        if raw_episode not in (None, ""):
            resolved = await resolve_episode(raw_episode, scope)
            if isinstance(resolved, Denied):
                return _denied(resolved)
            episode = resolved

        limit = args.get("limit")
        limit = (
            min(int(limit), gateway.MAX_LIST_SCENES)
            if isinstance(limit, int) and limit > 0
            else gateway.MAX_LIST_SCENES
        )

        scenes = await gateway.list_scenes_in_scope(scope, episode=episode, limit=limit)
        return {
            "ok": True,
            "count": len(scenes),
            "scenes": [s.as_dict() for s in scenes],
        }

    async def read_scene(self, args: dict, run_context: dict) -> dict:
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        scene = await resolve_scene(args.get("scene_id"), scope)
        if isinstance(scene, Denied):
            return _denied(scene)

        return {
            "ok": True,
            "scene_id": str(scene.id),
            "scene_no_in_episode": await gateway.scene_no_for(scope, scene),
            "heading_int_ext": scene.heading_int_ext,
            "location_text": scene.location_text,
            "time_of_day": scene.time_of_day,
            # The optimistic-concurrency token. Surfaced so ProposeEdit can
            # quote it back and A5's write path can reuse the EXISTING ops
            # channel's VersionConflict semantics rather than inventing a
            # second mechanism (spec §5.2).
            "content_version": scene.content_version,
            "elements": await gateway.read_scene_elements(scope, scene),
            "shots": await gateway.list_shots_for_scene(scope, scene),
        }

    async def create_shot(self, args: dict, run_context: dict) -> dict:
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        scene = await resolve_scene(args.get("scene_id"), scope)
        if isinstance(scene, Denied):
            return _denied(scene)

        try:
            shot = await gateway.create_shot(scope, scene, args)
        except Exception as exc:  # noqa: BLE001 — never raise into the loop
            logger.exception("[screenwriting] CreateShot failed scene=%s", scene.id)
            return {
                "ok": False,
                "error": f"could not create the shot: {exc.__class__.__name__}",
                "error_code": "write_failed",
            }
        return {"ok": True, "scene_id": str(scene.id), "shot": shot}

    async def update_shot(self, args: dict, run_context: dict) -> dict:
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        shot = await resolve_shot(args.get("shot_id"), scope)
        if isinstance(shot, Denied):
            return _denied(shot)

        try:
            updated = await gateway.update_shot(scope, shot, args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[screenwriting] UpdateShot failed shot=%s", shot.id)
            return {
                "ok": False,
                "error": f"could not update the shot: {exc.__class__.__name__}",
                "error_code": "write_failed",
            }
        if updated is None:
            return {
                "ok": False,
                "error": (
                    "no updatable fields supplied — pass at least one of "
                    "shot_type / camera_angle / camera_movement / "
                    "focal_length / lighting / description"
                ),
                "error_code": "no_fields",
            }
        return {"ok": True, "shot": updated}

    async def propose_edit(self, args: dict, run_context: dict) -> dict:
        """Produce a REVIEWABLE proposal; write nothing.

        A4's deliberate boundary (A5 owns the apply path): the proposal is
        validated against the scene the resolver authorized — every
        ``element_id`` must actually exist in that scene's ``content_json``,
        and the scene's current ``content_version`` is captured — then
        returned for a human to accept or reject. Persisting it would need
        an approvals table and an accept/reject UI, both of which are A5/A7
        work; returning it keeps the proposal in the transcript (where A7
        renders it) instead of half-building a store nobody reads yet.

        The staleness check is the *cheap half* of spec §5.2: comparing the
        model's ``base_content_version`` against the scene's current one
        catches "the writer edited while the agent was thinking" at propose
        time. The authoritative check still happens at apply time through the
        existing ops channel's ``VersionConflict`` — this one just avoids
        showing the writer a proposal that is already known to be stale.
        """
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        scene = await resolve_scene(args.get("scene_id"), scope)
        if isinstance(scene, Denied):
            return _denied(scene)

        raw_ids = args.get("element_ids")
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list) or not raw_ids:
            return {
                "ok": False,
                "error": "element_ids must be a non-empty list of element ids",
                "error_code": "invalid_args",
            }
        element_ids = [str(e) for e in raw_ids[:_MAX_ELEMENT_IDS]]

        proposed_text = str(args.get("proposed_text") or "").strip()
        if not proposed_text:
            return {
                "ok": False,
                "error": "proposed_text is required",
                "error_code": "invalid_args",
            }

        known = {
            el["element_id"]
            for el in await gateway.read_scene_elements(scope, scene)
            if el.get("element_id")
        }
        unknown = [e for e in element_ids if e not in known]
        if unknown:
            return {
                "ok": False,
                "error": (
                    "these element_ids are not in that scene: "
                    f"{', '.join(unknown[:10])}. Re-read the scene and anchor "
                    "the proposal to ids it actually contains."
                ),
                "error_code": "unknown_element",
            }

        base_version = args.get("base_content_version")
        stale = isinstance(base_version, int) and base_version != scene.content_version

        return {
            "ok": True,
            "proposal": {
                "scene_id": str(scene.id),
                "scene_no_in_episode": await gateway.scene_no_for(scope, scene),
                "element_ids": element_ids,
                "proposed_text": proposed_text,
                "rationale": str(args.get("rationale") or "").strip() or None,
                "base_content_version": scene.content_version,
            },
            "applied": False,
            "stale": stale,
            "note": (
                "The scene changed since you read it — re-read it and rebase "
                "this proposal before offering it."
                if stale
                else "Proposal recorded for review; the script is unchanged."
            ),
        }


# Module-level singleton: the class is stateless (see its docstring), so the
# runner can dispatch without per-turn construction.
SCREENWRITING_TOOLS = ScreenwritingTools()

# tool name -> bound handler, used by AgentRunner's dispatch.
SCREENWRITING_HANDLERS = {
    "ListScenes": SCREENWRITING_TOOLS.list_scenes,
    "ReadScene": SCREENWRITING_TOOLS.read_scene,
    "CreateShot": SCREENWRITING_TOOLS.create_shot,
    "UpdateShot": SCREENWRITING_TOOLS.update_shot,
    "ProposeEdit": SCREENWRITING_TOOLS.propose_edit,
}


__all__ = [
    "SCREENWRITING_HANDLERS",
    "SCREENWRITING_TOOLS",
    "ScreenwritingTools",
]
