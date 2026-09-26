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
from app.repositories.agent_references import is_agent_soft_deleted
from app.repositories.agent_repository import get_agent_repository
from app.repositories.agent_workforce_repository import (
    AgentWorkforceRepository,
    get_agent_workforce_repository,
    payload_issue_id,
)
from app.repositories.skill_repository import get_skill_repository
from app.services.ai.adapters.factory import provider_key_for_model
from app.services.ai.chat.ai_library_chat_wiring import build_agent_runner_stack
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.runner.turn_end import result_was_cancelled
from app.services.ai.scope.scope_binding import resolve_dispatch_scope, team_of_run
from app.services.workforce.settle import SettleReason, settle_reason_for_envelope
from app.services.workforce.subagent_delivery import (
    deliver_subagent_result,
    envelope_from_prior_run,
    terminal_run_for_task,
)

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
        # mig 501: a soft-deleted target is refused here too — this branch
        # skips the persistent gate below, where the main path checks it.
        if await is_agent_soft_deleted(agent_id):
            return await _fail_agent_deleted(workforce, task_id, agent_id)
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

    # mig 501: get_by_id returns the tombstone (history face) and it is still
    # persistent=true, so queued inbox work — which the old hard delete
    # CASCADEd away — would otherwise run as a deleted agent.
    if agent.get("deleted_at"):
        return await _fail_agent_deleted(workforce, task_id, agent_id)

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
            # The issue this delegation belongs to, carried on the payload
            # since Task 7b defect F. It has to reach the delegated agent's
            # OWN tool stack: if that agent delegates again, its Delegate tool
            # reads this to stamp the next payload — hop 2 landed with a NULL
            # ``task_tracking.issue_id`` until the review caught it, so an
            # issue's delegated work was findable one level deep and no
            # further. Also stamps the issue on any sub-run it spawns.
            issue_id=payload_issue_id(payload),
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

        # 3c A3：团队跟着派发者走，理由同 subagent_task_service。顶层 workforce
        # 派发没有父 run，继承不到就保持 None —— 回落到 get_team_id_for_user
        # 语义上不等价（一个用户可能属于多个团队），记到别人头上比不记更糟。
        child_team_id = await team_of_run(parent_run_id)

        async with RunRecorder(
            agent_id=composed.agent_id,
            user_id=user_id,
            trigger="workforce",
            session_id=None,
            team_id=child_team_id,
            # agent_runs.task_id (mig 282) — the run↔task link the stale-task
            # reaper reads to tell "worker died mid-run" from "never started".
            task_id=str(task_id),
            # 3c 终审 I1：这一行缺席时，委派出去的活与钱在议题维度整个消失。
            # `issue_id` 从「Runs 树上的一个链接」变成了四个读面的连接键 ——
            # 驾驶舱效率两格、`¢/output` 的分母、`/usage/issues/{id}` 的 token
            # 三列、`search_docs` 的深链，全按它连。子孙有议题（上面交给
            # build_agent_runner_stack 的那个），自己没有，于是这次委派的产出
            # 不进分母、而它的钱通过 root 的树总额进了分子，单价系统性偏高。
            # ⚠️ 在 INSERT 时写是唯一合法的时机：`_finish` 那段注释禁止事后
            # PATCH 的是 project_id / team_id / episode_id 三列，issue_id 不在
            # 其中（phase 2b-2 §4.2 明确它在 INSERT 时写）。
            issue_id=payload_issue_id(payload),
            **dispatch_scope.as_recorder_kwargs(),
            model=model or None,
            provider=provider,
            input_summary=user_query,
            credential_origin=stack.credential_origin,
            # root 一次扣的判据是「这个字段是不是 None」。metadata 里那份是给人
            # 看的 jsonb；扣费不该靠一个随时会改结构的 dict 取键。
            parent_run_id=str(parent_run_id) if parent_run_id else None,
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
            turn_cancelled = result_was_cancelled(result)
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
    # A hook cancel returns normally from run_turn; ``done`` would file
    # stopped work as finished (framework hardening C3). ``cancelled`` is a
    # first-class lifecycle: agent_tasks merged into task_tracking (mig 200)
    # and ``agent_workforce_repository.LIFECYCLE_TO_STATUS`` maps it to the
    # ``cancelled`` status. The outbox still delivers whatever the turn
    # produced — the sender is owed an answer either way.
    final_lifecycle = "cancelled" if turn_cancelled else "done"
    await workforce.update_task_status(
        task_id=task_id,
        lifecycle_status=final_lifecycle,
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
            # fh4 E2 (ruling 3): Delegate says why it settled too. Its result
            # never enters the parent run's inbox (it becomes a task for the
            # sender agent), so this is the only place the reason travels.
            "settle_reason": str(
                SettleReason.KILL if turn_cancelled else SettleReason.PRODUCER
            ),
        },
        task_id=task_id,
    )

    # Worker is done with this task — move state machine back to idle so
    # the inbox processor can dispatch the next message. Without this, the
    # agent stays in 'working' forever and ALLOWED_TRANSITIONS rejects the
    # next 'task_assigned'.
    await _move_worker_back_to_idle(agent_id, task_id, trigger="task_completed")

    return {"task_id": str(task_id), "status": final_lifecycle, "run_id": str(run_id)}


