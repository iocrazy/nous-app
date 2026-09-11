"""Screenwriting tool handlers — ListScenes / ReadScene / CreateShot /
UpdateShot (A4) / ProposeEdit / ApplyEdit (A5) — agent-layer spec §5.1.

Every handler follows the same three steps, in this order, with no
exceptions:

    1. ``scope_for_run(run_id)`` — the run's server-bound scope, re-derived
       fresh from ``agent_runs`` (A2). No handler accepts a scope, project or
       episode as a tool ARGUMENT; a model-supplied scope is not a scope.
    2. ``resolve_scene`` / ``resolve_shot`` / ``resolve_episode`` — turn the
       model's id into an authorized row, or a ``Denied``. These handlers
       never query ``script_scenes`` / ``script_shots`` / ``script_projects``
       themselves; ``tests/test_scope_resolver_single_choke_point.py`` fails
       the build if they start to. The two edit tools go through
       ``resolve_selection`` (A5), which is not a fourth resolver but a
       WRAPPER around ``resolve_scene`` that additionally checks the element
       ids are in the scene it just authorized — see
       ``scope/script_selection.py`` for why that second question needs an
       answer of its own.
    3. ``scoped_script_gateway`` — the actual read/write, taking the
       ``Resolved*`` handle from step 2 (never a raw id).

Two things this module deliberately does NOT do:

- **It does not check capabilities.** Write grading (read / propose / write)
  is enforced by ``HighRiskCapabilityGateHook`` in the PreToolUse chain, i.e.
  in the executor, before a handler is entered at all (spec §3.1 ③: the
  enforcement point must not be reachable from anything the model can say).
  A capability check duplicated here would be a second, drifting source of
  truth.

  That delegation is only sound because the gate's PRESENCE is verified
  before dispatch. It is not ambient: ``AgentRunner._run_pre_hooks`` is a
  no-op when the runner was built without hooks, and eight services do
  exactly that while still composing (and therefore advertising) these
  tools. ``AgentRunner._dispatch_screenwriting`` therefore refuses to enter
  any handler on a runner with no ``HighRiskCapabilityGateHook`` installed
  (A4 review, Critical 1). Read the two together: the handlers assume the
  gate ran, and the dispatcher guarantees the gate exists to run.
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

from app.db.session import caller_scope
from app.schemas.script_selection import MAX_SELECTION_ELEMENTS, SelectionRejected
from app.services.ai.scope import scoped_script_gateway as gateway
from app.services.ai.scope.agent_run_scope import AgentRunScope, scope_for_run
from app.services.ai.scope.scope_resolver import (
    Denied,
    resolve_episode,
    resolve_scene,
    resolve_shot,
)
from app.services.ai.scope.script_selection import (
    ResolvedSelection,
    resolve_selection,
    selection_from_run_context,
)
from app.services.deliverables.registry import register_deliverable_best_effort

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

# Imported rather than re-declared: the HTTP selection path and this tool
# path must refuse at the SAME size, or one silently truncates what the other
# accepted. A4 had its own literal 50 here; A5 gives the constant one home.
_MAX_ELEMENT_IDS = MAX_SELECTION_ELEMENTS


def _denied(result: Denied) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"{result.resource_type} {result.requested_id}: {result.reason}",
        "error_code": "scope_denied",
    }


def _edit_actor(scope: AgentRunScope) -> str:
    """The ``script_ops.actor`` an agent edit is stamped with.

    ``agent:<run_id>`` rather than the user's uuid: the column is free-form
    varchar and already carries a non-uuid actor (``version_service`` writes
    ``copilot``), and the run id resolves to the agent, the user AND the
    scope via one join on ``agent_runs``. Attributing the op to the user
    directly would be a lie the editor's own history panel would repeat —
    the writer did not type this, and the ledger is what A7 reads to say so.
    """
    return f"agent:{scope.run_id}"


def _parse_edits(args: dict, selection: "ResolvedSelection") -> Any:
    """``{element_id: replacement_text}`` from the model's arguments.

    Two accepted shapes, and the difference matters (spec §5.3: the payload
    must be a HANDLE, not a blob):

      * ``edits: [{element_id, text}, ...]`` — the precise form. Each
        replacement is bound to the element it replaces.
      * ``proposed_text: "..."`` — A4's original shape, kept working but
        ONLY when the selection names exactly one element. With two or more
        it is ambiguous which one the text replaces, and the old code
        resolved that ambiguity by not resolving it at all (it returned the
        blob and let a human sort it out). Guessing here would put the
        agent's text in the wrong line of dialogue, so it is refused.

    Returns the mapping, or an error dict.
    """
    raw_edits = args.get("edits")
    allowed = set(selection.element_ids)
    edits: dict[str, str] = {}

    if isinstance(raw_edits, list) and raw_edits:
        # Malformed entries are COUNTED, not skipped (A5 review, M2). Dropping
        # them silently meant 3 edits with 1 malformed wrote 2 and reported
        # success — the model would believe a revision landed that never did,
        # and so would the writer reading the transcript.
        malformed = 0
        for item in raw_edits[:_MAX_ELEMENT_IDS]:
            if not isinstance(item, dict):
                malformed += 1
                continue
            eid = str(item.get("element_id") or "").strip()
            text = item.get("text")
            if not eid or text is None:
                malformed += 1
                continue
            edits[eid] = str(text)
        if malformed:
            return {
                "ok": False,
                "error": (
                    f"{malformed} of {len(raw_edits)} edits are malformed — "
                    "each must be an object with a non-empty element_id and a "
                    "text field. Nothing was written; resend the whole batch."
                ),
                "error_code": "invalid_args",
            }
        stray = sorted(set(edits) - allowed)
        if stray:
            return {
                "ok": False,
                "error": (
                    "edits name elements outside the selection: "
                    f"{', '.join(stray[:10])}. Every edit must target one of "
                    f"{', '.join(selection.element_ids)}."
                ),
                "error_code": "unknown_element",
            }
        if not edits:
            return {
                "ok": False,
                "error": (
                    "edits must be a list of {element_id, text} objects with "
                    "both fields set."
                ),
                "error_code": "invalid_args",
            }
        return edits

    proposed_text = args.get("proposed_text")
    if proposed_text is not None and str(proposed_text).strip():
        if len(selection.element_ids) != 1:
            return {
                "ok": False,
                "error": (
                    f"proposed_text is ambiguous across "
                    f"{len(selection.element_ids)} elements — it would be "
                    "guesswork which one it replaces. Use edits: "
                    "[{element_id, text}, ...] to say explicitly."
                ),
                "error_code": "invalid_args",
            }
        return {selection.element_ids[0]: str(proposed_text)}

    return {
        "ok": False,
        "error": (
            "Nothing to write: pass edits as [{element_id, text}, ...] "
            "naming the replacement text for each element."
        ),
        "error_code": "invalid_args",
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


def _shot_deliverable_title(shot: dict) -> Optional[str]:
    """「S3 · Shot 1 · MS」，全部取自网关刚返回的那个 dict —— 零额外读库。"""
    parts = [p for p in (shot.get("shot_label"), shot.get("shot_type")) if p]
    return " · ".join(str(p) for p in parts) or None


def _scene_deliverable_title(scene_no: Any, scene: Any) -> str:
    """「S3 · INT. CAFE - DAY」。``scene_no`` 是 caller_scope 里已经读到的。"""
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


async def _register_write(
    scope: AgentRunScope,
    run_context: dict,
    *,
    kind: str,
    ref_id: Any,
    title: Optional[str],
) -> None:
    """3a：把一次已经**提交**的写入登记成这个 run 的产出。

    ⚠️ 必须在 ``caller_scope`` 的 ``async with`` **退出之后**调用，绝不在里面。
    ``caller_scope`` 把自己发布成 ambient session，里面的 ``write_scope()``
    会 join 那个 ``authenticated`` 事务；而 ``run_deliverables`` 只有
    service_role 策略（mig 453:126-129），``agent_run_transcript_events`` 也
    没给 ``authenticated`` 的 INSERT 策略 —— 登记会拿 42501，**并且把调用方
    还没提交的事务一起弄废**，退出时连分镜/场次的写入一起回滚。best-effort
    吞得掉那个异常，吞不掉已经作废的事务。

    同族先例见 ``caller_scope`` 的 docstring：``agent_run_events`` 的
    best-effort 审计写入同样必须跑在 postgres 上。
    """
    await register_deliverable_best_effort(
        run_id=scope.run_id,
        kind=kind,
        ref_id=str(ref_id),
        title=title,
        turn=run_context.get("turn"),
        step=run_context.get("step"),
        # 这一轮的活 recorder。少了它，登记口退回 ``for_run`` 另开一个
        # writer，``view.outputs`` 会被活 recorder 的下一次整值镜像抹掉
        # （3a T8c 缺陷 1 —— 座舱产出格在生产上从未渲染过）。
        recorder=run_context.get("recorder"),
    )


class ScreenwritingTools:
    """Handlers for the five A4 tools plus A6's GenerateShotImage. Stateless — one instance per turn is
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

        # RLS 第三层 (PR-2b): the actual scene read runs as the calling user,
        # not postgres, so mig-408 tenant policies are enforced at the DB. The
        # scope resolution + episode authorization above ran on postgres on
        # purpose (see caller_scope docstring §3).
        async with caller_scope(scope.user_id):
            scenes = await gateway.list_scenes_in_scope(
                scope, episode=episode, limit=limit
            )
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

        # RLS 第三层 (PR-2b): scene-number derivation, element read and shot
        # list all touch tenant tables — run them as the calling user.
        async with caller_scope(scope.user_id):
            scene_no = await gateway.scene_no_for(scope, scene)
            elements = await gateway.read_scene_elements(scope, scene)
            shots = await gateway.list_shots_for_scene(scope, scene)
        return {
            "ok": True,
            "scene_id": str(scene.id),
            "scene_no_in_episode": scene_no,
            "heading_int_ext": scene.heading_int_ext,
            "location_text": scene.location_text,
            "time_of_day": scene.time_of_day,
            # The optimistic-concurrency token. Surfaced so ProposeEdit can
            # quote it back and A5's write path can reuse the EXISTING ops
            # channel's VersionConflict semantics rather than inventing a
            # second mechanism (spec §5.2).
            "content_version": scene.content_version,
            "elements": elements,
            "shots": shots,
        }

    async def create_shot(self, args: dict, run_context: dict) -> dict:
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        scene = await resolve_scene(args.get("scene_id"), scope)
        if isinstance(scene, Denied):
            return _denied(scene)

        try:
            # RLS 第三层 (PR-2b): the INSERT (and its scene-number read) run as
            # the calling user; WITH CHECK on script_shots refuses a write that
            # would land outside the caller's tenant.
            async with caller_scope(scope.user_id):
                shot = await gateway.create_shot(scope, scene, args)
        except Exception as exc:  # noqa: BLE001 — never raise into the loop
            logger.exception("[screenwriting] CreateShot failed scene=%s", scene.id)
            return {
                "ok": False,
                "error": f"could not create the shot: {exc.__class__.__name__}",
                "error_code": "write_failed",
            }
        # 提交之后才登记（见 ``_register_write`` 的 ⚠️）。
        await _register_write(
            scope,
            run_context,
            kind="script_shot",
            ref_id=shot.get("shot_id"),
            title=_shot_deliverable_title(shot),
        )
        return {"ok": True, "scene_id": str(scene.id), "shot": shot}

    async def generate_shot_image(self, args: dict, run_context: dict) -> dict:
        """Dispatch the existing ``script_shot_generate`` DBOS workflow for
        one shot (A6, spec §5.1). Deliberately thin: this function's only job
        is AUTHORIZATION (resolve the id through THIS run's scope) plus
        DISPATCH — it does not touch the workflow's own logic
        (``script_shot_generate.py``), which is unchanged.

        The id is resolved through ``resolve_shot`` BEFORE the workflow ever
        sees it (A2 review follow-up, closed here): the workflow is handed a
        shot_id that has already been proven, against this run's server-bound
        scope, to belong to it — never a raw model-supplied id passed
        straight through to the workflow's own (unscoped) internal lookup.

        Asynchronous, on purpose (spec §4.5): this call only confirms
        DISPATCH. It does not wait for the image, and it does not report
        success/failure of the generation itself — that lands on the shot row
        later, written by the workflow's own steps. Nothing here is a
        precondition for anything else; there is no model-asserted value
        anywhere in this path that gates a later authorization decision.

        Cost control: the per-call spend cap lives in
        ``HighRiskCapabilityGateHook`` (media.max_calls_per_turn, checked
        BEFORE this handler is ever entered — same mechanism, same turn
        scope, as GenerateImage/GenerateVideo). On top of that, this handler
        flips the shot to ``status='generating'`` BEFORE dispatch and rolls
        back to ``'empty'`` if dispatch itself fails — mirroring
        ``script_shots_router.py``'s human ``/shots/{shot_id}/generate`` REST
        endpoint exactly (see ``gateway.set_shot_status``'s docstring for why
        this is a different write surface from ``UpdateShot``'s, not a
        loosening of it) — so two calls for the SAME shot cannot both slip
        through and pay twice: the second sees ``status == 'generating'``
        (see the check below) and is refused, a SERVER fact rather than
        anything the model asserted.
        """
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        from app.core.config import settings

        if not settings.FEATURE_SHOT_GENERATE:
            return {
                "ok": False,
                "error": "Shot image generation is currently disabled.",
                "error_code": "feature_disabled",
            }

        shot = await resolve_shot(args.get("shot_id"), scope)
        if isinstance(shot, Denied):
            return _denied(shot)

        # A generation already in flight for this EXACT shot — from a
        # human's click via the REST endpoint, or an earlier agent dispatch
        # that already flipped this same flag below — refuses a second one
        # rather than racing it and paying twice.
        if shot.status == "generating":
            return {
                "ok": False,
                "error": (
                    "This shot is already generating an image — wait for it "
                    "to finish (re-read the scene) before dispatching another."
                ),
                "error_code": "already_generating",
            }

        # Claim the shot BEFORE dispatch (closes the same-turn race two
        # back-to-back calls could otherwise both win: without this, both
        # would see status='empty' above and both would proceed to dispatch).
        try:
            # RLS 第三层 (PR-2b): the status claim is a tenant write on
            # script_shots — run it as the calling user. The DBOS dispatch that
            # follows stays on postgres (task_tracking / dbos.* are infra tables
            # authenticated has no grant on).
            async with caller_scope(scope.user_id):
                await gateway.set_shot_status(scope, shot, "generating")
        except Exception as exc:  # noqa: BLE001 — never raise into the loop
            logger.exception(
                "[screenwriting] GenerateShotImage status claim failed shot=%s",
                shot.id,
            )
            return {
                "ok": False,
                "error": f"could not claim the shot for generation: {exc.__class__.__name__}",
                "error_code": "status_claim_failed",
            }

        import uuid as _uuid

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.script_shot_generate import script_shot_generate_workflow

        try:
            mgr = get_task_manager()
            wf_id = str(_uuid.uuid4())
            task_id = await mgr.create(
                user_id=scope.user_id,
                task_type="shot_generate",  # ≤20 chars: task_tracking.task_type is VARCHAR(20)
                title="Generate shot image (agent)",
                dbos_workflow_id=wf_id,
                metadata={"trigger": "agent_tool", "run_id": scope.run_id},
            )
            await start_workflow_routed(
                "script_shot_generate",
                dbos_workflow_callable=script_shot_generate_workflow,
                dbos_workflow_kwargs={
                    # The RESOLVED id, not args["shot_id"] — the whole point
                    # of routing through resolve_shot first.
                    "shot_id": str(shot.id),
                    "user_id": scope.user_id,
                    # 3a: this lane DOES have a run. It was already going into
                    # task_tracking metadata; the workflow needs it too, or the
                    # image it produces has no run to hang off.
                    "run_id": gateway.ledger_run_id(scope),
                    "turn": run_context.get("turn"),
                    "step": run_context.get("step"),
                },
                workflow_id=wf_id,
            )
        except Exception as exc:  # noqa: BLE001 — never raise into the loop
            logger.exception(
                "[screenwriting] GenerateShotImage dispatch failed shot=%s", shot.id
            )
            # Dispatch failed AFTER the status claim above — roll back to the
            # honest pre-dispatch state, same as script_shots_router.py's
            # /generate endpoint does on the same failure mode. Without this
            # the shot would be stuck 'generating' forever with no live task.
            try:
                async with caller_scope(scope.user_id):
                    await gateway.set_shot_status(scope, shot, "empty")
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[screenwriting] GenerateShotImage status rollback failed "
                    "shot=%s",
                    shot.id,
                )
            return {
                "ok": False,
                "error": f"could not dispatch image generation: {exc.__class__.__name__}",
                "error_code": "dispatch_failed",
            }

        logger.info(
            "[screenwriting] GenerateShotImage run=%s shot=%s task=%s",
            scope.run_id,
            shot.id,
            task_id,
        )
        return {
            "ok": True,
            "dispatched": True,
            "shot_id": str(shot.id),
            "task_id": task_id,
            "note": (
                "Generation dispatched asynchronously — it is not done yet. "
                "The shot's image will update once it completes; this call "
                "does not wait and does not know the outcome."
            ),
        }

    async def update_shot(self, args: dict, run_context: dict) -> dict:
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        shot = await resolve_shot(args.get("shot_id"), scope)
        if isinstance(shot, Denied):
            return _denied(shot)

        try:
            # RLS 第三层 (PR-2b): the UPDATE (and its scene-number read) run as
            # the calling user.
            async with caller_scope(scope.user_id):
                updated = await gateway.update_shot(scope, shot, args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[screenwriting] UpdateShot failed shot=%s", shot.id)
            return {
                "ok": False,
                "error": f"could not update the shot: {exc.__class__.__name__}",
                "error_code": "write_failed",
            }
        if updated is not None:
            # 提交之后才登记（见 ``_register_write`` 的 ⚠️）。``None`` 是
            # 「没有可写字段」的 no-op，不是新版本。
            await _register_write(
                scope,
                run_context,
                kind="script_shot",
                ref_id=updated.get("shot_id"),
                title=_shot_deliverable_title(updated),
            )
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

    # ------------------------------------------------------------------ #
    # A5 — the edit-safety contract. ProposeEdit and ApplyEdit share ONE
    # preparation path (``_prepare_edit``) and differ in exactly one thing:
    # whether the write is executed. That is on purpose.
    #
    # WHY TWO TOOLS RATHER THAN ONE THAT SOMETIMES WRITES. A1 graded
    # ProposeEdit at "propose" and made the tiers ordinal precisely so that
    # "an agent trusted to suggest revisions is not thereby trusted to
    # commit them". A single tool whose behaviour depended on the caller's
    # grading would have to ask "what may I do?" INSIDE the handler — the
    # second, drifting source of truth this module's docstring refuses. Two
    # tool names let the existing gate answer it, per call, in the executor,
    # by the only mechanism the model cannot talk its way around: ApplyEdit
    # requires "write", ProposeEdit requires "propose", and a propose-only
    # agent simply never reaches the write path.
    #
    # Both run the SAME validation, so a proposal that came back clean is a
    # proposal that would have applied — a propose-graded agent gets honest
    # feedback rather than a rubber stamp that fails later in someone else's
    # hands.
    # ------------------------------------------------------------------ #

    async def _prepare_edit(
        self, args: dict, run_context: dict, scope: AgentRunScope
    ) -> Any:
        """Resolve the target passage and the per-element replacement text.

        Returns ``(ResolvedSelection, {element_id: text}, base_version)`` or
        an error dict. ``base_version`` is ``None`` when the model quoted
        none — legal for ProposeEdit (nothing is at stake), refused by
        ApplyEdit (see ``apply_edit``).
        """
        # The writer's actual selection outranks the model's recollection of
        # it. When A7's panel attaches one, ids the model typed are ignored
        # rather than merged: a half-model half-human target is a passage
        # nobody chose.
        attached = selection_from_run_context(run_context)
        raw_selection = attached or {
            "scene_id": args.get("scene_id"),
            "element_ids": args.get("element_ids"),
            "summary_text": args.get("summary_text") or "",
        }

        selection = await resolve_selection(raw_selection, scope)
        if isinstance(selection, Denied):
            return _denied(selection)
        if isinstance(selection, SelectionRejected):
            return {
                "ok": False,
                "error": selection.message,
                "error_code": selection.code,
            }

        edits = _parse_edits(args, selection)
        if isinstance(edits, dict) and edits.get("ok") is False:
            return edits

        base_version = args.get("base_content_version")
        if base_version is not None and not isinstance(base_version, bool):
            try:
                base_version = int(base_version)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": (
                        "base_content_version must be the integer "
                        "content_version ReadScene returned."
                    ),
                    "error_code": "invalid_args",
                }
        else:
            base_version = None

        return selection, edits, base_version

    async def propose_edit(self, args: dict, run_context: dict) -> dict:
        """Produce a REVIEWABLE proposal; write nothing.

        Runs the full contract short of the write: the scene is authorized,
        every element id is verified to be in it, and the replacement text is
        bound per element.

        IT DOES NOT ECHO THE SCENE'S CURRENT ``content_version`` (A5 review,
        Critical). The first cut returned it under ``base_content_version``,
        which meant a model whose proposal came back ``stale`` was handed, in
        the same payload, the exact number that used to switch the write
        precondition off. The proposal now carries back the model's OWN quoted
        value — echoing what it told us costs nothing and reveals nothing.
        This is defence in depth rather than the fix: the precondition no
        longer reads any model-supplied number at all (see
        ``scene_observations``), so quoting the current version buys an
        attacker nothing today. Not handing it over keeps it that way.

        ``stale`` stays a whole-scene comparison: at propose time it costs one
        comparison and answers "is there any point showing this to the
        writer". The authoritative, element-level precondition runs in
        ``ApplyEdit``, where a false alarm would cost a real edit rather than
        a re-read.
        """
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        prepared = await self._prepare_edit(args, run_context, scope)
        if isinstance(prepared, dict):
            return prepared
        selection, edits, base_version = prepared
        scene = selection.scene

        stale = base_version is not None and base_version != scene.content_version
        # RLS 第三层 (PR-2b): scene-number derivation touches tenant tables.
        # _prepare_edit above (resolve_selection, which also writes best-effort
        # audit rows) ran on postgres on purpose — see caller_scope docstring §3.
        async with caller_scope(scope.user_id):
            scene_no = await gateway.scene_no_for(scope, scene)
        return {
            "ok": True,
            "applied": False,
            "proposal": {
                "scene_id": str(scene.id),
                "scene_no_in_episode": scene_no,
                "element_ids": list(selection.element_ids),
                "edits": [
                    {"element_id": eid, "text": text} for eid, text in edits.items()
                ],
                "rationale": str(args.get("rationale") or "").strip() or None,
                # The model's own quoted value, echoed back — never the
                # server's current one. See the docstring.
                "base_content_version": base_version,
            },
            "stale": stale,
            "note": (
                "The scene changed since you read it — call ReadScene again "
                "and rebase this proposal before offering it."
                if stale
                else "Proposal recorded for review; the script is unchanged."
            ),
        }

    async def apply_edit(self, args: dict, run_context: dict) -> dict:
        """Write the revision into the script through the existing ops
        channel, under the element-level precondition (spec §5.2).

        "You cannot write what you never read" is enforced by the SERVER's
        record of what this run was shown, not by anything in ``args`` (A5
        review, Critical). ``base_content_version`` stays required — it is
        cheap, it is already in the tool spec, and a persistent mismatch
        against the record is a useful signal that the model is synthesising
        the number — but it decides nothing. The precondition compares the
        scene against ``scene_observations``; a run that never called
        ``ReadScene`` has no record and is refused there, with a message
        telling it to read first.
        """
        scope = await _bound_scope(run_context)
        if scope is None:
            return _UNBOUND

        prepared = await self._prepare_edit(args, run_context, scope)
        if isinstance(prepared, dict):
            return prepared
        selection, edits, base_version = prepared
        scene = selection.scene

        if base_version is None:
            return {
                "ok": False,
                "error": (
                    "base_content_version is required to write. Call ReadScene "
                    "first and quote the content_version it returned — it is "
                    "what proves you are rewriting the text you actually read."
                ),
                "error_code": "missing_precondition",
            }

        # RLS 第三层 (PR-2b): the ops-channel write (script_scenes UPDATE +
        # script_ops INSERT) and the scene-number read run as the calling user;
        # WITH CHECK refuses any write outside the caller's tenant. _prepare_edit
        # above (resolve_selection + its audit writes) ran on postgres on purpose.
        async with caller_scope(scope.user_id):
            outcome = await gateway.apply_element_edit(
                scope,
                scene,
                edits,
                quoted_base_version=base_version,
                actor=_edit_actor(scope),
            )
            if isinstance(outcome, gateway.EditRefused):
                return outcome.as_dict()
            scene_no = await gateway.scene_no_for(scope, scene)

        # 提交之后才登记（见 ``_register_write`` 的 ⚠️）。EditRefused 已经在
        # 上面返回了——「什么都没写」不该留下一个版本。
        await _register_write(
            scope,
            run_context,
            kind="script_scene",
            ref_id=scene.id,
            title=_scene_deliverable_title(scene_no, scene),
        )
        return {
            "ok": True,
            "applied": True,
            "scene_no_in_episode": scene_no,
            **outcome.as_dict(),
            "note": (
                "Applied. The writer had edited elsewhere in the scene "
                f"(version {outcome.rebased_from} -> "
                f"{outcome.content_version}); their changes were kept and "
                "yours rebased on top."
                if outcome.rebased_from is not None
                else "Applied to the scene."
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
    "ApplyEdit": SCREENWRITING_TOOLS.apply_edit,
    "GenerateShotImage": SCREENWRITING_TOOLS.generate_shot_image,
}


__all__ = [
    "SCREENWRITING_HANDLERS",
    "SCREENWRITING_TOOLS",
    "ScreenwritingTools",
]
