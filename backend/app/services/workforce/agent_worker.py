"""Agent worker runtime — M3 execution layer.

Where M2 stops: it wrote inbox/task/outbox tables + a state machine,
but nothing actually picked tasks off the queue and ran them. This
module is what ``agent_workforce_workflow`` dispatches to. It mirrors the
ChatPanel chat path (compose → AgentRunner.run_turn → RunRecorder)
but reads input from ``agent_tasks.payload`` and writes output to
``agent_outbox`` so the calling agent can see the result.

## Lifecycle of a task as it flows through here

    queued (set by inbox processor)
        ↓ claim_task — CAS guard, flips to 'assigned' and records which DBOS
          workflow owns the row. A replay of THAT SAME workflow is re-admitted
          from 'assigned'/'in_progress'; anyone else is refused.
    assigned
        ↓ this module enters
    in_progress  ← started_at set
        ↓ run_turn() — LLM call(s), tool dispatch
    done | failed  ← ended_at set, result/error fields populated
        ↓ outbox row written (delivers result to calling user/agent)

## What the calling user/agent sees

When this worker finishes a task, it writes an ``agent_outbox`` row
addressed to the original sender (user or agent). The OutboxDispatcher
loop (M2) picks it up and:
  - For ``recipient_kind='user'``: marks delivered (Realtime fires the
    INSERT event to subscribed frontends — the user's ChatPanel sees
    the response in their inbox subscription).
  - For ``recipient_kind='agent'``: re-enqueues into recipient agent's
    inbox so the calling agent can read the result on its next turn.

## Failure semantics

Any exception inside ``run_one_task`` is caught and translated into a
``lifecycle_status='failed'`` UPDATE with ``error_code`` + ``error_message``
populated. The worker pool above swallows the exception so a crash
doesn't poison other agents.

The state machine doesn't run on failure paths from this module — task
lifecycle and worker state are tracked separately. Worker state moves
(idle → working → idle) are wired in by the inbox processor (M2) when
it claims an unread message.

## Why this module PATCHes phase/status directly (route C rule 2 exception)

Route C rule 2 forbids business code from writing task_tracking's lifecycle
columns — ``mirror_dbos_lifecycle_to_tracking`` owns them. It cannot own THESE
rows: a workforce task's DBOS workflow id is ``workforce-<task_id>-<attempt>``
(see ``dbos_pool.workflow_id_for``), while
``task_tracking.dbos_workflow_id`` holds the application-level uuid4 the
repository minted at create time. They never match, the trigger's join finds
nothing, and the row would sit at ``queued`` for the whole run. These writes
are deliberate, not drift. Changing the workflow-id scheme re-opens the
decision: matching ids would hand phase back to the trigger, and these
UPDATEs would start fighting it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from app.core.config import settings
from app.repositories.agent_repository import get_agent_repository
from app.repositories.agent_workforce_repository import (
    AgentWorkforceRepository,
    get_agent_workforce_repository,
)
from app.repositories.skill_repository import get_skill_repository
from app.services.ai.adapters.factory import provider_key_for_model
from app.services.ai.chat.ai_library_chat_wiring import build_agent_runner_stack
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.scope.scope_binding import resolve_dispatch_scope

logger = logging.getLogger(__name__)


# Hard cap on inherited delegation depth before the worker refuses to run
# anything. Mirrors DelegateToolService.MAX_DELEGATION_DEPTH so deeply-
# nested chains never reach a LLM call.
MAX_INHERITED_DEPTH = 3


async def run_one_task(task: dict[str, Any]) -> dict[str, Any]:
    """Execute one ``agent_task`` end-to-end.

    Takes ``task`` as a dispatch ENVELOPE — it only has to carry ``id`` and
    ``workforce_workflow_id``; everything else is read back from the row the
    claim returns.

    Safe on replay: the claim re-admits this same DBOS workflow and refuses
    every other, so a retry of THIS run continues its own work while a second
    worker gets ``skipped``.

    Returns a small dict for observability:
      ``{"task_id": str, "status": str, "run_id": str|None}``

    The background sub-agent branch adds ``idle_dispatch``
    (``{"issue_id": int, "user_id": str} | None``) — an ORDER for the caller,
    not something this function may carry out. Everything here executes inside
    ``run_one_task_step`` (a ``@DBOS.step``), and starting a workflow from
    inside a step is what DBOS asserts against; see ``_run_subagent_task``.
    Other branches never wake an issue, so they omit the key and the body
    reads it with ``.get``.
    """
    task_id_str = task.get("id")
    if not task_id_str:
        logger.error(f"[agent-worker] task missing id: {task}")
        return {"task_id": None, "status": "skipped", "reason": "missing_id"}
    task_id = UUID(task_id_str)

    workforce = get_agent_workforce_repository()

    # Take ownership atomically: one UPDATE, no preceding read. This replaced a
    # read-then-check (get_task, then test the phase) that two workers could
    # both pass — the window between the read and the first write was never
    # guarded.
    #
    # ``workflow_id`` is OUR DBOS workflow id, threaded in by
    # ``agent_workforce_workflow``. It is what lets a replay of THIS run
    # re-enter a row it already moved past 'queued', while still refusing a
    # different worker. Without it a crash between claim and completion strands
    # the row at 'assigned' with nothing able to pick it up again.
    #
    # Absent → refuse, never coerce to "". The empty string is not a harmless
    # default: ``claim_task`` WRITES whatever it is handed into
    # ``metadata.workforce_workflow_id``, so two tasks dispatched without an id
    # would both store "" and each would then satisfy the other's re-entry arm
    # — an ownership check that admits anyone. A producer that forgets the key
    # should fail loudly here rather than quietly share a token.
    workflow_id = str(task.get("workforce_workflow_id") or "").strip()
    if not workflow_id:
        logger.error(f"[agent-worker] task {task_id} dispatched with no workflow id")
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="missing_workflow_id",
            error_message=(
                "task dict carries no workforce_workflow_id; the claim's "
                "ownership token would be empty and shared with every other "
                "id-less task"
            ),
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    claimed = await workforce.claim_task(str(task_id), workflow_id=workflow_id)
    if claimed is None:
        logger.info(f"[agent-worker] task {task_id} not claimable (already taken)")
        return {"task_id": str(task_id), "status": "skipped", "reason": "not_claimable"}
    # The claim returns the authoritative row, so a dispatch order only has to
    # carry an id. It also means the run acts on the CURRENT payload rather
    # than a snapshot taken whenever the order was enqueued.
    task = {**task, **claimed}
    agent_id = UUID(str(task["agent_id"]))
    user_id = UUID(str(task["user_id"]))

    # Refuse to enter the run if depth budget is already blown. The
    # inbox processor should have caught this earlier via DelegateTool,
    # but defense-in-depth is cheap. Checked AFTER the claim: writing a
    # failure onto a row we do not own would stamp another worker's task.
    payload = task.get("payload") or {}
    inherited_depth = int(payload.get("delegated_at_depth") or 0) + 1
    if inherited_depth > MAX_INHERITED_DEPTH:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="depth_exceeded",
            error_message=f"inherited depth {inherited_depth} > max {MAX_INHERITED_DEPTH}",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # Background sub-agent (phase 2b-2 §2.3). BEFORE the persistent gate on
    # purpose: a sub-agent target is ordinarily NOT persistent, so falling
    # through would fail every background Task with 'not_persistent' — the
    # persistent flag gates the DELEGATE path, which this is not.
    if (payload.get("kind") or "") == "subagent":
        return await _run_subagent_task(
            task_id=task_id, payload=payload, workforce=workforce, agent_id=agent_id
        )

    # Resolve the agent record for model + budget + identity.
    agent_repo = get_agent_repository()
    skill_repo = get_skill_repository()
    agent = await agent_repo.get_by_id(agent_id)
    if not agent:
        logger.error(f"[agent-worker] agent {agent_id} not found for task {task_id}")
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="agent_not_found",
            error_message=f"agent_id {agent_id} no longer exists",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # Persistent flag is the gate: refuse to run a non-persistent agent
    # via the workforce path. (The DelegateTool already rejects this on
    # the dispatch side; this is the runner-side equivalent.)
    if not agent.get("persistent"):
        logger.warning(
            f"[agent-worker] agent {agent['slug']} is not persistent — refusing"
        )
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="not_persistent",
            error_message=f"agent '{agent['slug']}' lacks persistent=true",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # Mark in_progress before the LLM call so observability shows what
    # the worker is doing right now.
    # agent_runs.id is BIGINT Snowflake (mig 232) — keep the numeric string;
    # UUID() would raise ValueError on a bigint.
    parent_run_id_raw = payload.get("parent_run_id")
    parent_run_id: Optional[str] = str(parent_run_id_raw) if parent_run_id_raw else None

    user_query = payload.get("prompt") or ""
    if not user_query:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="empty_prompt",
            error_message="task payload missing 'prompt'",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    await workforce.update_task_status(
        task_id=task_id,
        lifecycle_status="in_progress",
    )

    # ── Build the runner stack (parent_run_id + depth inherited) ─────
    try:
        stack = await build_agent_runner_stack(
            agent=agent,
            skill_repo=skill_repo,
            user_id=user_id,
            session_id=None,  # workforce runs aren't bound to a chat session
            user_query=user_query,
            settings=settings,
            parent_run_id=parent_run_id,
            agent_depth=inherited_depth,
        )

        composer = PromptComposer(agent_repo, skill_repo)
        composed = await composer.compose(
            ComposerInput(
                agent_slug=agent["slug"],
                request_instructions=(
                    "You are running as a workforce-dispatched agent. "
                    "A peer agent has handed you a task to complete. "
                    "Produce a complete, self-contained response — the "
                    "result will be delivered back as a single message."
                ),
            )
        )

        # ── Run the turn under RunRecorder ───────────────────────────
        model = composed.model or ""
        try:
            provider = provider_key_for_model(model) if model else None
        except ValueError:
            provider = None

        # A4: a workforce run inherits its dispatcher's scope verbatim —
        # project AND episode. Copying only the project would let an
        # episode-scoped agent widen itself back to project-wide reach simply
        # by handing the work to a peer, which is the kind of escalation that
        # reads like a narrowing at the call site. No parent (a top-level
        # workforce dispatch) leaves both None, i.e. no screenwriting reach.
        dispatch_scope = await resolve_dispatch_scope(parent_run_id=parent_run_id)

        async with RunRecorder(
            agent_id=composed.agent_id,
            user_id=user_id,
            trigger="workforce",
            session_id=None,
            team_id=None,
            **dispatch_scope.as_recorder_kwargs(),
            model=model or None,
            provider=provider,
            input_summary=user_query,
            metadata={
                "task_id": str(task_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "agent_depth": inherited_depth,
            },
        ) as recorder:
            # Tag the run with the parent chain so cost rollup queries
            # (root_run_id) work for delegated trees. The runs repo
            # exposes this; AgentRunsRepository wraps the UPDATE.
            if parent_run_id is not None:
                await _attach_to_parent_run(
                    run_id=recorder.run_id,
                    parent_run_id=parent_run_id,
                    agent_depth=inherited_depth,
                )

            result = await stack.runner.run_turn(
                composed,
                user_messages=[{"role": "user", "content": user_query}],
                recorder=recorder,
            )
            run_id = recorder.run_id
            assistant_content = result.get("content") or ""
            recorder.set_summaries(output_summary=assistant_content)

    except AgentPausedError as err:
        logger.warning(f"[agent-worker] agent paused: {err}")
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="agent_paused",
            error_message=str(err),
        )
        await _move_worker_back_to_idle(agent_id, task_id, trigger="task_completed")
        return {"task_id": str(task_id), "status": "failed", "run_id": None}
    except Exception as err:
        logger.exception(f"[agent-worker] turn failed for task {task_id}: {err}")
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="runtime_error",
            error_message=str(err)[:500],
        )
        await _move_worker_back_to_idle(agent_id, task_id, trigger="task_completed")
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # ── Persist task result + outbox delivery ───────────────────────
    await workforce.update_task_status(
        task_id=task_id,
        lifecycle_status="done",
        current_run_id=run_id,
        result={
            "content": assistant_content,
            "run_id": str(run_id),
            "depth": inherited_depth,
        },
    )

    # Deliver the response back to whoever sent the task. The inbox row
    # references the sender; we route the outbox accordingly.
    inbox_message_id_raw = task.get("inbox_message_id")
    inbox_msg = None
    if inbox_message_id_raw:
        inbox_msg = await _lookup_inbox_message(workforce, UUID(inbox_message_id_raw))

    sender_kind = (inbox_msg or {}).get("sender_kind") or "user"
    sender_user_id_raw = (inbox_msg or {}).get("sender_user_id")
    sender_agent_id_raw = (inbox_msg or {}).get("sender_agent_id")

    await workforce.enqueue_outbox(
        sender_agent_id=agent_id,
        recipient_kind="agent" if sender_kind == "agent" else "user",
        recipient_user_id=(UUID(sender_user_id_raw) if sender_user_id_raw else user_id),
        recipient_agent_id=(UUID(sender_agent_id_raw) if sender_agent_id_raw else None),
        message_type="task_result",
        payload={
            "task_id": str(task_id),
            "run_id": str(run_id),
            "content": assistant_content,
            "depth": inherited_depth,
        },
        task_id=task_id,
    )

    # Worker is done with this task — move state machine back to idle so
    # the inbox processor can dispatch the next message. Without this, the
    # agent stays in 'working' forever and ALLOWED_TRANSITIONS rejects the
    # next 'task_assigned'.
    await _move_worker_back_to_idle(agent_id, task_id, trigger="task_completed")

    return {"task_id": str(task_id), "status": "done", "run_id": str(run_id)}


async def _run_subagent_task(
    *,
    task_id: UUID,
    payload: dict[str, Any],
    workforce: AgentWorkforceRepository,
    agent_id: UUID,
) -> dict[str, Any]:
    """A background sub-agent: rebuild the caller's context from the payload,
    run the very same ``_spawn(await=True)`` the foreground form runs, then
    deliver the envelope.

    EVERY await after the child has run is guarded individually. Once the
    child has run it has cost real money, and the parent's transcript already
    carries a ``subagent_spawned`` telling the user a child is out there — so
    an exception escaping here would strand the task row at 'assigned' AND
    pin ``children.async_pending`` at +1 for the rest of the parent's run.
    Three things therefore always happen, in this order and independently:
    each delivery step is attempted, ``subagent_done`` is emitted, and the
    task row is finalised with a typed ``error_code``.

    Delivery is TWO decisions on an issue target, not one. The inbox row is
    the result itself and is written HERE, unconditionally — an idle issue
    would otherwise have nothing to read the answer from. Whether a turn
    should ALSO start now is the second decision, and this function does not
    make it: it returns ``idle_dispatch``, the order that
    ``agent_workforce_workflow``'s body carries out.

    That split is route C, and it is not a preference. This whole function
    runs inside ``run_one_task_step`` (a ``@DBOS.step``); the idle arm of
    ``deliver_or_dispatch`` reaches ``DBOS.start_workflow``, which DBOS
    refuses from inside a step — ``AssertionError: assert cur_ctx.is_workflow()``
    on EVERY background result delivered to an idle issue (Task 7b defect B).
    The Task 7a sweeper caught each one a minute later, so the only symptoms
    were a delay and an ERROR with a full traceback per sub-agent.

    ``subagent_done`` goes on the PARENT run. That run may have ended turns
    ago; events outlive runs, and the fold counts a ``done`` with no matching
    ``spawned`` (spec §2.4).
    """
    import time

    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )
    from app.services.ai.runner.run_recorder import RunEventWriter
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    started = time.monotonic()
    parent_run_id = payload.get("parent_run_id")
    issue_id = payload.get("issue_id")

    try:
        service = SubAgentTaskService(
            caller_agent_id=UUID(str(payload["caller_agent_id"])),
            caller_user_id=UUID(str(payload["user_id"])),
            parent_run_id=str(parent_run_id) if parent_run_id else None,
            agent_depth=int(payload.get("agent_depth") or 0),
            # spec §2.5: the child run belongs to the parent's issue. Without
            # it the row has no issue link and its spend never reaches the
            # issue rollup.
            issue_id=int(issue_id) if issue_id else None,
        )
    except (KeyError, ValueError) as err:
        logger.error(f"[agent-worker] subagent task {task_id} payload unusable: {err}")
        await _finalise_subagent_task(
            workforce, task_id, ok=False, error_code="bad_subagent_payload"
        )
        return {
            "task_id": str(task_id),
            "status": "failed",
            "run_id": None,
            "idle_dispatch": None,
        }

    # ── run the child ────────────────────────────────────────────────
    failures: list[str] = []
    try:
        envelope = await service.run_background_task(payload)
    except Exception as err:  # noqa: BLE001 — the child must not sink the task
        logger.exception(f"[agent-worker] subagent task {task_id} crashed: {err}")
        envelope = {"status": "failed", "error": f"{err!s:.200}", "summary": ""}
        failures.append("subagent_crashed")

    content = {
        "child_run_id": envelope.get("sub_run_id"),
        "subagent_type": payload.get("subagent_type"),
        "description": payload.get("description"),
        "status": envelope.get("status"),
        "summary": envelope.get("summary") or "",
        "cost_cents": envelope.get("cost_cents") or 0,
        "tokens_used": envelope.get("tokens_used") or 0,
    }

    # ── deliver: inbox row, audit outbox, wake ───────────────────────
    # A malformed reply_to must not raise either: the child already ran, and
    # an exception here would lose its result AND leave the row un-finalised.
    reply_to = payload.get("reply_to") or {}
    target_kind = str(reply_to.get("target_kind") or "")
    try:
        target_id = int(reply_to["target_id"]) if target_kind else None
    except (KeyError, TypeError, ValueError):
        target_kind, target_id = "", None

    if target_kind and target_id is not None:
        try:
            await get_agent_run_inbox_repository().enqueue(
                target_kind=target_kind,
                target_id=target_id,
                user_id=str(payload["user_id"]),
                kind="subagent_result",
                content=content,
            )
        except Exception as err:  # noqa: BLE001
            logger.exception(
                f"[agent-worker] subagent task {task_id}: the result could not "
                f"be delivered to {target_kind} {target_id}: {err}"
            )
            failures.append("result_delivery_failed")
    else:
        logger.error(
            f"[agent-worker] subagent task {task_id} has no usable reply "
            f"target ({reply_to!r}); the result has nowhere to go"
        )
        failures.append("no_reply_target")

    # spec §2.2 item 4: the audit trail beside the delivery. It is NOT the
    # delivery — losing it is logged, but calling the task failed over it
    # would misreport a result the parent has already received.
    try:
        await workforce.enqueue_outbox(
            sender_agent_id=agent_id,
            recipient_kind="user",
            recipient_user_id=UUID(str(payload["user_id"])),
            recipient_agent_id=None,
            message_type="task_result",
            payload={
                "task_id": str(task_id),
                "run_id": content["child_run_id"],
                "content": content["summary"],
                "kind": "subagent",
                "status": content["status"],
            },
            task_id=task_id,
        )
    except Exception as err:  # noqa: BLE001 — audit, not delivery
        logger.exception(
            f"[agent-worker] subagent task {task_id}: audit outbox write "
            f"failed: {err}"
        )

    # The wake ORDER, not the wake. Only an issue has turns to start, so a
    # conversation target carries None — and the key is present either way:
    # an absent key would leave the workflow body guessing whether this branch
    # considered the question at all.
    idle_dispatch: Optional[dict[str, Any]] = (
        {"issue_id": int(target_id), "user_id": str(payload["user_id"])}
        if target_kind == "issue" and target_id is not None
        else None
    )

    # ── always: the parent's transcript, then the task row ───────────
    if parent_run_id:
        try:
            writer = await RunEventWriter.for_run(int(parent_run_id))
            await writer.append(
                "subagent_done",
                {
                    "child_run_id": content["child_run_id"],
                    "task_id": str(task_id),
                    "mode": "async",
                    "subagent_type": content["subagent_type"],
                    "status": content["status"],
                    "cost_cents": content["cost_cents"],
                    "tokens_used": content["tokens_used"],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
        except Exception as err:  # noqa: BLE001 — observability, not the work
            logger.exception(
                f"[agent-worker] subagent_done on parent run {parent_run_id} "
                f"failed: {err}"
            )

    ok = content["status"] == "success" and not failures
    error_code = failures[0] if failures else (None if ok else "subagent_failed")
    await _finalise_subagent_task(
        workforce, task_id, ok=ok, error_code=error_code, error=envelope.get("error")
    )
    return {
        "task_id": str(task_id),
        "status": "success" if ok else "failed",
        "run_id": content["child_run_id"],
        "idle_dispatch": idle_dispatch,
    }


async def _finalise_subagent_task(
    workforce: AgentWorkforceRepository,
    task_id: UUID,
    *,
    ok: bool,
    error_code: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Close the row out. Guarded too: a task left un-finalised is invisible
    to every operator view, so a failure here is the one worth shouting
    about — there is nothing further we can do about it in-process."""
    try:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="done" if ok else "failed",
            **({} if error_code is None else {"error_code": error_code}),
            **({} if not error else {"error_message": str(error)[:500]}),
        )
    except Exception as err:  # noqa: BLE001
        logger.exception(
            f"[agent-worker] subagent task {task_id} could not be finalised "
            f"({'done' if ok else 'failed'}): {err}"
        )


