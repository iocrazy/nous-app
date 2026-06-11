"""Drive a single agent turn with tool-call resolution + hook chain.

Optional ``recorder`` (:class:`RunRecorder`) — when passed, this runner
refreshes its heartbeat between iterations, polls ``cancel_requested``,
forwards token usage, and records skill invocations. All telemetry
failures are swallowed inside RunRecorder so the agent run itself
never breaks on a dead telemetry path.

Optional ``hooks`` (:class:`HookRegistry`) — when passed, runs registered
PreToolUse / PostToolUse chains around each tool dispatch. See
``app.services.infra.hooks.__init__`` for the contract. With an empty registry
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
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

if TYPE_CHECKING:
    from app.agent_framework import AbortController

from app.agent_framework import ContextCompactor
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.run_recorder import RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService
from app.services.infra.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    PostToolUseHook,
    PreToolUseHook,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

# Module-level singleton — ContextCompactor is stateless. Reusing the
# same instance per turn avoids the GC churn of allocating a fresh
# object on the agent's hot path.
_DEFAULT_COMPACTOR = ContextCompactor()

# Tool names recognised by the runner. Anything else is silently ignored
# (forward-compat with future caller-provided tools).
# Q5: MCP-routed tools are matched by ``"." in name`` separately — they
# don't need to be listed here.
# S4-T6: ResourceFetch is a per-request caller-provided tool; its handler
# is injected onto runner.resource_fetch_handler before each turn.
SUPPORTED_TOOLS: frozenset[str] = frozenset({"Skill", "Delegate", "ResourceFetch"})


def _last_user_text(user_messages: list[dict]) -> str:
    """Last user-role message content as display text (P3 transcript).

    Multimodal content arrives as a list of blocks — summarise non-text
    blocks (image_url etc.) instead of dumping base64 into the event log.
    """
    for m in reversed(user_messages or []):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                btype = block.get("type") if isinstance(block, dict) else None
                if btype == "text":
                    parts.append(str(block.get("text") or ""))
                elif btype:
                    parts.append(f"[{btype}]")
            return " ".join(p for p in parts if p) or "[non-text content]"
        return str(content or "")
    return ""


def _is_mcp_tool_name(name: str, mcp_registry) -> bool:
    """Q5: check if a tool name maps to a registered MCP server.

    Returns True iff (a) name contains exactly one '.' separator,
    (b) the prefix matches one of the registry's registered server
    names. Returns False when registry is None or name shape mismatches.
    """
    if mcp_registry is None or not name or "." not in name:
        return False
    server_name, _, raw = name.partition(".")
    if not raw:
        return False
    return server_name in mcp_registry.server_names()


def _mcp_tools_to_openai_format(qualified_tools) -> list[dict]:
    """Q5: convert MCPOutboundRegistry.QualifiedTool[] → OpenAI tools[].

    The OpenAI function-calling spec is what every chat-completions
    adapter expects in ``composed.tools``:

        {
          "type": "function",
          "function": {
            "name": "notion.create_page",
            "description": "...",
            "parameters": { "type": "object", ... }   # JSONSchema
          }
        }

    MCP's ``inputSchema`` already matches the JSONSchema shape, so the
    adapter is a thin wrapper.
    """
    out = []
    for qt in qualified_tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": qt.qualified_name,
                    "description": qt.description or "",
                    "parameters": qt.input_schema or {"type": "object"},
                },
            }
        )
    return out


class AgentRunner:
    def __init__(
        self,
        adapter: Any,
        skill_tool: SkillToolService,
        *,
        hooks: Optional[HookRegistry] = None,
        delegate_tool: Optional[Any] = None,
        mcp_registry: Optional[Any] = None,
    ) -> None:
        self.adapter = adapter
        self.skill_tool = skill_tool
        self.hooks = hooks  # None = no hook chain (back-compat default)
        # Optional cross-agent dispatch tool. When None, ``Delegate`` calls
        # are answered with an explicit "tool not configured" so the LLM
        # gets useful feedback instead of silent skip behaviour.
        self.delegate_tool = delegate_tool
        # Q5: optional outbound MCP registry. When set, tools advertised
        # by registered MCP servers are injected into composed.tools at
        # turn-start, and tool_calls whose name matches a server prefix
        # are routed via mcp_registry.call(qualified_name, args). Server
        # names are sanitized — no '.' allowed — so the prefix split is
        # unambiguous (e.g. 'notion.create_page' → server 'notion').
        self.mcp_registry = mcp_registry
        # S4-T6: per-request ResourceFetch handler. Injected by
        # ai_library_chat_service when the turn includes resource_ref
        # attachments. None means no @-referenced resources for this turn —
        # calls to ResourceFetch return a clear error instead of crashing.
        self.resource_fetch_handler: Optional[Any] = None

    async def stream_turn(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
        *,
        recorder: Optional[RunRecorder] = None,
        abort: Optional["AbortController"] = None,
        auto_recorder: bool = True,
        user_id: Optional["UUID"] = None,
        session_id: Optional["UUID"] = None,
        trigger: str = "chat_stream",
    ):
        """Wave H (B) + Phase P (P1) + R4: incremental streaming with tool_calls.

        Yields StreamChunk instances as the model emits them. When the
        model emits tool_call deltas, we collect them, execute the tools
        on completion (between LLM iterations), and re-enter the stream
        loop with the tool results in messages.

        R4: when ``recorder`` is None and ``auto_recorder=True`` (default),
        an internal RunRecorder is constructed + context-managed for the
        duration of the stream. Caller need only pass ``user_id``
        (and optionally session_id). This guarantees telemetry
        (agent_runs row + status + duration + cost) is never silently
        dropped because the caller forgot the wrapper.

        Yields:
          - delta_text chunks during text generation
          - StreamChunk with tool_call_delta during tool emission
          - synthetic StreamChunk with delta_text describing each tool
            execution (so caller's UI can show "→ ran skill X")
          - final chunk with finish_reason on completion

        Falls back to a single buffered chunk when the adapter doesn't
        implement stream().

        Per-turn output budget + AbortController + loop_guard all apply
        same as run_turn().
        """
        from contextlib import AsyncExitStack

        from app.agent_framework._metrics_helper import inc_metric

        # R4: optionally wrap in RunRecorder when caller didn't supply one
        async with AsyncExitStack() as _stack:
            if recorder is None and auto_recorder and user_id is not None:
                recorder = await _stack.enter_async_context(
                    RunRecorder(
                        agent_id=composed.agent_id,
                        user_id=user_id,
                        trigger=trigger,
                        session_id=session_id,
                        model=composed.model,
                    )
                )
                inc_metric("stream_turn_auto_recorder")

            async for _chunk in self._stream_turn_inner(
                composed,
                user_messages,
                recorder=recorder,
                abort=abort,
            ):
                yield _chunk

    async def _stream_turn_inner(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
        *,
        recorder: Optional[RunRecorder],
        abort: Optional["AbortController"],
    ):
        """R4: extracted inner generator so stream_turn can wrap us in
        an optional RunRecorder context without nesting concerns."""
        from app.agent_framework import RunAborted, ToolCallLoopGuard
        from app.agent_framework._metrics_helper import inc_metric
        from app.services.ai.adapters.base import StreamChunk, StreamingNotSupported

        stream_method = getattr(self.adapter, "stream", None)
        if stream_method is None:
            resp = await self.adapter.call(composed, user_messages)
            msg = resp["choices"][0]["message"]
            yield StreamChunk(
                delta_text=msg.get("content") or "",
                finish_reason=resp["choices"][0].get("finish_reason") or "stop",
                usage=resp.get("usage"),
            )
            return

        inc_metric("streaming_started")

        # P1: per-run loop guard same as run_turn
        loop_guard = ToolCallLoopGuard(repeat_threshold=3, window=5)

        # G3: discover MCP tools once + augment composed.tools (mirrors
        # run_turn's logic). Failures isolated — discovery error skips
        # MCP for this turn but the stream proceeds.
        mcp_tool_names: set[str] = set()
        if self.mcp_registry is not None:
            try:
                qualified = await self.mcp_registry.all_tools()
                if qualified:
                    extra_tools = _mcp_tools_to_openai_format(qualified)
                    composed = composed.model_copy(
                        update={
                            "tools": list(composed.tools or []) + extra_tools,
                        }
                    )
                    mcp_tool_names = {qt.qualified_name for qt in qualified}
                    inc_metric("mcp_tools_injected", by=len(extra_tools))
            except Exception as exc:
                logger.warning(
                    f"[stream_turn] MCP tool discovery failed (non-fatal): {exc}"
                )

        messages = list(user_messages)
        iteration = 0
        MAX_STREAM_ITERATIONS = 10
        # mig 286: same wall-clock cap as run_turn (see comment there).
        import time as _time

        _deadline = (
            _time.monotonic() + composed.timeout_sec
            if getattr(composed, "timeout_sec", None)
            else None
        )

        # P3 transcript (mig 285): open the event stream with the user turn.
        # Streaming runs skip the final 'assistant' event — the chat layer
        # persists the full message itself; tool_call events below are the
        # part the Transcript adds over chat history.
        if recorder is not None and hasattr(recorder, "record_event"):
            await recorder.record_event(
                "user", {"content": _last_user_text(user_messages)}
            )

        while iteration < MAX_STREAM_ITERATIONS:
            iteration += 1
            if _deadline is not None and _time.monotonic() > _deadline:
                logger.warning(
                    f"[stream_turn] run timeout_sec={composed.timeout_sec} "
                    f"exceeded at iter={iteration}"
                )
                yield StreamChunk(
                    delta_text=(
                        f"\n\n[run exceeded the agent's timeout_sec "
                        f"({composed.timeout_sec}s)]"
                    ),
                    finish_reason="length",
                    usage={"warning": "timeout_sec_exceeded"},
                )
                return
            if abort is not None and abort.is_aborted():
                inc_metric("streaming_aborted_mid")
                raise RunAborted("user cancel between stream iterations")

            # Per-iteration tool_call accumulation. Provider sends each
            # tool_call as deltas across multiple chunks; we stitch them.
            tool_call_buf: dict[int, dict] = {}
            final_finish: Optional[str] = None
            final_usage: Optional[dict] = None

            try:
                async for chunk in stream_method(composed, messages):
                    if abort is not None and abort.is_aborted():
                        inc_metric("streaming_aborted_mid")
                        raise RunAborted("user cancel mid-stream")

                    # Forward text delta as-is to caller
                    if chunk.delta_text or chunk.tool_call_delta:
                        yield chunk

                    # Stitch tool_call deltas
                    if chunk.tool_call_delta:
                        _merge_tool_call_deltas(
                            tool_call_buf,
                            chunk.tool_call_delta.get("tool_calls") or [],
                        )

                    if chunk.finish_reason:
                        final_finish = chunk.finish_reason
                        final_usage = chunk.usage
                        if recorder is not None and chunk.usage:
                            from app.services.ai.runner.usage_cached import (
                                extract_cached_input_tokens,
                            )

                            recorder.record_usage(
                                prompt_tokens=int(
                                    chunk.usage.get("prompt_tokens") or 0
                                ),
                                completion_tokens=int(
                                    chunk.usage.get("completion_tokens") or 0
                                ),
                                cached_input_tokens=extract_cached_input_tokens(
                                    chunk.usage
                                ),
                            )
                        break
            except StreamingNotSupported:
                resp = await self.adapter.call(composed, messages)
                msg = resp["choices"][0]["message"]
                yield StreamChunk(
                    delta_text=msg.get("content") or "",
                    finish_reason=resp["choices"][0].get("finish_reason") or "stop",
                    usage=resp.get("usage"),
                )
                return

            # If finish_reason is 'tool_calls' (or we collected calls
            # despite a 'stop'), execute them + re-enter loop.
            tool_calls_to_run = list(tool_call_buf.values()) if tool_call_buf else []
            if not tool_calls_to_run:
                # No tool calls — turn complete. Always yield terminal
                # finish chunk (inner loop's finish chunk wasn't yielded
                # when it lacked delta_text/tool_call_delta).
                yield StreamChunk(
                    finish_reason=final_finish or "stop",
                    usage=final_usage,
                )
                return

            # Append assistant tool-use message
            assistant_msg = {
                "role": "assistant",
                "content": "",
                "tool_calls": tool_calls_to_run,
            }
            messages.append(assistant_msg)

            # Execute each tool, append tool reply, yield synthetic
            # delta describing each.
            import json as _json

            for call in tool_calls_to_run:
                fn = call.get("function") or {}
                tool_name = fn.get("name", "")
                # G3: accept MCP tools alongside built-in Skill / Delegate
                is_mcp = tool_name in mcp_tool_names or _is_mcp_tool_name(
                    tool_name,
                    self.mcp_registry,
                )
                if not is_mcp and tool_name not in SUPPORTED_TOOLS:
                    continue
                try:
                    args = _json.loads(fn.get("arguments") or "{}")
                except _json.JSONDecodeError:
                    args = {}

                # Yield synthetic UI hint
                hint_label = args.get("skill") or "" if tool_name == "Skill" else ""
                yield StreamChunk(
                    delta_text=f"\n\n→ Running {tool_name}({hint_label})...\n",
                )

                try:
                    args_repr = _json.dumps(args, sort_keys=True, ensure_ascii=False)
                except (TypeError, ValueError):
                    args_repr = repr(args)
                loop_guard.observe(tool_name, args_repr)
                inc_metric("loop_guard_observed")

                if tool_name == "Skill":
                    if recorder is not None and args.get("skill"):
                        recorder.record_skill(str(args["skill"]))
                    result = await self.skill_tool.execute(args)
                elif tool_name == "ResourceFetch":
                    # S4-T6: per-request handler injected by the chat service.
                    if self.resource_fetch_handler is None:
                        result = {
                            "error": (
                                "ResourceFetch is not available for this turn. "
                                "Include @resource references in your message "
                                "to make resources accessible."
                            )
                        }
                    else:
                        try:
                            result = await self.resource_fetch_handler(args)
                        except Exception as rf_exc:
                            logger.warning(
                                f"[AgentRunner] ResourceFetch handler raised: {rf_exc!r}"
                            )
                            result = {
                                "error": f"ResourceFetch failed: {rf_exc.__class__.__name__}"
                            }
                elif is_mcp:
                    # G3: route to outbound MCP server. Mirrors run_turn
                    # error handling — transport errors → tool result
                    # dict, not raise.
                    try:
                        result = await self.mcp_registry.call(tool_name, args)
                        inc_metric("mcp_tool_call")
                        if isinstance(result, dict) and result.get("isError"):
                            inc_metric("mcp_tool_call_error")
                    except Exception as exc:
                        inc_metric("mcp_tool_call_transport_error")
                        result = {
                            "error": f"MCP transport failure: {exc}",
                            "tool": tool_name,
                        }
                else:  # Delegate
                    if self.delegate_tool is None:
                        result = {"error": "Delegate tool not configured"}
                    else:
                        result = await self.delegate_tool.execute(args)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": tool_name,
                        "content": _json.dumps(result, ensure_ascii=False),
                    }
                )

                # P3 transcript (mig 285): mirror of run_turn's tool event.
                if recorder is not None and hasattr(recorder, "record_event"):
                    await recorder.record_event(
                        "tool_call",
                        {
                            "tool": tool_name,
                            "args": args,
                            "result": result,
                            "iteration": iteration,
                        },
                    )

                if loop_guard.is_looping():
                    warning = loop_guard.render_warning()
                    if warning:
                        messages.append({"role": "system", "content": warning})
                        inc_metric("loop_guard_tripped")
                        break  # Out of inner for; back to LLM with warning

            # Loop continues — next iteration calls stream_method again
            # with the updated messages

        # Hit MAX_STREAM_ITERATIONS — yield terminal chunk
        yield StreamChunk(
            finish_reason="length",
            usage={"warning": "max_stream_iterations_exceeded"},
        )

    async def run_turn(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
        *,
        recorder: Optional[RunRecorder] = None,
        abort: Optional["AbortController"] = None,
    ) -> dict[str, Any]:
        """Run one turn of the agent loop.

        ``recorder`` polls cancel BETWEEN iterations (cooperative).
        ``abort`` (Sprint 2 #3) interrupts the in-flight adapter.call —
        pressing the frontend cancel button takes effect within seconds,
        not after the LLM call finishes. Caller is responsible for
        creating the AbortController and the watcher coroutine that
        fires it.
        """
        # Pre-flight 1: tiered compaction (Phase 1 of issue #199).
        # Yellow tier prunes tool results in place; orange/red emergency-
        # caps message bodies (Phase 2 will swap that for an LLM head
        # summary). On green this is essentially free — identity return,
        # no token re-count. On any other tier we feed the COMPACTED list
        # into the budget check below so we don't reject a turn that
        # would have fit after pruning.
        user_messages, compaction_stats = await _DEFAULT_COMPACTOR.maybe_compact(
            system_message=composed.system_message,
            user_messages=user_messages,
            model=composed.model,
        )
        # hasattr instead of try/except AttributeError so a real bug
        # inside note_compaction (e.g., supabase write failing with
        # AttributeError on a None response) doesn't get swallowed —
        # Phase 5 wires the helper in; until then this branch is just
        # quiet.
        if (
            recorder is not None
            and compaction_stats.tokens_saved > 0
            and hasattr(recorder, "note_compaction")
        ):
            recorder.note_compaction(compaction_stats)

        # Phase 5 of #199: hand the per-turn recorder to the
        # SubAgentTaskService so any spawn() inside this turn can roll
        # its envelope counters up to metadata.subagents. Set lazily —
        # SkillToolService.subagent_task may be None if the chat layer
        # didn't install one for this run.
        if (
            recorder is not None
            and getattr(self.skill_tool, "subagent_task", None) is not None
        ):
            try:
                self.skill_tool.subagent_task.parent_recorder = recorder
            except Exception:  # noqa: BLE001 — telemetry side-effect
                pass

        # Pre-flight 2: context budget guard. A small-context model
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
        # mig 286 (paperclip P4): per-run wall-clock cap. Checked between
        # LLM iterations — bounds the tool loop; a single hung HTTP call is
        # bounded by the adapter's own client timeout.
        import time as _time

        _deadline = (
            _time.monotonic() + composed.timeout_sec
            if getattr(composed, "timeout_sec", None)
            else None
        )

        # P3 transcript (mig 285): open the event stream with the user turn.
        # Best-effort — record_event never raises.
        if recorder is not None and hasattr(recorder, "record_event"):
            await recorder.record_event(
                "user", {"content": _last_user_text(user_messages)}
            )

        # Wave G (G3): per-run loop guard. Detects "same (tool, args)
        # called >= N times in last M calls" and warns the LLM mid-run
        # rather than letting it burn the iteration budget on a stuck
        # repeat. Per-instance state — different runs are independent.
        from app.agent_framework import ToolCallLoopGuard, ToolResultCache

        loop_guard = ToolCallLoopGuard(repeat_threshold=3, window=5)
        loop_warning_already_injected = False
        # Phase L (L1): per-run tool result cache. Skill must opt in via
        # idempotent flag in skill_manifest entry. Each run gets its own
        # cache so stale data can't leak across users / sessions.
        tool_cache = ToolResultCache()
        # Build a quick lookup of which skill slugs are idempotent
        idempotent_slugs = {
            s.get("slug")
            for s in (composed.skill_manifest or [])
            if s.get("idempotent") is True and s.get("slug")
        }

        # Step A milestone: trace each Skill / Delegate dispatch made
        # during this turn. The chat service surfaces this list so the
        # frontend can render sub-task cards inline ("→ summarize, 24s,
        # ¢0.27") without needing a separate streaming channel. Order
        # mirrors LLM emission order. Result is best-effort serialised
        # (we strip non-JSON values so dataclasses / UUIDs don't poison
        # the response payload).
        tool_call_trace: list[dict[str, Any]] = []

        # Q5: discover MCP tools once per turn + augment composed.tools.
        # Failures isolated — if discovery breaks, the turn proceeds
        # without MCP tools (back-compat).
        mcp_tool_names: set[str] = set()
        if self.mcp_registry is not None:
            try:
                qualified = await self.mcp_registry.all_tools()
                if qualified:
                    extra_tools = _mcp_tools_to_openai_format(qualified)
                    composed = composed.model_copy(
                        update={
                            "tools": list(composed.tools or []) + extra_tools,
                        }
                    )
                    mcp_tool_names = {qt.qualified_name for qt in qualified}
                    from app.agent_framework._metrics_helper import inc_metric

                    inc_metric("mcp_tools_injected", by=len(extra_tools))
            except Exception as exc:
                logger.warning(
                    f"[AgentRunner] MCP tool discovery failed (non-fatal): {exc}"
                )

        for _ in range(MAX_TOOL_ITERATIONS):
            iteration += 1

            if _deadline is not None and _time.monotonic() > _deadline:
                logger.warning(
                    f"[AgentRunner] run timeout_sec={composed.timeout_sec} "
                    f"exceeded at iter={iteration}"
                )
                return {
                    "content": "",
                    "raw": None,
                    "error": (
                        f"run exceeded the agent's timeout_sec "
                        f"({composed.timeout_sec}s)"
                    ),
                    "error_code": "run_timeout",
                }

            if recorder is not None:
                await recorder.heartbeat()
                if await recorder.check_cancelled():
                    return {"content": "", "raw": None, "cancelled": True}

            # Wave G (G4): per-call output budget. Compute a max_tokens
            # cap based on remaining window. If smaller than what
            # composed declared, build a copy with the tighter cap so
            # the adapter doesn't request more than will fit.
            composed_for_call = composed
            try:
                from app.agent_framework import (
                    count_messages_tokens,
                    derive_output_budget,
                )

                consumed_input = count_messages_tokens(messages, composed.model)
                budget = derive_output_budget(
                    model=composed.model,
                    consumed_input_tokens=consumed_input,
                )
                if budget.max_tokens < composed.max_tokens:
                    composed_for_call = composed.model_copy(
                        update={"max_tokens": budget.max_tokens}
                    )
                    from app.agent_framework._metrics_helper import inc_metric

                    inc_metric("output_budget_tightened")
                    logger.debug(
                        f"[AgentRunner] output budget tightened: "
                        f"{composed.max_tokens} → {budget.max_tokens} "
                        f"(consumed_input={consumed_input}, model={composed.model})"
                    )
            except Exception as bg_exc:
                # Non-fatal — fall back to the agent's configured max_tokens.
                logger.debug(f"output budget derive failed (non-fatal): {bg_exc}")

            # Sprint 2 #3: race adapter.call against AbortController so
            # pressing cancel mid-LLM-call interrupts within seconds
            # instead of waiting for the full request to complete.
            if abort is not None:
                from app.agent_framework import RunAborted, race_until_abort

                try:
                    resp = await race_until_abort(
                        self.adapter.call(composed_for_call, messages),
                        abort,
                    )
                except RunAborted as exc:
                    logger.info(f"[AgentRunner] aborted mid-call: {exc}")
                    return {
                        "content": "",
                        "raw": None,
                        "cancelled": True,
                        "abort_reason": str(exc),
                    }
            else:
                resp = await self.adapter.call(composed_for_call, messages)

            if recorder is not None:
                usage = resp.get("usage") or {}
                from app.services.ai.runner.usage_cached import (
                    extract_cached_input_tokens,
                )

                recorder.record_usage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    cached_input_tokens=extract_cached_input_tokens(usage),
                )

            msg = resp["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # msg.get("content") can be None (e.g. Claude emits null
                # content on a pure-tool-use turn). The `or ""` guarantees
                # the contract — callers always receive a str.
                if recorder is not None and hasattr(recorder, "record_event"):
                    await recorder.record_event(
                        "assistant", {"content": msg.get("content") or ""}
                    )
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
                # Q5: MCP tools have a server-prefixed name (e.g.
                # 'notion.create_page'). Allow them in addition to the
                # built-in Skill / Delegate.
                is_mcp = tool_name in mcp_tool_names or _is_mcp_tool_name(
                    tool_name or "", self.mcp_registry
                )
                if not is_mcp and tool_name not in SUPPORTED_TOOLS:
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
                # Phase L (L1): cache check — only for idempotent skills.
                cache_key: Optional[str] = None
                cached_result: Optional[dict] = None
                if tool_name == "Skill":
                    skill_slug = args.get("skill")
                    if skill_slug and skill_slug in idempotent_slugs:
                        cache_key = ToolResultCache.key(tool_name, args)
                        cached_result = tool_cache.get(cache_key)

                if cached_result is not None:
                    result = cached_result
                    from app.agent_framework._metrics_helper import inc_metric

                    inc_metric("tool_cache_hit")
                elif tool_name == "Skill":
                    if recorder is not None and args.get("skill"):
                        recorder.record_skill(str(args["skill"]))
                    result = await self.skill_tool.execute(args)
                    # Cache result if this skill is idempotent
                    if (
                        cache_key is not None
                        and isinstance(result, dict)
                        and not result.get("error")
                    ):
                        tool_cache.put(cache_key, result)
                elif tool_name == "ResourceFetch":
                    # S4-T6: route to per-request handler injected by the chat
                    # service. None means no @-referenced resources for this turn.
                    if self.resource_fetch_handler is None:
                        result = {
                            "error": (
                                "ResourceFetch is not available for this turn. "
                                "Include @resource references in your message "
                                "to make resources accessible."
                            )
                        }
                    else:
                        try:
                            result = await self.resource_fetch_handler(args)
                        except Exception as rf_exc:
                            logger.warning(
                                f"[AgentRunner] ResourceFetch handler raised: {rf_exc!r}"
                            )
                            result = {
                                "error": f"ResourceFetch failed: {rf_exc.__class__.__name__}"
                            }
                elif is_mcp:
                    # Q5: route to outbound MCP server. Tool errors
                    # (server returned isError=true) come back as a
                    # normal result dict with error info. Transport
                    # failures (network down, 4xx) → MCPClientError
                    # which we catch and convert to a result dict so
                    # the LLM gets feedback instead of crashing the run.
                    from app.agent_framework._metrics_helper import inc_metric

                    try:
                        result = await self.mcp_registry.call(tool_name, args)
                        inc_metric("mcp_tool_call")
                        if result.get("isError"):
                            inc_metric("mcp_tool_call_error")
                    except Exception as exc:
                        inc_metric("mcp_tool_call_transport_error")
                        result = {
                            "error": f"MCP transport failure: {exc}",
                            "tool": tool_name,
                        }
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

                # P3 transcript (mig 285): one event per executed tool call
                # (args + result in one payload — the Nice renderer shows it
                # as a folded card). record_event truncates long values.
                if recorder is not None and hasattr(recorder, "record_event"):
                    await recorder.record_event(
                        "tool_call",
                        {
                            "tool": tool_name,
                            "args": args,
                            "result": result,
                            "iteration": iteration,
                        },
                    )

                # Wave G (G3): observe for loop detection. Args
                # canonicalized to a stable string (sorted keys).
                try:
                    args_repr = json.dumps(args, sort_keys=True, ensure_ascii=False)
                except (TypeError, ValueError):
                    args_repr = repr(args)
                loop_guard.observe(tool_name, args_repr)
                # J1 telemetry
                from app.agent_framework._metrics_helper import inc_metric

                inc_metric("loop_guard_observed")

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

                # Wave G (G3): if the guard says we're looping, inject
                # ONE system warning into messages. Subsequent iterations
                # don't re-inject (avoid repeated warnings polluting the
                # context). LLM must self-correct on next turn.
                if not loop_warning_already_injected and loop_guard.is_looping():
                    warning = loop_guard.render_warning()
                    if warning:
                        messages.append({"role": "system", "content": warning})
                        loop_warning_already_injected = True
                        inc_metric("loop_guard_tripped")
                        logger.warning(
                            f"[AgentRunner] loop_guard tripped at iter={iteration}"
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
        # run_id (agent_runs.id) is a BIGINT Snowflake string post mig 232; the
        # test-path placeholder is the string "0" (cost_auditor skips it).
        run_id = recorder.run_id if recorder and recorder.run_id else "0"
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


# ─── Phase P (P1) helpers ─────────────────────────────────────────────


def _merge_tool_call_deltas(buf: dict, deltas: list[dict]) -> None:
    """Merge OpenAI-style tool_call deltas into ``buf`` keyed by index.

    Each delta carries ``index`` (which tool slot) + partial ``id`` /
    ``function.name`` / ``function.arguments`` (a string fragment).
    Stitch arguments by appending; latch id + name on first delta.
    """
    for d in deltas or []:
        idx = d.get("index", 0)
        slot = buf.setdefault(
            idx,
            {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        if d.get("id"):
            slot["id"] = d["id"]
        fn_delta = d.get("function") or {}
        if fn_delta.get("name"):
            slot["function"]["name"] = fn_delta["name"]
        if fn_delta.get("arguments"):
            slot["function"]["arguments"] += fn_delta["arguments"]