async def _fail_agent_deleted(
    workforce: Any, task_id: UUID, agent_id: UUID
) -> dict[str, Any]:
    """Typed failure for a task whose agent was soft-deleted (mig 501)."""
    logger.warning(f"[agent-worker] agent {agent_id} was deleted — task {task_id}")
    await workforce.update_task_status(
        task_id=task_id,
        lifecycle_status="failed",
        error_code="agent_deleted",
        error_message=f"agent {agent_id} was deleted",
    )
    return {"task_id": str(task_id), "status": "failed", "run_id": None}


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
            workforce,
            task_id,
            lifecycle_status="failed",
            error_code="bad_subagent_payload",
        )
        return {
            "task_id": str(task_id),
            "status": "failed",
            "run_id": None,
            "idle_dispatch": None,
        }

    # ── run the child (or deliver the run a replayed step already made) ──
    failures: list[str] = []
    prior = await terminal_run_for_task(workforce, task_id)
    if prior is not None:
        # fh4 E2c: DBOS replayed this step after the child had finished. The
        # row IS the result; running the child again would bill twice.
        logger.warning(
            f"[agent-worker] subagent task {task_id}: run {prior['id']} already "
            f"ended ({prior.get('status')}); delivering it instead of re-running"
        )
        envelope, settle = envelope_from_prior_run(prior)
    else:
        try:
            envelope = await service.run_background_task(payload, task_id=str(task_id))
        except Exception as err:  # noqa: BLE001 — the child must not sink the task
            logger.exception(f"[agent-worker] subagent task {task_id} crashed: {err}")
            envelope = {"status": "failed", "error": f"{err!s:.200}", "summary": ""}
            failures.append("subagent_crashed")
        settle = settle_reason_for_envelope(envelope.get("status"))

    # 子 run id **解析一次，两处都用**。两处指：下面 ``content["child_run_id"]``
    # 与花费回落读的那一行。分开解析过一次，代价是回落读回来的钱当场又被丢掉 ——
    # ``fold_done`` 在 ``child_run_id`` 为假时整条 ``subagent_done`` 直接丢弃
    # （`folds/subagents.py`），于是父的 ``by_child`` 拿不到那笔钱、
    # ``children.async_pending`` 也不减一，收口继续被挡在门外。
    child_run_id = _resolve_child_run_id(envelope, payload)

    # 崩溃分支自己造的 envelope 没有 ``cost_cents`` 键，而一个跑了十轮工具调用
    # 才挂掉的子 run 花的是真钱 —— 把「没有这个键」读成 0，报 0 的恰恰是最值得
    # 注意的那些 run（Task 7b defect A 同族）。键缺席时改去问子 run 行。
    cost_cents = envelope.get("cost_cents")
    if cost_cents is None:
        cost_cents = await _child_row_cost_cents(child_run_id, task_id)
    elif isinstance(cost_cents, bool) or not isinstance(cost_cents, (int, float)):
        # 键在、但不是个数（信封形状漂移）。这里必须显式归一，不能原样透传：
        # ``fold_done`` 用 ``isinstance(cents, (int, float))`` 判断，非数字会让它
        # **整笔**跳过 ``by_child`` —— 父行少的不是精度，是这个孩子的全部花费。
        # 老写法 `envelope.get("cost_cents") or 0` 顺手把 "" / [] 折成 0，改成
        # 显式判断后这条得自己写回来。真数字（含 0.0）不走这里。
        logger.warning(
            f"[agent-worker] subagent task {task_id}: envelope cost_cents "
            f"{cost_cents!r} is not a number; reporting 0"
        )
        cost_cents = 0.0

    content = {
        "child_run_id": child_run_id,
        "subagent_type": payload.get("subagent_type"),
        "description": payload.get("description"),
        "status": envelope.get("status"),
        # fh4 E2a: who ended it (``settle.SettleReason``), beside what it is.
        "settle_reason": str(settle),
        # A crashed child has NO summary. The model used to get an empty frame
        # for it while the card fell through to the error text; both read the
        # same text now (fh4 E2, recon 1d.5).
        "summary": envelope.get("summary") or envelope.get("error") or "",
        "cost_cents": cost_cents,
        # 同海拔的 BYOK 分量（整棵子树），落到父行的 cost.by_child_byok。
        # ⚠️ 当前无消费方（终审 I3）：扣费按行聚合，不看 by_child*——子 run 的
        # BYOK 由它自己那一行报（见 ai/billing/tree_charge.py 模块 docstring）。
        # 所以漏传这一项今天不会多收钱，只会让父行面板上的分解少半边。
        "byok_cents": envelope.get("byok_cents") or 0,
        "tokens_used": envelope.get("tokens_used") or 0,
    }

    # ── deliver: inbox row, audit outbox, wake ───────────────────────
    # One delivery, shared with the stale-task reaper and keyed by the task
    # (fh4 E2c), so a replayed step or a reaper racing this worker converges on
    # the first writer's row. It never raises: the child already ran.
    delivery = await deliver_subagent_result(
        inbox_repo=get_agent_run_inbox_repository(),
        task_id=str(task_id),
        payload=payload,
        content=content,
    )
    if delivery.failure:
        failures.append(delivery.failure)

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
                "settle_reason": content["settle_reason"],
            },
            task_id=task_id,
        )
    except Exception as err:  # noqa: BLE001 — audit, not delivery
        logger.exception(
            f"[agent-worker] subagent task {task_id}: audit outbox write failed: {err}"
        )

    # The wake ORDER, not the wake (``delivery.idle_dispatch``): only an issue
    # has turns to start, so a conversation target carries None — and the key
    # is present either way, so the workflow body never has to guess whether
    # this branch considered the question at all.
    idle_dispatch = delivery.idle_dispatch

    # ── always: the parent's transcript, then the task row ───────────
    if parent_run_id:
        await emit_async_subagent_done(
            parent_run_id=str(parent_run_id),
            task_id=str(task_id),
            child_run_id=content["child_run_id"],
            subagent_type=content["subagent_type"],
            status=content["status"],
            settle_reason=settle,
            # The card's summary line. A background child's inbox claim lands
            # in a LATER run — the parent had already finished — so the fold's
            # same-run claim/card pairing never fires and this is the only
            # source the card has (MH-90/91/92).
            #
            # A crashed child has NO summary; ``content["summary"]`` already
            # falls through to the error text, which is what keeps its card
            # from going blank (defect J's own symptom) — and since fh4 the
            # model's frame reads the same words.
            summary=content["summary"],
            cost_cents=content["cost_cents"],
            byok_cents=content["byok_cents"],
            tokens_used=content["tokens_used"],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    # Tri-state (framework hardening C3): a delivery failure is still a
    # failure; otherwise the envelope's own verdict decides, and a cancelled
    # child files the task ``cancelled`` — not ``failed`` (nothing broke) and
    # not ``done`` (the work was stopped).
    if failures:
        outcome, error_code = "failed", failures[0]
    elif content["status"] == "success":
        outcome, error_code = "success", None
    elif content["status"] == "cancelled":
        outcome, error_code = "cancelled", None
    else:
        outcome, error_code = "failed", "subagent_failed"
    await _finalise_subagent_task(
        workforce,
        task_id,
        lifecycle_status=_SUBAGENT_OUTCOME_TO_LIFECYCLE[outcome],
        error_code=error_code,
        error=envelope.get("error"),
    )
    return {
        "task_id": str(task_id),
        "status": outcome,
        "run_id": content["child_run_id"],
        "idle_dispatch": idle_dispatch,
    }


async def emit_async_subagent_done(
    *,
    parent_run_id: str,
    task_id: str,
    child_run_id: Optional[str],
    subagent_type: Optional[str],
    status: Optional[str],
    settle_reason: SettleReason,
    summary: str,
    cost_cents: Any,
    byok_cents: Any,
    tokens_used: Any,
    duration_ms: Optional[int],
) -> None:
    """Close one background child on its PARENT run: append ``subagent_done``
    (``mode: async``), then try to settle the tree. Both halves are guarded —
    the delegation already happened, so neither telemetry nor billing may fail
    the caller.

    Two callers, one bookkeeping: the worker's normal completion
    (:func:`_run_subagent_task`) and the stale-task reaper
    (``stale_tasks.reap_stale_workforce_tasks``) closing a child whose worker
    died. A second hand-written copy would let the two drift, and the fold
    only drains ``children.async_pending`` on this exact event shape.

    ``subagent_done`` goes on the PARENT run. That run may have ended turns
    ago; events outlive runs, and the fold counts a ``done`` with no matching
    ``spawned`` (spec §2.4). ``fold_done`` drops the event when
    ``child_run_id`` is falsy, so a caller without one should not expect
    ``async_pending`` to move.
    """
    from app.services.ai.runner.inbox import clip_claimed_text
    from app.services.ai.runner.run_recorder import RunEventWriter

    try:
        writer = await RunEventWriter.for_run(int(parent_run_id))
        await writer.append(
            "subagent_done",
            {
                "child_run_id": child_run_id,
                "task_id": task_id,
                "mode": "async",
                "subagent_type": subagent_type,
                "status": status,
                "settle_reason": str(settle_reason),
                # Bounded by the helper ``inbox_claimed`` uses, so the two
                # projections of one result agree.
                "summary": clip_claimed_text(summary or ""),
                "cost_cents": cost_cents,
                "byok_cents": byok_cents,
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
            },
        )
    except Exception as err:  # noqa: BLE001 — observability, not the work
        logger.exception(
            f"[agent-worker] subagent_done on parent run {parent_run_id} "
            f"failed: {err}"
        )

    # 这一刻是这棵树上**最后一个可观测事件**，也是积分收口唯一能成立的时机。
    #
    # 收口要求全树 ``view.children.async_pending == 0``（异步派发时子 run 的行
    # 还没建出来，「行全终态」不蕴含「树跑完了」）。而那个计数**只在上面这条
    # subagent_done 折进父视图时才减一** —— 它比子 run 自己的 ``_finish`` 晚。
    # 于是异步链上的时序恒为：root 结束 → pending=1 不收口；子 run 结束 →
    # pending 仍是 1，还是不收口；``subagent_done`` 落地归零 —— 而到这一步为止
    # 全仓没有任何人会再调收口（调用点只有 ``_finish`` 与三个崩溃写方）。
    # 不补这一次，每棵异步委派树都要等清扫器 2 小时后强制收口，而那条路径还会
    # 打一条「async child never materialised」的 WARNING —— 与事实正好相反。
    #
    # 传 child：它在树里（``root_run_id`` 指向真 root），而 ``parent_run_id``
    # 未必是 root。收口自己会沿 ``root_run_id`` 解析，并靠 CAS 保证只扣一次。
    if child_run_id:
        try:
            from app.services.ai.billing.tree_charge import settle_tree_if_closed

            await settle_tree_if_closed(run_id=str(child_run_id))
        except Exception as err:  # noqa: BLE001 — 计费绝不连坐这次已完成的委派
            logger.warning(
                f"[agent-worker] tree settle after subagent_done "
                f"(child={child_run_id}) failed: {err}"
            )


def _resolve_child_run_id(
    envelope: dict[str, Any], payload: dict[str, Any]
) -> Optional[str]:
    """这次委派的子 run id，字符串或 None。

    ``_build_envelope`` 与 ``_spawn`` 的崩溃分支都给字符串，所以这里统一成字符串
    —— 下游三个消费方（``fold_done`` 的 ``str()``、``settle_tree_if_closed`` 的
    ``int()``、workflow 结果里的 ``run_id``）对形状的期望本来就是它。

    ``payload["child_run_id"]`` **不在候选里**：那是被续写的**上一轮**，这一轮是
    它的 fork，是另一行。见 :func:`_child_row_cost_cents`。
    """
    raw = envelope.get("sub_run_id") or payload.get("sub_run_id")
    if raw is None:
        return None
    return str(raw).strip() or None


async def _child_row_cost_cents(child_run_id: Optional[str], task_id: UUID) -> float:
    """子 run 行上的 ``cost_cents``，读不到就 0.0（并说出来）。

    只在 envelope **没有** ``cost_cents`` 键时调用 —— 也就是
    ``run_background_task`` 整个抛了出来、这个函数自己造了一个 envelope 的时候。
    有键就按键走，``0.0`` 是一个答案而不是「没答案」。

    ⚠️ 读的是 ``agent_runs.cost_cents``，**老列**：自身 + 已报到的后代，展示语义
    —— 与 ``subagent_done.cost_cents`` 一直以来的口径一致，父行卡片上的那个数就
    该是整棵子树。聚合读面（议题预算 ``prior``、效率账、树总额）自 mig 479 起一律
    读 ``own_cost_cents``，不经过这里，所以这条回落不会把老列重新拉回钱上。

    这是**第二道**防线。第一道在源头：``_spawn`` 崩溃时返回的 envelope 现在带着
    ``sub_run_id`` 与 ``cost_cents``（同批修的），所以子 run 真的跑起来过的那些
    崩溃根本走不到这里 —— 有键，按键走。

    ⚠️ 走到这里的是 ``run_background_task`` **整个抛穿**的情形，而那几处
    （``_child_chain_ok`` / ``_continue_messages`` / ``resolve_dispatch_scope`` /
    ``team_of_run``）都跑在建 recorder 之前 —— 子 run 行压根还不存在，所以今天这
    条路的答案通常就是回落的 0.0，且那个 0 是**对的**。留着这条读库是为了「有 id
    就别猜」：将来谁把子 run id 放进 payload（``sub_run_id``），它立刻生效。
    ``payload["child_run_id"]`` **不算**：那是被续写的**上一轮**，崩掉的这一轮是
    它的 fork，是另一行；拿它的钱冒充这一轮，比报 0 更糟。
    """
    try:
        child_id = int(child_run_id) if child_run_id is not None else None
    except (TypeError, ValueError):
        child_id = None
    if child_id is None:
        logger.warning(
            f"[agent-worker] subagent task {task_id} crashed with no child run "
            f"id; its cost is reported as 0 (it may have spent real money)"
        )
        return 0.0

    from app.repositories.agent_runs_repository import get_agent_runs_repository

    try:
        rows = await get_agent_runs_repository().cost_rows_for_ids([child_id])
    except Exception as err:  # noqa: BLE001 — 遥测永远不该让收口失败
        logger.warning(
            f"[agent-worker] subagent task {task_id}: child run {child_id} cost "
            f"unreadable, reporting 0: {err}"
        )
        return 0.0
    if not rows:
        logger.warning(
            f"[agent-worker] subagent task {task_id}: child run {child_id} has no "
            f"row; cost reported as 0"
        )
        return 0.0
    try:
        return float(rows[0].get("cost_cents") or 0.0)
    except (TypeError, ValueError):
        logger.warning(
            f"[agent-worker] subagent task {task_id}: child run {child_id} cost "
            f"{rows[0].get('cost_cents')!r} is not a number; reporting 0"
        )
        return 0.0


_SUBAGENT_OUTCOME_TO_LIFECYCLE: dict[str, str] = {
    "success": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}


async def _finalise_subagent_task(
    workforce: AgentWorkforceRepository,
    task_id: UUID,
    *,
    lifecycle_status: str,
    error_code: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Close the row out. Guarded too: a task left un-finalised is invisible
    to every operator view, so a failure here is the one worth shouting
    about — there is nothing further we can do about it in-process."""
    try:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status=lifecycle_status,
            **({} if error_code is None else {"error_code": error_code}),
            **({} if not error else {"error_message": str(error)[:500]}),
        )
    except Exception as err:  # noqa: BLE001
        logger.exception(
            f"[agent-worker] subagent task {task_id} could not be finalised "
            f"({lifecycle_status}): {err}"
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
