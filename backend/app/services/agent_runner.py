"""Drive a single agent turn with tool-call resolution + hook chain.

Optional ``recorder`` (:class:`RunRecorder`) — when passed, this runner
refreshes its heartbeat between iterations, polls ``cancel_requested``,
forwards token usage, and records skill invocations. All telemetry
failures are swallowed inside RunRecorder so the agent run itself
never breaks on a dead telemetry path.

Optional ``hooks`` (:class:`HookRegistry`) — when passed, runs registered
PreToolUse / PostToolUse chains around each tool dispatch. See
``app.services.hooks.__init__`` for the contract. With an empty registry
(default), behaviour is byte-for-byte identical to the pre-hook version
(this is the regression contract M1.A tests enforce).

Hook decisions:
- ``continue``        → proceed normally
- ``modify``          → tool args replaced with result.modified_args
- ``abort``           → terminate the run, surface abort_reason
- ``await_approval``  → pause the run, persist approval_request (UI M4)

Hook exceptions are caught and logged. The run continues — a buggy hook
must never break ChatPanel.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import UUID

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    PostToolUseHook,
    PreToolUseHook,
)
from app.services.run_recorder import RunRecorder
from app.services.skill_tool_service import SkillToolService

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

# Tool names recognised by the runner. Anything else is silently ignored
# (forward-compat with future caller-provided tools).
SUPPORTED_TOOLS: frozenset[str] = frozenset({"Skill", "Delegate"})


class AgentRunner:
    def __init__(
        self,
        adapter: Any,
        skill_tool: SkillToolService,
        *,
        hooks: Optional[HookRegistry] = None,
        delegate_tool: Optional[Any] = None,
    ) -> None:
        self.adapter = adapter
        self.skill_tool = skill_tool
        self.hooks = hooks  # None = no hook chain (back-compat default)
        # Optional cross-agent dispatch tool. When None, ``Delegate`` calls
        # are answered with an explicit "tool not configured" so the LLM
        # gets useful feedback instead of silent skip behaviour.
        self.delegate_tool = delegate_tool

    async def run_turn(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
        *,
        recorder: Optional[RunRecorder] = None,
    ) -> dict[str, Any]:
        # Pre-flight: context budget guard. A small-context model
        # (e.g. user filled qwen-max with a heavy AGENT spec) would
        # otherwise return truncated nonsense or fail with cryptic
        # provider errors. Reject early with a structured error.
        try:
            from app.agent_framework import (
                ContextWindowError,
                check_context_budget,
            )
            check_context_budget(
                system_prompt=composed.system_message,
                user_messages=user_messages,
                model=composed.model,
            )
        except ContextWindowError as exc:
            logger.warning(f"[AgentRunner] context budget rejected: {exc}")
            return {
                "content": "",
                "raw": None,
                "error": str(exc),
                "error_code": "context_budget_exceeded",
            }
        # ContextWindowWarning is emitted via warnings module — picked
        # up by loguru's stdlib bridge if configured. Don't block on it.

        messages = list(user_messages)
        iteration = 0
        # Step A milestone: trace each Skill / Delegate dispatch made
        # during this turn. The chat service surfaces this list so the
        # frontend can render sub-task cards inline ("→ summarize, 24s,
        # ¢0.27") without needing a separate streaming channel. Order
        # mirrors LLM emission order. Result is best-effort serialised
        # (we strip non-JSON values so dataclasses / UUIDs don't poison
        # the response payload).
        tool_call_trace: list[dict[str, Any]] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            iteration += 1

            if recorder is not None:
                await recorder.heartbeat()
                if await recorder.check_cancelled():
                    return {"content": "", "raw": None, "cancelled": True}

            resp = await self.adapter.call(composed, messages)

            if recorder is not None:
                usage = resp.get("usage") or {}
                recorder.record_usage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                )

            msg = resp["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # msg.get("content") can be None (e.g. Claude emits null
                # content on a pure-tool-use turn). The `or ""` guarantees
                # the contract — callers always receive a str.
                return {
                    "content": msg.get("content") or "",
                    "raw": resp,
                    "tool_calls": tool_call_trace,
                }

            # Append assistant tool-call stub. Tracked separately so we
            # can strip it on a PreToolUse abort — leaving an assistant
            # tool_calls message in `messages` without its paired tool
            # reply would 400 the next API call (orphaned tool_use).
            messages.append(msg)
            assistant_msg_index = len(messages) - 1

            # Resolve each tool call (with hook chain bracketing).
            for call in tool_calls:
                fn = call.get("function") or {}
                tool_name = fn.get("name")
                if tool_name not in SUPPORTED_TOOLS:
                    # Unknown tool — skip (caller-provided tools handled elsewhere in future)
                    continue
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                # ── PreToolUse chain ────────────────────────────────────────
                pre_result = await self._run_pre_hooks(
                    composed=composed,
                    recorder=recorder,
                    tool_name=tool_name,
                    args=args,
                    iteration=iteration,
                )
                if pre_result is not None:
                    if pre_result.decision == "abort":
                        # Strip the assistant tool_calls msg we just
                        # appended — no reply is going to follow.
                        if 0 <= assistant_msg_index < len(messages):
                            messages.pop(assistant_msg_index)
                        return self._aborted_response(pre_result)
                    if pre_result.decision == "await_approval":
                        if 0 <= assistant_msg_index < len(messages):
                            messages.pop(assistant_msg_index)
                        return self._awaiting_approval_response(pre_result)
                    if pre_result.decision == "modify" and pre_result.modified_args:
                        args = pre_result.modified_args

                # ── Tool dispatch ──────────────────────────────────────────
                if tool_name == "Skill":
                    if recorder is not None and args.get("skill"):
                        recorder.record_skill(str(args["skill"]))
                    result = await self.skill_tool.execute(args)
                else:  # tool_name == "Delegate"
                    if self.delegate_tool is None:
                        result = {
                            "error": (
                                "Delegate tool not configured for this run. "
                                "Cross-agent dispatch requires a "
                                "DelegateToolService — wiring this run "
                                "didn't supply one."
                            )
                        }
                    else:
                        result = await self.delegate_tool.execute(args)

                # Trace for the chat UI. Done AFTER the dispatch so the
                # result is captured. The result payload is already a
                # plain dict from skill_tool / delegate_tool; we don't
                # truncate here — the frontend renders summarised, this
                # keeps the API truthful.
                tool_call_trace.append(
                    {
                        "name": tool_name,
                        "args": args,
                        "result": result,
                        "iteration": iteration,
                    }
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

                # ── PostToolUse chain ──────────────────────────────────────
                post_result = await self._run_post_hooks(
                    composed=composed,
                    recorder=recorder,
                    tool_name=tool_name,
                    args=args,
                    tool_result=result,
                    iteration=iteration,
                )
                if post_result is not None:
                    if post_result.decision == "abort":
                        return self._aborted_response(post_result)
                    if post_result.decision == "await_approval":
                        return self._awaiting_approval_response(post_result)

        return {"content": "", "raw": None, "error": "max_tool_iterations_exceeded"}

    # ------------------------------------------------------------------
    # Hook chain plumbing
    # ------------------------------------------------------------------

    async def _run_pre_hooks(
        self,
        *,
        composed: ComposedSystemPrompt,
        recorder: Optional[RunRecorder],
        tool_name: str,
        args: dict[str, Any],
        iteration: int,
    ) -> Optional[HookResult]:
        """Run PreToolUse chain. Returns terminal HookResult or None to continue.

        Empty registry → returns None immediately (zero overhead). Each
        hook's exception is caught and logged; chain continues to the next
        hook. Modify decisions accumulate (later hooks see modified args).
        """
        if self.hooks is None:
            return None

        ctx = self._build_context(
            composed=composed,
            recorder=recorder,
            tool_name=tool_name,
            args=args,
            iteration=iteration,
        )

        last_result: Optional[HookResult] = None
        for entry in self.hooks.get_pre_hooks():
            hook_result = await self._safe_invoke_pre(entry.name, entry.hook, ctx)
            if hook_result is None:
                continue
            self._dispatch_side_effect(entry.name, hook_result)
            last_result = hook_result
            if hook_result.decision in ("abort", "await_approval"):
                return hook_result
            if hook_result.decision == "modify" and hook_result.modified_args:
                # Subsequent hooks see modified args via a fresh context.
                ctx = self._build_context(
                    composed=composed,
                    recorder=recorder,
                    tool_name=tool_name,
                    args=hook_result.modified_args,
                    iteration=iteration,
                )
        # No abort/await; if any hook returned modify, last_result carries
        # the final args. Otherwise None signals "no terminal decision".
        if last_result and last_result.decision == "modify":
            return last_result
        return None

    async def _run_post_hooks(
        self,
        *,
        composed: ComposedSystemPrompt,
        recorder: Optional[RunRecorder],
        tool_name: str,
        args: dict[str, Any],
        tool_result: dict[str, Any],
        iteration: int,
    ) -> Optional[HookResult]:
        if self.hooks is None:
            return None

        ctx = self._build_context(
            composed=composed,
            recorder=recorder,
            tool_name=tool_name,
            args=args,
            iteration=iteration,
        )

        for entry in self.hooks.get_post_hooks():
            hook_result = await self._safe_invoke_post(
                entry.name, entry.hook, ctx, tool_result
            )
            if hook_result is None:
                continue
            self._dispatch_side_effect(entry.name, hook_result)
            if hook_result.decision in ("abort", "await_approval"):
                return hook_result
            # modify/continue on PostToolUse have no semantic effect on
            # the already-appended tool result. Logged for observability
            # but not acted on (M2 may surface modified results to the
            # next iteration).
        return None

    async def _safe_invoke_pre(
        self,
        name: str,
        hook: PreToolUseHook,
        ctx: HookContext,
    ) -> Optional[HookResult]:
        try:
            return await hook(ctx)
        except Exception:  # noqa: BLE001 — hook failures must never break the run
            logger.exception("[hook:%s] PreToolUse raised; continuing run", name)
            return None

    async def _safe_invoke_post(
        self,
        name: str,
        hook: PostToolUseHook,
        ctx: HookContext,
        tool_result: dict[str, Any],
    ) -> Optional[HookResult]:
        try:
            return await hook(ctx, tool_result)
        except Exception:  # noqa: BLE001
            logger.exception("[hook:%s] PostToolUse raised; continuing run", name)
            return None

    def _build_context(
        self,
        *,
        composed: ComposedSystemPrompt,
        recorder: Optional[RunRecorder],
        tool_name: str,
        args: dict[str, Any],
        iteration: int,
    ) -> HookContext:
        # Recorder may be absent in test paths. Use safe defaults so the
        # hook signature stays stable.
        run_id = recorder.run_id if recorder and recorder.run_id else UUID(int=0)
        user_id = recorder.user_id if recorder else UUID(int=0)
        session_id = recorder.session_id if recorder else None

        prompt_tokens = recorder.prompt_tokens if recorder else 0
        completion_tokens = recorder.completion_tokens if recorder else 0
        cost_cents = self._compute_cost(recorder)

        return HookContext(
            run_id=run_id,
            agent_id=composed.agent_id,
            agent_slug=composed.agent_slug,
            user_id=user_id,
            session_id=session_id,
            tool_name=tool_name,
            tool_args=args,
            accumulated_prompt_tokens=prompt_tokens,
            accumulated_completion_tokens=completion_tokens,
            accumulated_cost_cents=cost_cents,
            iteration=iteration,
        )

    @staticmethod
    def _compute_cost(recorder: Optional[RunRecorder]) -> float:
        """Mirror RunRecorder._finish() cost formula for in-flight reads."""
        if recorder is None:
            return 0.0
        prompt_rate = getattr(recorder, "_prompt_rate", None)
        completion_rate = getattr(recorder, "_completion_rate", None)
        if prompt_rate is None or completion_rate is None:
            return 0.0
        return (
            recorder.prompt_tokens / 1000.0 * prompt_rate
            + recorder.completion_tokens / 1000.0 * completion_rate
        )

    @staticmethod
    def _dispatch_side_effect(name: str, result: HookResult) -> None:
        """Fire HookResult.side_effect — non-blocking.

        side_effect is a zero-arg callable. Hook owners build a closure
        that does whatever dispatch they want — typically
        ``start_workflow_routed("...", ...)`` (Celery was removed in
        PR-D7). AgentRunner just invokes it and continues. Dispatch
        failures (DBOS not enabled, signature malformed) are logged
        and swallowed — the run continues.
        """
        if result.side_effect is None:
            return
        try:
            result.side_effect()
        except Exception:  # noqa: BLE001
            logger.exception(
                "[hook:%s] side_effect dispatch failed; run continues", name
            )

    @staticmethod
    def _aborted_response(result: HookResult) -> dict[str, Any]:
        return {
            "content": "",
            "raw": None,
            "aborted": True,
            "abort_reason": result.abort_reason or "hook_aborted",
        }

    @staticmethod
    def _awaiting_approval_response(result: HookResult) -> dict[str, Any]:
        approval = result.approval_request
        return {
            "content": "",
            "raw": None,
            "awaiting_approval": True,
            "approval_reason": approval.reason if approval else "",
            "approval_payload": approval.payload if approval else {},
        }
