"""agent_video DBOS workflow — the half of the agent's GenerateVideo tool that
outlives the turn.

The tool (``services/ai/tools/generate_media_tools.py``) only submits: it
files a ``task_tracking`` row (``task_type='agent_video'``) and starts this
workflow. A run is one turn and there is no deferred tool result, so the
outcome reaches the model the only way anything does after a turn: as a NEW
item on the ``agent_run_inbox`` of the run's issue or conversation.

Shape (modelled on ``script_shot_video``, retired in OpenAPI P7):

1. ``generate_agent_video_step`` — the row the catalog pick lands on decides
   whose machine runs it (``db_registry.resolve_video_route``):
   * **local** (``jimeng-local``) → the user's paired daemon through the
     shared seam (``local_dispatch``). The daemon's upload registers the clip
     from the ticket's attribution (kind ``agent_run``, derivation
     ``video_gen``, run/turn/step, agent, conversation, ``byok``). A
     deterministic failure (daemon offline / timed out / …) comes back as a
     ``{"failed", "code"}`` marker, never a raise.
   * **server** → the existing server generation runs INSIDE the step (no
     600s tool budget applies here) and the clip is registered.
   The step is never retried: a retry re-submits a long, possibly paid job.
2. Delivery, from the BODY, on success AND on failure — every arm independent
   and none allowed to mask the outcome:
   (a) an inbox item ``kind='media_result'`` (dedupe key ``agentvideo-<wf>``),
   (b) a ``generation_result`` notification for the user,
   (c) a ``media_job_done`` event on the dispatching run's transcript,
   (d) issue targets only: ``deliver_or_dispatch(already_enqueued=True)`` —
       starts a turn when the issue is idle. That reaches
       ``DBOS.start_workflow``, which DBOS refuses inside a step, hence the
       body (same split as ``agent_workforce._dispatch_idle_wake``).
   A conversation target has no wake mechanism: the item waits for the user's
   next message (claimed at a step boundary; unclaimed items expire after 24h)
   and the user sees the outcome via (b) and the Task Center.
3. Failure is delivered first and then RAISED (route C §4): a workflow that
   returned a failed dict would read as SUCCESS in ``task_tracking``.

Billing: a local job runs on the user's own machine and account, so its
ticket says ``byok`` and the platform never charges it. The server row has no
``ai_model_prices`` row today. Either way the completion usually lands after
the dispatching run's tree was settled ("一树一扣"), and a settled tree is
never charged again ("只向前不追扣") — so an async completion is structurally
free, and a future PRICED server route must charge at SUBMIT
(``tests/workflows/test_agent_video_workflow.py`` pins the settled half).

Known limits: a worker restart mid-step re-runs the step (DBOS replays an
unfinished step), i.e. re-submits the job; cancelling the task does not reach
a job already running on the daemon.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.services.ai.media.gen_attribution import resolved_attribution
from app.services.generation.local_dispatch import raise_if_failed
from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.resources_service import _resolve_personal_team_id
from app.services.library.scratch_reaper import reap_scratch_dir

_VIDEO_MIME = "video/mp4"
MEDIA_RESULT_KIND = "media_result"
MEDIA_JOB_DONE_EVENT = "media_job_done"
#: Failure a raising step is reported as (the typed ones come from the marker).
GENERIC_FAILURE_CODE = "generation_failed"
_PROMPT_ECHO_MAX = 200
_ERROR_ECHO_MAX = 300


def stream_url(gen_id: str) -> str:
    """Durable serving URL of a video row (``/cover`` is 404 for video)."""
    return f"/api/v1/generated-media/{gen_id}/stream"


async def _scope_for(team_id: Optional[int], user_id: str) -> int:
    if team_id is not None:
        return int(team_id)
    return int(await _resolve_personal_team_id(str(user_id)))


def _clip(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ── generation ───────────────────────────────────────────────────────────


async def _generate_locally(
    route: Any,
    *,
    prompt: str,
    source_image_url: str,
    user_id: str,
    scope_id: int,
    agent_id: Optional[str],
    run_id: Any,
    turn: Optional[int],
    step: Optional[int],
    conversation_id: Optional[int],
) -> dict[str, str]:
    """The picked row runs on the user's OWN machine. ``{"gen_id", ...}`` or
    the ``{"failed", "code"}`` marker (see ``record_failure_detail``)."""
    from app.services.generation.local_dispatch import (
        dispatch_local_generation,
        reconcile_for_engine,
    )
    from app.services.generation.ref_urls import generated_media_ref_url
    from app.services.generation.request import GenerationRequest

    # Same admission rule as the shot path: only a durable generated-media url
    # the server wrote goes to the daemon; anything else is text2video.
    ref = generated_media_ref_url(source_image_url)
    request = GenerationRequest.from_params(
        kind="video", prompt=prompt, model=route.row_name, params={}, source_url=ref
    )
    eff, dropped = await reconcile_for_engine(route.engine, request)
    provider = f"{route.engine}-local"
    result = await dispatch_local_generation(
        engine=route.engine,
        engine_model=route.engine_model,
        media_kind="video",
        request=eff,
        ref_urls=list(eff.refs),
        user_id=user_id,
        scope_id=scope_id,
        attribution={
            "kind": "agent_run",
            "derivation_kind": "video_gen",
            "prompt": prompt,
            "model": route.engine_model,
            "provider": provider,
            "run_id": run_id,
            "turn": turn,
            "step": step,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            # The user's own machine and account: never a platform charge.
            "byok": True,
            "requested": request.knobs_dict(),
            "effective": eff.knobs_dict(),
            "dropped": dropped,
        },
    )
    if result.get("failed"):
        return {
            "failed": str(result["failed"]),
            "code": str(result.get("code") or GENERIC_FAILURE_CODE),
        }
    return {
        "gen_id": str(result.get("gen_id") or ""),
        "provider": provider,
        "model": route.engine_model,
    }


async def _generate_on_server(
    *,
    prompt: str,
    source_image_url: str,
    provider: Optional[str],
    model: Optional[str],
    user_id: str,
    scope_id: int,
    agent_id: Optional[str],
    run_id: Any,
    turn: Optional[int],
    step: Optional[int],
    conversation_id: Optional[int],
) -> dict[str, str]:
    """The existing server generation, now inside a step. Raises on failure."""
    from app.services.ai.media.image_generation_service import (
        ImageGenerationService,
    )

    raw = await ImageGenerationService().generate_video(
        project_id="",
        node_id="",
        source_image_url=source_image_url,
        prompt=prompt,
        provider_name=provider or "",
        model=model or "",
        user_id=user_id,
    )
    produced = str((raw or {}).get("video_url") or (raw or {}).get("video_path") or "")
    if not produced:
        raise RuntimeError("video provider returned no video")
    gen_provider, gen_model = resolved_attribution(
        raw or {}, requested_provider=provider, requested_model=model
    )
    is_url = produced.startswith(("http://", "https://"))
    try:
        row = await register_generated_media(
            user_id=user_id,
            scope_id=scope_id,
            source_url=produced if is_url else None,
            source_path=None if is_url else produced,
            mime=_VIDEO_MIME,
            origin=GenerationOrigin(
                kind="agent_run",
                run_id=run_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                prompt=prompt,
                model=gen_model,
                provider=gen_provider,
                params={"source_image_url": source_image_url},
                derivation_kind="video_gen",
                turn=turn,
                step=step,
            ),
            # No live recorder: the run has usually ended. The registry writes
            # the deliverable through ``RunEventWriter.for_run``.
        )
    finally:
        if not is_url:
            try:
                reap_scratch_dir(produced)
            except Exception as exc:  # noqa: BLE001 - reaping never fails a job
                logger.warning("[agent_video] scratch reap failed: {}", exc)
    gen_id = (row or {}).get("id")
    if gen_id is None:
        raise RuntimeError("register_generated_media returned no id")
    return {
        "gen_id": str(gen_id),
        "provider": gen_provider or "",
        "model": gen_model or "",
    }


@DBOS.step(retries_allowed=False)
async def generate_agent_video_step(
    prompt: str,
    source_image_url: str,
    provider: Optional[str],
    model: Optional[str],
    user_id: str,
    *,
    team_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    run_id: Any = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
    conversation_id: Optional[int] = None,
) -> dict[str, str]:
    """Run the video on whichever machine the picked row names.

    ``{"gen_id", "provider", "model"}`` on success, the ``{"failed", "code"}``
    marker for a deterministic local failure; anything else raises (and is
    not retried — see the module docstring)."""
    from app.services.media.parsers.video_providers import db_registry

    if not user_id:
        raise ValueError("agent video job has no user_id")
    route = await db_registry.resolve_video_route(provider or None, user_id=user_id)
    scope_id = await _scope_for(team_id, user_id)
    common: dict[str, Any] = dict(
        prompt=prompt,
        source_image_url=source_image_url,
        user_id=user_id,
        scope_id=scope_id,
        agent_id=agent_id,
        run_id=run_id,
        turn=turn,
        step=step,
        conversation_id=conversation_id,
    )
    if isinstance(route, db_registry.LocalVideoRoute):
        out = await _generate_locally(route, **common)
    else:
        out = await _generate_on_server(provider=provider, model=model, **common)
    logger.info("[agent_video][step] run {} -> {}", run_id, out)
    return out


# ── delivery ─────────────────────────────────────────────────────────────


def media_result_content(
    *,
    task_id: str,
    status: str,
    gen_id: Optional[str],
    error_code: Optional[str],
    error: Optional[str],
    prompt: str,
) -> dict[str, Any]:
    """The inbox item's content. ``text`` is what the model reads as the body;
    the other keys ride as frame attributes and into the transcript."""
    url = stream_url(gen_id) if gen_id else None
    about = f'GenerateVideo job {task_id} (prompt: "{_clip(prompt, _PROMPT_ECHO_MAX)}")'
    if status == "completed":
        text = (
            f"{about} finished. generated_media_id={gen_id}, url={url}. "
            "It is saved in the user's Generations library."
        )
    else:
        text = (
            f"{about} failed ({error_code}): {_clip(error, _ERROR_ECHO_MAX)}. "
            "Nothing was produced."
        )
    return {
        "status": status,
        "media_kind": "video",
        "generated_media_id": gen_id,
        "url": url,
        "error_code": error_code,
        "task_id": task_id,
        "text": text,
    }


@DBOS.step()
async def enqueue_media_result_step(
    *,
    target_kind: str,
    target_id: int,
    user_id: str,
    content: dict[str, Any],
    dedupe_key: str,
) -> Optional[int]:
    """File the result on the target's inbox. Idempotent on ``dedupe_key``
    (mig 462 unique index), so a replayed body never files it twice. Never
    raises: a lost delivery is logged, and the task still reports the truth."""
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    try:
        row = await get_agent_run_inbox_repository().enqueue(
            target_kind=target_kind,
            target_id=int(target_id),
            user_id=str(user_id),
            kind=MEDIA_RESULT_KIND,
            content=content,
            dedupe_key=dedupe_key,
        )
        return int(row["id"])
    except Exception as exc:  # noqa: BLE001 - logged, not swallowed
        logger.opt(exception=True).error(
            "[agent_video] could not deliver the result to {} {}: {}",
            target_kind,
            target_id,
            exc,
        )
        return None


@DBOS.step()
async def notify_media_result_step(
    *,
    user_id: str,
    status: str,
    target_kind: str,
    target_id: int,
    error_code: Optional[str],
) -> None:
    """The user-facing half. ``notify`` never raises. An issue target links
    the issue (by its human identifier, which the issues route consumes); a
    conversation has no notification link kind, so it links nothing (the Task
    Center row carries the rest).

    ``dedupe=False``: every job is its own event, but the link is shared (or
    absent), so ``notify``'s (user, kind, link) window would swallow a second
    video finishing inside 10 minutes. Replay safety comes from this being a
    memoized DBOS step instead."""
    from app.services.notifications import notify

    ok = status == "completed"
    is_issue = target_kind == "issue"
    await notify(
        user_id=str(user_id),
        kind="generation_result",
        title="Video ready" if ok else "Video generation failed",
        body=(
            "Your agent's video finished rendering. Find it in Generations."
            if ok
            else f"Your agent's video could not be generated ({error_code})."
        ),
        severity="success" if ok else "error",
        link_kind="issue" if is_issue else None,
        link_id=await _issue_link_id(int(target_id)) if is_issue else None,
        dedupe=False,
    )


async def _issue_link_id(issue_id: int) -> str:
    """The issue's human identifier (MH-N) for the deep link, falling back to
    the numeric id (same rule as ``input_gate.mark_awaiting_input``)."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Issues

    try:
        async with read_scope() as session:
            ident = (
                await session.execute(
                    select(Issues.identifier).where(Issues.id == issue_id)
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - logged; the link degrades
        logger.warning(
            "[agent_video] identifier lookup failed issue={}: {}", issue_id, exc
        )
        return str(issue_id)
    return str(ident) if ident else str(issue_id)


@DBOS.step()
async def record_media_job_event_step(
    *,
    run_id: Any,
    payload: dict[str, Any],
    turn: Optional[int],
    step: Optional[int],
) -> None:
    """``media_job_done`` on the dispatching run. That run has usually ended;
    ``for_run`` continues its seq. No fold reads this type, so it never moves
    the run's cost. Observability, not delivery: never raises."""
    if not run_id:
        return
    from app.services.ai.runner.run_recorder import RunEventWriter

    try:
        writer = await RunEventWriter.for_run(int(run_id))
        await writer.append(MEDIA_JOB_DONE_EVENT, payload, turn=turn, step=step)
    except Exception as exc:  # noqa: BLE001 - logged, not swallowed
        logger.opt(exception=True).error(
            "[agent_video] {} on run {} failed: {}", MEDIA_JOB_DONE_EVENT, run_id, exc
        )


async def _wake_issue(issue_id: int, user_id: str, wf_id: str) -> None:
    """BODY only (``deliver_or_dispatch``'s idle arm starts a workflow). The
    item is already on the inbox, so this only decides whether a turn starts
    now; the dedupe key pins the started turn's id to this workflow, so a
    replayed body cannot buy a second turn."""
    from app.services.issues import inbox_or_dispatch

    try:
        outcome = await inbox_or_dispatch.deliver_or_dispatch(
            issue_id,
            kind=MEDIA_RESULT_KIND,
            content={},
            user_id=str(user_id),
            already_enqueued=True,
            dedupe_key=f"agentvideo-wake-{wf_id}",
        )
    except Exception as exc:  # noqa: BLE001 - the sweeper is the backstop
        logger.opt(exception=True).error(
            "[agent_video] issue {} (task {}): result filed, wake-up raised: {}",
            issue_id,
            wf_id,
            exc,
        )
        return
    if outcome.mode == "skipped" and outcome.reason not in (None, "issue_terminal"):
        logger.warning(
            "[agent_video] issue {} (task {}): result filed, no turn started: {}",
            issue_id,
            wf_id,
            outcome.reason,
        )


async def _deliver(
    *,
    wf_id: str,
    user_id: str,
    reply_to_kind: str,
    reply_to_id: int,
    run_id: Any,
    turn: Optional[int],
    step: Optional[int],
    content: dict[str, Any],
) -> None:
    await enqueue_media_result_step(
        target_kind=reply_to_kind,
        target_id=int(reply_to_id),
        user_id=user_id,
        content=content,
        dedupe_key=f"agentvideo-{wf_id}",
    )
    await notify_media_result_step(
        user_id=user_id,
        status=content["status"],
        target_kind=reply_to_kind,
        target_id=int(reply_to_id),
        error_code=content.get("error_code"),
    )
    await record_media_job_event_step(
        run_id=run_id,
        payload={k: v for k, v in content.items() if k != "text"}
        | {"reply_to_kind": reply_to_kind},
        turn=turn,
        step=step,
    )
    if reply_to_kind == "issue":
        await _wake_issue(int(reply_to_id), user_id, wf_id)


@DBOS.workflow()
async def agent_video_workflow(
    *,
    prompt: str,
    source_image_url: str,
    provider: Optional[str],
    model: Optional[str],
    user_id: str,
    reply_to_kind: str,
    reply_to_id: int,
    team_id: Optional[int] = None,
    agent_id: Optional[str] = None,
    run_id: Any = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
    conversation_id: Optional[int] = None,
) -> dict[str, Any]:
    """Generate, deliver, and (on failure) raise. See the module docstring."""
    wf_id = str(DBOS.workflow_id or "")
    deliver = dict(
        wf_id=wf_id,
        user_id=user_id,
        reply_to_kind=reply_to_kind,
        reply_to_id=reply_to_id,
        run_id=run_id,
        turn=turn,
        step=step,
    )
    out: dict[str, Any] = {}
    try:
        out = await generate_agent_video_step(
            prompt,
            source_image_url,
            provider,
            model,
            user_id,
            team_id=team_id,
            agent_id=agent_id,
            run_id=run_id,
            turn=turn,
            step=step,
            conversation_id=conversation_id,
        )
        raise_if_failed(out)
        gen_id = str(out.get("gen_id") or "")
        if not gen_id:
            raise RuntimeError("video job finished without a generated_media id")
    except Exception as exc:
        code = (out or {}).get("code") if (out or {}).get("failed") else None
        await _deliver(
            **deliver,
            content=media_result_content(
                task_id=wf_id,
                status="failed",
                gen_id=None,
                error_code=code or GENERIC_FAILURE_CODE,
                error=str(exc),
                prompt=prompt,
            ),
        )
        raise
    await _deliver(
        **deliver,
        content=media_result_content(
            task_id=wf_id,
            status="completed",
            gen_id=gen_id,
            error_code=None,
            error=None,
            prompt=prompt,
        ),
    )
    return {
        "status": "success",
        "generated_media_id": gen_id,
        "url": stream_url(gen_id),
    }


__all__ = [
    "MEDIA_JOB_DONE_EVENT",
    "MEDIA_RESULT_KIND",
    "agent_video_workflow",
    "enqueue_media_result_step",
    "generate_agent_video_step",
    "media_result_content",
    "notify_media_result_step",
    "record_media_job_event_step",
]