async def _move_worker_back_to_idle(
    agent_id: UUID, task_id: UUID, *, trigger: str
) -> None:
    """Best-effort post-turn worker state transition.

    Fires the state machine so worker state goes back to 'idle' (on
    task_completed) or 'blocked' (on error). Without this, the agent
    stays in 'working' after the first task and ALLOWED_TRANSITIONS
    rejects the next ``task_assigned``.

    Failure is logged + swallowed — ``agent_tasks.lifecycle_status`` is
    the source of truth for whether the work succeeded; worker state
    row is just routing metadata.
    """
    from app.services.workforce.state_machine import (
        InvalidTransitionError,
        WorkerStateMachine,
    )

    try:
        await WorkerStateMachine().transition(
            agent_id=agent_id,
            trigger=trigger,
            task_id=task_id,
            current_task_id=None,
        )
    except InvalidTransitionError as err:
        # Worker is already in idle/terminated/etc — nothing to do.
        logger.debug(
            f"[agent-worker] no state move needed "
            f"(agent={agent_id}, trigger={trigger}): {err}"
        )
    except Exception as err:
        logger.exception(
            f"[agent-worker] post-turn transition failed "
            f"(agent={agent_id}, trigger={trigger}): {err}"
        )


async def _lookup_inbox_message(
    workforce: AgentWorkforceRepository, message_id: UUID
) -> Optional[dict[str, Any]]:
    """Read one inbox row to find the original sender. Best-effort —
    if the row is gone (cascade delete, RLS race), we fall back to the
    task's user_id as the user recipient."""
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AgentInbox

        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            AgentInbox.sender_kind,
                            AgentInbox.sender_user_id,
                            AgentInbox.sender_agent_id,
                        )
                        .where(AgentInbox.id == str(message_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return None
        # uuid → str: the caller feeds sender_*_id into UUID(...), which
        # rejects a UUID object — PostgREST handed back strings here.
        return {
            "sender_kind": row["sender_kind"],
            "sender_user_id": (
                str(row["sender_user_id"]) if row["sender_user_id"] else None
            ),
            "sender_agent_id": (
                str(row["sender_agent_id"]) if row["sender_agent_id"] else None
            ),
        }
    except Exception as err:
        logger.warning(f"[agent-worker] inbox lookup failed: {err}")
        return None


async def _attach_to_parent_run(
    *,
    # agent_runs.id is BIGINT Snowflake (mig 232) → numeric string.
    run_id: str,
    parent_run_id: str,
    agent_depth: int,
) -> None:
    """Set ``agent_runs.parent_run_id`` and propagate ``root_run_id``.

    RunRecorder doesn't take these on construction (M2 added them as a
    schema-only change), so we patch them in via a follow-up UPDATE.
    The parent's ``root_run_id`` is read first; if NULL (parent is root),
    we use the parent's id as the root.
    """

    try:
        from sqlalchemy import select
        from sqlalchemy import update as sa_update

        from app.db.session import read_scope, write_scope
        from app.models import AgentRuns

        # Read parent's root_run_id (or use parent_run_id as fallback if
        # parent is itself a root). agent_runs.id/parent_run_id/root_run_id are
        # BIGINT (mig 232) → bind int.
        async with read_scope() as session:
            parent = (
                await session.execute(
                    select(AgentRuns.root_run_id)
                    .where(AgentRuns.id == int(parent_run_id))
                    .limit(1)
                )
            ).first()
        root_run_id = (parent[0] if parent is not None else None) or int(parent_run_id)

        async with write_scope() as session:
            await session.execute(
                sa_update(AgentRuns)
                .where(AgentRuns.id == int(run_id))
                .values(
                    parent_run_id=int(parent_run_id),
                    root_run_id=int(root_run_id),
                    agent_depth=agent_depth,
                )
            )
    except Exception as err:
        logger.warning(
            f"[agent-worker] failed to attach run {run_id} to parent "
            f"{parent_run_id}: {err}"
        )
