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
from app.services.ai.llm.empty_response import diagnose_empty_response
from app.services.ai.runner.events import emit as emit_event
from app.services.ai.runner.reasoning import (
    ReasoningStreamFilter,
    model_uses_reasoning,
    strip_reasoning,
)
from app.services.ai.runner.run_recorder import RunRecorder
from app.services.ai.runner.step_hooks import (
    StepContext,
    StepDecision,
    StepHookChain,
    default_step_hooks,
)
from app.services.ai.skills.skill_tool_service import SkillToolService
from app.services.ai.tools.ask_user_tool import (
    ASK_USER_TOOL_NAME,
    ask_user_handler,
)
from app.services.ai.tools.schedule_wakeup_tool import SCHEDULE_WAKEUP_TOOL_NAME
from app.services.infra.hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    PostToolUseHook,
    PreToolUseHook,
)

logger = logging.getLogger(__name__)

# Audit #15: single tool-call loop ceiling shared by run_turn (buffered) and
# stream_turn (SSE). Previously run_turn capped at 5 while stream_turn capped at
# 10 → the same agent/task truncated when buffered but completed when streamed.
# Unified to 10; the per-run wall-clock deadline (timeout_sec) is the real bound.
MAX_TOOL_ITERATIONS = 10

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
# FinishIssue (Spec-2) is, like ResourceFetch, a per-request caller-provided
# tool: its handler is injected onto runner.finish_issue_handler only for
# issue-context turns, and its spec is only added to composed.tools there, so
# regular chat turns never see or accept it.
# A4 screenwriting tools (spec §5.1). Unlike ResourceFetch / GenerateImage /
# FinishIssue there is no injected handler to configure: the handlers are
# stateless and derive everything they need from the run context, so they are
# dispatched by direct import below. That is deliberate — an injected handler
# means every dispatch path must remember to inject it, and a path that
# forgets gets a tool that is advertised (the spec comes from the composer,
# which every path shares) but inert. Authorization does NOT depend on this:
# the A1 capability gate runs in the PreToolUse chain and the A2 resolver runs
# inside each handler.
#
# GenerateShotImage (A6) joins this set even though it is graded on a
# different capability axis (media.image, not write_level — see
# high_risk_capability_gate.py's TOOL_REQUIREMENTS): it still needs a shot id
# resolved through THIS run's scope before it may dispatch the
# script_shot_generate DBOS workflow, and that resolve-then-act shape is
# exactly what _dispatch_screenwriting already provides (gate-presence check,
# scope-for-run, resolver, never-raise-into-the-loop). A separate dispatch
# branch (like GenerateImage/GenerateVideo's injected-handler pattern) would
# have to re-implement all of that for one tool.
SCREENWRITING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "ListScenes",
        "ReadScene",
        "CreateShot",
        "UpdateShot",
        "ProposeEdit",
        "ApplyEdit",
        "GenerateShotImage",
    }
)

SUPPORTED_TOOLS: frozenset[str] = frozenset(
    {
        "Skill",
        "Delegate",
        "ResourceFetch",
        "FinishIssue",
        "GenerateImage",
        "GenerateVideo",
        *SCREENWRITING_TOOL_NAMES,
        ASK_USER_TOOL_NAME,  # phase 2a: ask the human, park the turn
        # phase 2b-2: arm a one-time wake-up on this issue (issue root runs
        # only; the handler is injected per turn, like FinishIssue).
        SCHEDULE_WAKEUP_TOOL_NAME,
    }
)


_IMG_PROMOTED_NOTE = (
    "[image content delivered as an image part in the following user message]"
)
_IMG_OMITTED_NOTE = "[image omitted: the current model has no vision capability]"


def _image_blocks(result: Any) -> list[dict]:
    """Usable image_url blocks (data:/http(s) url) inside a tool result's
    ``content`` list. Anything else — error dicts, text content, relative
    URLs no provider could fetch — yields []."""
    if not isinstance(result, dict) or result.get("error"):
        return []
    content = result.get("content")
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image_url":
            continue
        url = str(block.get("url") or "")
        if url.startswith(("data:", "http://", "https://")):
            out.append(block)
    return out


def _strip_image_urls(result: dict, note: str) -> dict:
    """Copy of ``result`` with image_url block urls replaced by ``note`` —
    for the tool message, the tool_call trace and the transcript event, so
    multi-MB base64 never rides anywhere except the promoted image part."""
    content = [
        (
            {**b, "url": note}
            if isinstance(b, dict) and b.get("type") == "image_url"
            else b
        )
        for b in result.get("content") or []
    ]
    return {**result, "content": content}


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


def _user_event_payload(
    composed: ComposedSystemPrompt, user_messages: list[dict]
) -> dict:
    """The turn-opening ``user`` event (三期 3a T8c 缺陷 4).

    ``referenced_outputs`` is written ONLY when this turn cites something. A
    turn without citations must produce byte-for-byte the payload it always
    did: old runs and uncited new ones then render identically, and no reader
    grows a branch for "the key is there but empty".
    """
    payload: dict = {"content": _last_user_text(user_messages)}
    cited = getattr(composed, "referenced_outputs", None)
    if cited:
        payload["referenced_outputs"] = list(cited)
    return payload


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
        parent_run_id: Optional[str] = None,
        agent_depth: int = 0,
        delegation_chain: tuple[str, ...] = (),
        step_hooks: Optional[StepHookChain] = None,
    ) -> None:
        self.adapter = adapter
        self.skill_tool = skill_tool
        self.hooks = hooks  # None = no hook chain (back-compat default)
        # Seam A (harness p4): the per-step cross-cutting chain. None →
        # exactly the behaviour the old inline blocks had (heartbeat, cancel).
        self.step_hooks: StepHookChain = step_hooks or default_step_hooks()
        # M2 multi-agent scope, threaded into every HookContext. Defaults
        # describe a top-of-tree turn; sub-agent / workforce wiring passes
        # the inherited values (see build_agent_runner_stack).
        self.parent_run_id = parent_run_id
        self.agent_depth = agent_depth
        self.delegation_chain = delegation_chain
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
        # Whether the turn's model accepts image parts. Set by the chat
        # service (model_supports_vision) each turn; gates the promotion of
        # image-bearing tool results into user-message image parts. Default
        # False = old text-only behaviour.
        self.vision_capable: bool = False
        # Spec-2: per-request FinishIssue handler. Injected by the chat service
        # only for issue-context turns; None on regular chat turns so a stray
        # FinishIssue call returns a clear "not available" result.
        self.finish_issue_handler: Optional[Any] = None
        # genmedia: per-request media generation handlers. Injected by the
        # media generation service when the agent turn is allowed to generate
        # images or video. None means generation is not configured for this
        # turn — calls return a clear error instead of crashing.
        self.generate_image_handler: Optional[Any] = None
        self.generate_video_handler: Optional[Any] = None
        # phase 2b-2 §3: per-request ScheduleWakeup handler, injected by the
        # chat service on an issue ROOT run only. None everywhere else, so a
        # stray call gets a clear "not available" result rather than arming a
        # wake-up on a conversation the caller does not own.
        self.schedule_wakeup_handler: Optional[Any] = None
        # Task 5 (Agent 权限页梳理立项, 2026-08-10): tool names already
        # reported via a "capability_denied" transcript event THIS turn.
        # One AgentRunner instance == one turn (see build_agent_runner_stack
        # "HookRegistry per-turn"), so __init__ is the correct reset point —
        # no separate per-turn re-init needed in run_turn/stream_turn.
        self._denied_tools_this_turn: set[str] = set()

    @staticmethod
    def _record_buffered_usage(
        recorder: Optional[RunRecorder], usage: Optional[dict]
    ) -> None:
        """A1 (needs_input first-class, Task 5): stream_turn's two buffered
        fallbacks — no ``adapter.stream()`` at all, or a mid-loop
        ``StreamingNotSupported`` — call ``adapter.call()`` directly and used
        to hand the provider's usage straight to the caller via the yielded
        ``StreamChunk`` without ever telling the recorder. Any trigger that
        drives a turn through stream_turn (chat's SSE path AND
        issue_agent_executor both always pass a chunk_callback, so both
        always land here) then persisted 0/NULL tokens+cost whenever the
        bound model's adapter can't really stream — e.g. ClaudeAdapter has
        no ``.stream()`` method. Mirrors run_turn's post-``adapter.call``
        ``record_usage`` call so both code paths feed the same accumulator;
        cost_cents is still computed once, in RunRecorder.compute_cost_cents/
        _finish — nothing here duplicates pricing."""
        if recorder is None or not usage:
            return
        from app.services.ai.runner.usage_cached import extract_cached_input_tokens

        recorder.record_usage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_input_tokens=extract_cached_input_tokens(usage),
        )

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
        # A4: scope for the AUTO-created recorder only (ignored when the
        # caller supplies its own ``recorder`` — that one is already bound by
        # whoever built it). Server-derived values only; never pass anything
        # that came out of a tool argument or model output.
        project_id: Optional[int] = None,
        episode_id: Optional[int] = None,
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
                # A4: this recorder used to stamp NEITHER scope column, so a
                # run created here could never use the screenwriting tools.
                # Two server-side sources, in order: the explicit kwargs a
                # caller that knows its project passes, and — failing that —
                # this runner's own parent run (set when the stack was built
                # for a delegated/sub-agent turn), inherited verbatim so a
                # child is never wider than its parent. Neither available ⇒
                # unbound, and the tools say so.
                from app.services.ai.scope.scope_binding import (
                    resolve_dispatch_scope,
                )

                _scope = await resolve_dispatch_scope(
                    project_id=project_id,
                    episode_id=episode_id,
                    parent_run_id=self.parent_run_id,
                )
                recorder = await _stack.enter_async_context(
                    RunRecorder(
                        agent_id=composed.agent_id,
                        user_id=user_id,
                        trigger=trigger,
                        session_id=session_id,
                        model=composed.model,
                        **_scope.as_recorder_kwargs(),
                    )
                )
                inc_metric("stream_turn_auto_recorder")

            # Phase-2 (443): hand the skill tool this run's recorder for the
            # duration of the turn so Skill(todo) snapshots file under THIS
            # run; detach after (a tool outliving the turn would otherwise
            # file the next run's todos here). Mirrors run_turn.
            _todo_slot = recorder is not None and hasattr(self.skill_tool, "recorder")
            if _todo_slot:
                self.skill_tool.recorder = recorder
            # Phase 2: one typed ``turn_end`` per turn. The last chunk that
            # carried a finish_reason is the outcome; none at all means a
            # cooperative cancel returned silently. GeneratorExit (consumer
            # went away) cannot await, so that path records nothing — the
            # run's own status already says cancelled.
            from app.services.ai.runner.turn_end import (
                classify_exception,
                classify_stream_end,
                emit_turn_end,
            )

            _last_terminal = None
            try:
                async for _chunk in self._stream_turn_inner(
                    composed,
                    user_messages,
                    recorder=recorder,
                    abort=abort,
                ):
                    if getattr(_chunk, "finish_reason", None):
                        _last_terminal = _chunk
                    yield _chunk
            except GeneratorExit:
                raise
            except BaseException as exc:
                reason, extra = classify_exception(exc)
                await emit_turn_end(recorder, reason, extra)
                raise
            else:
                reason, extra = classify_stream_end(_last_terminal)
                await emit_turn_end(recorder, reason, extra)
            finally:
                if _todo_slot:
                    self.skill_tool.recorder = None

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

        # Pre-flight: shared compaction + context-budget guard (mirrors
        # run_turn). The streaming path — the primary ChatPanel route —
        # previously skipped both, so a long conversation could overflow the
        # model window mid-stream and surface a cryptic provider error. On
        # budget rejection, emit one clean terminal chunk and stop.
        user_messages, preflight_err = await self._preflight_compact_and_budget(
            composed, user_messages, recorder
        )
        if preflight_err is not None:
            yield StreamChunk(
                delta_text=(
                    "This conversation has grown too long for the selected "
                    "model's context window. Start a new session or switch to "
                    "a larger-context model to continue."
                ),
                finish_reason="length",
                usage={"error_code": preflight_err.get("error_code")},
            )
            return

        self._bind_turn_recorder(recorder)

        stream_method = getattr(self.adapter, "stream", None)
        if stream_method is None:
            # Production ALWAYS lands here for chunk_callback turns: the chat
            # wiring hands stream_turn an LLMFallbackChain, which has no
            # ``stream``. This branch used to do one bare adapter.call and
            # forward ONLY message content — a model that answered with
            # tool_calls (FinishIssue, Skill, …) had the whole turn silently
            # swallowed: no tool execution, no tool_call_trace, empty text
            # (the "doubao-lite 空产出" / "FinishIssue 从未被调用" signature,
            # root-caused 2026-08-03). Delegate to run_turn so the buffered
            # path shares its full tool loop; run_turn records usage on the
            # recorder itself (no _record_buffered_usage — that would
            # double-count).
            result = await self.run_turn(
                composed,
                user_messages,
                recorder=recorder,
                abort=abort,
                _emit_turn_end=False,  # stream_turn classifies this turn
            )
            if result.get("cancelled"):
                return
            raw = result.get("raw") or {}
            raw_choices = raw.get("choices") or [{}]
            usage = raw.get("usage")
            # Phase 2: keep the error marker on the terminal chunk so the
            # typed turn_end does not read a loop-ceiling/timeout as "stop".
            _err_code = result.get("error_code") or (
                result.get("error") if "error" in result else None
            )
            if _err_code:
                usage = {**(usage or {}), "error_code": str(_err_code)[:80]}
            # Phase 2a: a typed stop (paused / awaiting_input) is carried by
            # run_turn's ``stop_reason`` — the terminal chunk's ``usage`` is
            # the ONLY thing the chat service and classify_stream_end read,
            # so forward it or the turn is filed as ``completed`` and the
            # parked question / pause is lost (real-stack finding, Task 9).
            _stop = result.get("stop_reason")
            if _stop:
                usage = {**(usage or {}), "stop_reason": str(_stop)}
            # Same for a hook decision: the true-stream path files
            # ``usage.hook_decision`` plus a bracket line; run_turn hands
            # back ``aborted`` / ``awaiting_approval`` flags with empty
            # content. Mirror the stream shape so classify_stream_end and
            # the chat service's approval-row persistence see the same thing
            # on both routes.
            _text = result.get("content") or ""
            if result.get("aborted"):
                _reason = result.get("abort_reason") or "hook_aborted"
                usage = {**(usage or {}), "hook_decision": "abort"}
                _text = _text or f"\n\n[blocked: {_reason}]"
            elif result.get("awaiting_approval"):
                _reason = result.get("approval_reason") or "approval required"
                usage = {
                    **(usage or {}),
                    "hook_decision": "await_approval",
                    "approval_reason": str(_reason),
                }
                _text = _text or f"\n\n[awaiting approval: {_reason}]"
            yield StreamChunk(
                delta_text=_text,
                finish_reason=(raw_choices[0].get("finish_reason") or "stop"),
                usage=usage,
                tool_call_trace=result.get("tool_calls") or [],
            )
            return

        inc_metric("streaming_started")

        # P1: per-run loop guard same as run_turn
        loop_guard = ToolCallLoopGuard(repeat_threshold=3, window=5)

        # Bugfix (was silently dropped): trace of every tool executed this
        # turn, mirroring run_turn's ``tool_call_trace`` exactly (same keys
        # — "name" / "args" / "result" / "iteration" — so callers like
        # extract_issue_outcome() work identically regardless of which path
        # ran). Surfaced to the caller via the terminal StreamChunk's
        # ``tool_call_trace`` field (see StreamChunk in adapters/base.py).
        tool_call_trace: list[dict[str, Any]] = []

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
        # Audit #15: share the unified module ceiling instead of a divergent
        # local literal (was 10 here vs 5 in run_turn).
        MAX_STREAM_ITERATIONS = MAX_TOOL_ITERATIONS
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
        await emit_event(recorder, "user", _user_event_payload(composed, user_messages))

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
                    tool_call_trace=tool_call_trace,
                )
                return
            if abort is not None and abort.is_aborted():
                inc_metric("streaming_aborted_mid")
                raise RunAborted("user cancel between stream iterations")

            # Heartbeat + cooperative cancel between iterations (mirrors
            # run_turn). Without this a long multi-iteration stream never
            # refreshes heartbeat_at, so the sweeper can wrongly mark a healthy
            # run heartbeat_lost; and a DB-side cancel would be ignored.
            _step_ctx = StepContext(
                turn=1,
                step=iteration,
                recorder=recorder,
                composed=composed,
                messages=messages,
                parent_run_id=self.parent_run_id,
                is_stream=True,
            )
            if await self.step_hooks.run(_step_ctx) is StepDecision.STOP:
                inc_metric("streaming_cancelled_cooperative")
                # A terminal chunk carrying the stop reason (mirrors the
                # hook_decision terminal chunks below) so classify_stream_end
                # files paused / awaiting_input as themselves, not as cancel.
                yield StreamChunk(
                    finish_reason="stop",
                    usage={"stop_reason": _step_ctx.stop_reason},
                    tool_call_trace=tool_call_trace,
                )
                return
            if _step_ctx.injected:
                messages.extend(_step_ctx.injected)

            # Per-iteration tool_call accumulation. Provider sends each
            # tool_call as deltas across multiple chunks; we stitch them.
            tool_call_buf: dict[int, dict] = {}
            final_finish: Optional[str] = None
            final_usage: Optional[dict] = None

            # Suppress a Qwen3 <think>…</think> block from the streamed text
            # (gated on the model so non-thinking models keep streaming live).
            # tool_call deltas are forwarded untouched.
            reason_filter = ReasoningStreamFilter(
                enabled=model_uses_reasoning(getattr(composed, "model", ""))
            )
            _t0 = await self._step_started(
                recorder, composed, iteration, is_stream=True
            )
            try:
                async for chunk in stream_method(composed, messages):
                    if abort is not None and abort.is_aborted():
                        inc_metric("streaming_aborted_mid")
                        raise RunAborted("user cancel mid-stream")

                    # Forward filtered text delta + tool_call deltas to caller.
                    emit_text = reason_filter.feed(chunk.delta_text)
                    if emit_text or chunk.tool_call_delta:
                        yield StreamChunk(
                            delta_text=emit_text,
                            tool_call_delta=chunk.tool_call_delta,
                            finish_reason=chunk.finish_reason,
                            usage=chunk.usage,
                        )

                    # Stitch tool_call deltas
                    if chunk.tool_call_delta:
                        _merge_tool_call_deltas(
                            tool_call_buf,
                            chunk.tool_call_delta.get("tool_calls") or [],
                        )

                    if chunk.finish_reason:
                        # Surface any buffered (un-closed/truncated) thinking so
                        # a max_tokens truncation isn't a silent blank reply.
                        tail = reason_filter.flush()
                        if tail:
                            yield StreamChunk(delta_text=tail)
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
                await self._step_ended(
                    recorder, composed, iteration, _t0, final_usage, final_finish
                )
            except StreamingNotSupported:
                resp = await self.adapter.call(composed, messages)
                msg = resp["choices"][0]["message"]
                self._record_buffered_usage(recorder, resp.get("usage"))
                yield StreamChunk(
                    delta_text=strip_reasoning(msg.get("content") or ""),
                    finish_reason=resp["choices"][0].get("finish_reason") or "stop",
                    usage=resp.get("usage"),
                    tool_call_trace=tool_call_trace,
                )
                return

            # If finish_reason is 'tool_calls' (or we collected calls
            # despite a 'stop'), execute them + re-enter loop.
            tool_calls_to_run = list(tool_call_buf.values()) if tool_call_buf else []
            if not tool_calls_to_run:
                # No tool calls — turn complete. Always yield terminal
                # finish chunk (inner loop's finish chunk wasn't yielded
                # when it lacked delta_text/tool_call_delta).
                # Bugfix: carry the accumulated tool_call_trace on this
                # terminal chunk — this is the chunk callers see after a
                # FinishIssue call (the model declares, then answers with
                # no further tool calls), so this is the seam that matters
                # most for issue lifecycle routing.
                yield StreamChunk(
                    finish_reason=final_finish or "stop",
                    usage=final_usage,
                    tool_call_trace=tool_call_trace,
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
                    # Never skip silently. An assistant tool_call left without a
                    # tool result is an orphan the NEXT request is rejected for
                    # (OpenAI: every tool_call_id must be answered; Anthropic:
                    # orphaned tool_use). Synthesize an error result so the log
                    # stays replay-legal AND the model learns the tool does not
                    # exist instead of calling it again (dsh tools rule: a call
                    # that never started still gets a paired result).
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "name": tool_name,
                            "content": _json.dumps(
                                {
                                    "error": f"unknown tool: {tool_name}",
                                    "synthetic": True,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue
                try:
                    args = _json.loads(fn.get("arguments") or "{}")
                except _json.JSONDecodeError:
                    args = {}

                # ── PreToolUse chain (mirrors run_turn) ─────────────────────
                # The streaming path previously ran NO hooks, so CapabilityGate
                # / BudgetGuard / RateLimit were silently bypassed on the main
                # ChatPanel route. _run_pre_hooks also fires registered
                # side-effects + honours fail_closed security gates.
                pre_result = await self._run_pre_hooks(
                    composed=composed,
                    recorder=recorder,
                    tool_name=tool_name,
                    args=args,
                    iteration=iteration,
                )
                if pre_result is not None:
                    if pre_result.decision == "abort":
                        yield StreamChunk(
                            delta_text=(
                                f"\n\n[blocked: "
                                f"{pre_result.abort_reason or tool_name}]"
                            ),
                            finish_reason="stop",
                            usage={"hook_decision": "abort"},
                            tool_call_trace=tool_call_trace,
                        )
                        return
                    if pre_result.decision == "await_approval":
                        _req = pre_result.approval_request
                        yield StreamChunk(
                            delta_text=(
                                "\n\n[awaiting approval: "
                                f"{_req.reason if _req else 'approval required'}]"
                            ),
                            finish_reason="stop",
                            usage={"hook_decision": "await_approval"},
                            tool_call_trace=tool_call_trace,
                        )
                        return
                    if pre_result.decision == "modify" and pre_result.modified_args:
                        args = pre_result.modified_args

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
                    result = await self._timed(tool_name, self.skill_tool.execute(args))
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
                            result = await self._timed(
                                tool_name, self.resource_fetch_handler(args)
                            )
                        except Exception as rf_exc:
                            logger.warning(
                                f"[AgentRunner] ResourceFetch handler raised: {rf_exc!r}"
                            )
                            result = {
                                "error": f"ResourceFetch failed: {rf_exc.__class__.__name__}"
                            }
                elif tool_name == ASK_USER_TOOL_NAME:
                    result = await self._timed(
                        tool_name,
                        self._dispatch_ask_user(args, recorder, iteration, composed),
                    )
                elif tool_name == "FinishIssue":
                    result = await self._timed(
                        tool_name, self._dispatch_finish_issue(args)
                    )
                elif tool_name == SCHEDULE_WAKEUP_TOOL_NAME:
                    result = await self._timed(
                        tool_name,
                        self._dispatch_schedule_wakeup(args, recorder),
                    )
                elif tool_name == "GenerateImage":
                    if self.generate_image_handler is None:
                        result = {
                            "ok": False,
                            "error": "GenerateImage not configured",
                        }
                    else:
                        result = await self._timed(
                            tool_name,
                            self.generate_image_handler(
                                args,
                                self._media_run_context(
                                    recorder, composed, step=iteration
                                ),
                            ),
                        )
                elif tool_name == "GenerateVideo":
                    if self.generate_video_handler is None:
                        result = {
                            "ok": False,
                            "error": "GenerateVideo not configured",
                        }
                    else:
                        result = await self._timed(
                            tool_name,
                            self.generate_video_handler(
                                args,
                                self._media_run_context(
                                    recorder, composed, step=iteration
                                ),
                            ),
                        )
                elif tool_name in SCREENWRITING_TOOL_NAMES:
                    result = await self._timed(
                        tool_name,
                        self._dispatch_screenwriting(
                            tool_name, args, recorder, composed, step=iteration
                        ),
                    )
                elif is_mcp:
                    # G3: route to outbound MCP server. Mirrors run_turn
                    # error handling — transport errors → tool result
                    # dict, not raise.
                    try:
                        result = await self._timed(
                            tool_name, self.mcp_registry.call(tool_name, args)
                        )
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
                        result = await self._timed(
                            tool_name, self.delegate_tool.execute(args)
                        )

                # Image promotion: vision models only see images in user
                # messages — lift image blocks out of the tool result and
                # strip the base64 from everything persisted (tool msg,
                # trace, transcript event).
                promoted_parts: list[dict] = []
                if tool_name == "ResourceFetch":
                    img_blocks = _image_blocks(result)
                    if img_blocks:
                        if self.vision_capable:
                            promoted_parts = [
                                {"type": "image_url", "image_url": {"url": b["url"]}}
                                for b in img_blocks
                            ]
                            result = _strip_image_urls(result, _IMG_PROMOTED_NOTE)
                        else:
                            result = _strip_image_urls(result, _IMG_OMITTED_NOTE)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": tool_name,
                        "content": _json.dumps(result, ensure_ascii=False),
                    }
                )
                if promoted_parts:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "[image content from ResourceFetch]",
                                },
                                *promoted_parts,
                            ],
                        }
                    )

                # Bugfix: trace this dispatch — same shape as run_turn's
                # tool_call_trace (see extract_issue_outcome, which reads
                # call["name"] / call["result"]["outcome"]). Previously
                # stream_turn built no trace at all, so every FinishIssue
                # declaration made on the streaming path (100% of issue
                # turns — run_issue_agent always streams) vanished and
                # issue_lifecycle silently fell back to its in_review
                # default.
                tool_call_trace.append(
                    {
                        "name": tool_name,
                        "args": args,
                        "result": result,
                        "iteration": iteration,
                    }
                )

                # P3 transcript (mig 285): mirror of run_turn's tool event.
                await emit_event(
                    recorder,
                    "tool_call",
                    {
                        "tool": tool_name,
                        "args": args,
                        "result": result,
                        "iteration": iteration,
                    },
                )

                # ── PostToolUse chain (mirrors run_turn) ────────────────────
                # Fires CostAuditor + MemoryHarvester side-effects, which the
                # streaming path previously skipped entirely.
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
                        yield StreamChunk(
                            delta_text=(
                                f"\n\n[blocked post-tool: "
                                f"{post_result.abort_reason or tool_name}]"
                            ),
                            finish_reason="stop",
                            usage={"hook_decision": "abort"},
                            tool_call_trace=tool_call_trace,
                        )
                        return
                    if post_result.decision == "await_approval":
                        _req = post_result.approval_request
                        yield StreamChunk(
                            delta_text=(
                                "\n\n[awaiting approval: "
                                f"{_req.reason if _req else 'approval required'}]"
                            ),
                            finish_reason="stop",
                            usage={"hook_decision": "await_approval"},
                            tool_call_trace=tool_call_trace,
                        )
                        return

                if tool_name == ASK_USER_TOOL_NAME and result.get("asked"):
                    # Park: the human has to answer before anything else
                    # happens. Typed terminal chunk → turn_end{awaiting_input}.
                    await self._skip_parked_siblings(
                        tool_calls_to_run,
                        call,
                        tool_call_trace,
                        recorder,
                        iteration,
                        messages,
                    )
                    yield StreamChunk(
                        delta_text=self._awaiting_input_bracket(recorder),
                        finish_reason="stop",
                        usage={"stop_reason": "awaiting_input"},
                        tool_call_trace=tool_call_trace,
                    )
                    return

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
            tool_call_trace=tool_call_trace,
        )

    async def _step_started(
        self, recorder, composed, step: int, *, is_stream: bool
    ) -> float:
        """step_start bracket (mig 453): one per LLM call, both paths.
        Returns the monotonic start so ``_step_ended`` can stamp duration."""
        import time as _time

        await emit_event(
            recorder,
            "step_start",
            {
                "turn": 1,
                "step": step,
                "model": getattr(composed, "model", None),
                "is_stream": is_stream,
            },
            turn=1,
            step=step,
        )
        return _time.monotonic()

    async def _step_ended(
        self, recorder, composed, step: int, t0: float, usage, finish_reason
    ) -> None:
        """step_end bracket: usage + cost at this run's rates + duration.
        ``run.cost`` folds from these — the only place per-call cost is born."""
        import time as _time

        usage = usage or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        try:
            from app.services.ai.runner.usage_cached import extract_cached_input_tokens

            cached = int(extract_cached_input_tokens(usage) or 0)
        except Exception:  # noqa: BLE001
            cached = 0
        cost = None
        if recorder is not None and hasattr(recorder, "cost_of"):
            try:
                cost = recorder.cost_of(prompt, completion, cached)
            except Exception:  # noqa: BLE001
                cost = None
        # Context gauge: this call's prompt is the context the model just held.
        # A local fold (no event row) — the compactor only reports on
        # compaction, so without this the gauge stays empty on every turn that
        # never compacts (2026-09-05 真栈验收: view.context null).
        if recorder is not None and hasattr(recorder, "measure_context") and prompt > 0:
            try:
                from app.agent_framework.context_window import resolve_model_window

                window, known = resolve_model_window(getattr(composed, "model", None))
                if known and window:
                    recorder.measure_context(prompt, int(window))
            except Exception:  # noqa: BLE001 — a gauge never fails a turn
                pass
        await emit_event(
            recorder,
            "step_end",
            {
                "turn": 1,
                "step": step,
                "model": getattr(composed, "model", None),
                "usage": {"prompt": prompt, "completion": completion, "cached": cached},
                "cost_cents": cost,
                "duration_ms": int((_time.monotonic() - t0) * 1000),
                "finish_reason": finish_reason,
            },
            turn=1,
            step=step,
        )

    async def _dispatch_ask_user(
        self,
        args: dict,
        recorder: Optional[RunRecorder],
        iteration: int,
        composed: Any = None,
    ) -> dict:
        """Phase 2a: AskUser records ``question_asked`` through the shared
        question primitive. The park itself happens in the ladder right
        after the tool result is traced (both paths).

        Only a turn that ADVERTISED the tool may park on it: a sub-agent or
        any runner whose ``composed.tools`` never listed AskUser has nobody
        who could answer, so the call gets a typed refusal instead of a
        question parked in the void."""
        if not self._advertises(composed, ASK_USER_TOOL_NAME):
            return {
                "asked": False,
                "error": (
                    "AskUser is not available on this turn — nobody is "
                    "listening for an answer here."
                ),
            }
        return await ask_user_handler(args, recorder=recorder, turn=1, step=iteration)

    @staticmethod
    def _advertises(composed: Any, tool_name: str) -> bool:
        for spec in getattr(composed, "tools", None) or []:
            if not isinstance(spec, dict):
                continue
            if (spec.get("function") or {}).get("name") == tool_name:
                return True
        return False

    async def _skip_parked_siblings(
        self,
        calls: list,
        current: Any,
        tool_call_trace: list[dict],
        recorder: Any,
        iteration: int,
        messages: list[dict],
    ) -> None:
        """The turn parks on AskUser; any tool call the model put AFTER it in
        the same message is not executed. Give each a paired, self-describing
        result (trace + transcript + tool message) so nothing is orphaned or
        silently dropped."""
        import json as _json

        seen = False
        for call in calls:
            if call is current:
                seen = True
                continue
            if not seen:
                continue
            name = (call.get("function") or {}).get("name", "")
            result = {
                "error": "not executed: the turn parked on AskUser before this call",
                "skipped": True,
            }
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": _json.dumps(result, ensure_ascii=False),
                }
            )
            tool_call_trace.append(
                {"name": name, "args": {}, "result": result, "iteration": iteration}
            )
            await emit_event(
                recorder,
                "tool_call",
                {"tool": name, "args": {}, "result": result, "iteration": iteration},
            )

    @staticmethod
    def _parked_question(recorder: Any) -> Optional[dict]:
        from app.services.ai.runner.question import payload_from_view

        views = getattr(recorder, "views", None) or {}
        parked = (views.get("view") or {}).get("question")
        return payload_from_view(parked) if parked else None

    def _awaiting_input_bracket(self, recorder: Any) -> str:
        q = self._parked_question(recorder) or {}
        return f"\n\n[awaiting input: {q.get('prompt') or 'question pending'}]"

    def _awaiting_input_response(
        self, recorder: Any, tool_call_trace: list[dict]
    ) -> dict[str, Any]:
        """Mirror of ``_awaiting_approval_response`` for a typed question;
        carries the trace so FinishIssue / AskUser declarations survive."""
        return {
            # Same bracket line the stream path yields, so both paths persist
            # one shape for the same event.
            "content": self._awaiting_input_bracket(recorder),
            "raw": None,
            "awaiting_input": True,
            "stop_reason": "awaiting_input",
            "cancelled": False,
            "question": self._parked_question(recorder),
            "tool_calls": tool_call_trace,
        }

    async def _timed(self, tool_name: str, coro):
        """All tool handlers go through here (phase 2b-1 §3): the per-tool
        wall-clock limit; a timeout is a tool RESULT the model reads
        (``{error: "timeout", timed_out: true, ...}``), never a run stop."""
        from app.services.ai.runner.tool_exec import run_tool_with_timeout

        return await run_tool_with_timeout(tool_name, coro)

    async def _dispatch_finish_issue(self, args: dict) -> dict:
        """Spec-2: route a FinishIssue call to the per-request handler injected
        by the chat service. None means this is not an issue turn — return a
        clear result instead of crashing. Mirrors the ResourceFetch contract."""
        if self.finish_issue_handler is None:
            return {
                "error": (
                    "FinishIssue is not available on this turn — it only "
                    "applies when working an assigned issue."
                )
            }
        try:
            return await self.finish_issue_handler(args)
        except Exception as fi_exc:  # noqa: BLE001
            logger.warning(f"[AgentRunner] FinishIssue handler raised: {fi_exc!r}")
            return {"error": f"FinishIssue failed: {fi_exc.__class__.__name__}"}

    async def _dispatch_schedule_wakeup(self, args: dict, recorder: Any) -> dict:
        """Route a ScheduleWakeup call to the per-request handler injected by
        the chat service. ``recorder`` is passed through because the run it
        represents is what the row is stamped with and what the
        ``schedule_set`` event is written to — neither exists when the tool is
        registered. Mirrors the FinishIssue contract: never raises."""
        if self.schedule_wakeup_handler is None:
            return {
                "error": (
                    "ScheduleWakeup is not available on this turn — it only "
                    "applies while working an assigned issue."
                )
            }
        try:
            return await self.schedule_wakeup_handler(args, recorder)
        except Exception as sw_exc:  # noqa: BLE001
            logger.warning(f"[AgentRunner] ScheduleWakeup handler raised: {sw_exc!r}")
            return {"error": f"ScheduleWakeup failed: {sw_exc.__class__.__name__}"}

    def _high_risk_gate_registered(self) -> bool:
        """True when ``HighRiskCapabilityGateHook`` is actually installed in
        this runner's PreToolUse chain.

        Checked by ``isinstance``, not by the registration NAME: a name match
        proves only that something is registered under that string, while the
        type is what actually implements the write-grading decision. It also
        survives a caller registering the hook under a different name.

        Any failure to introspect (no registry, a registry that raises, an
        import problem) is treated as "not registered" — this is the input to
        a fail-closed decision, so an unknown answer must be the restrictive
        one.
        """
        try:
            from app.services.infra.hooks.high_risk_capability_gate import (
                HighRiskCapabilityGateHook,
            )

            if self.hooks is None:
                return False
            return any(
                isinstance(entry.hook, HighRiskCapabilityGateHook)
                for entry in self.hooks.get_pre_hooks()
            )
        except Exception:  # noqa: BLE001 — unknown ⇒ deny
            logger.warning(
                "[AgentRunner] could not introspect the hook registry; "
                "treating the high-risk capability gate as absent"
            )
            return False

    async def _dispatch_screenwriting(
        self,
        tool_name: str,
        args: dict,
        recorder: Optional[RunRecorder],
        composed: "ComposedSystemPrompt",
        step: Optional[int] = None,
    ) -> dict:
        """Run one A4 screenwriting tool.

        The handler needs only ``run_id`` (from which ``scope_for_run``
        re-derives the run's server-bound scope) plus identity for logging —
        exactly what ``_media_run_context`` already assembles, so it is
        reused rather than duplicated.

        THE A1 GATE IS A PRECONDITION, NOT AN AMBIENT GUARANTEE (A4 review,
        Critical 1). Write grading lives in ``HighRiskCapabilityGateHook``,
        which only runs if it was registered on this runner's ``hooks``.
        ``_run_pre_hooks`` returns ``None`` immediately when ``self.hooks is
        None`` — so on a hookless runner the PreToolUse chain is a no-op and
        every tool below would execute UNGATED. Seven services build
        ``AgentRunner(adapter=..., skill_tool=...)`` with no hooks
        (script_ai / summarize / caption / classify / translate /
        visual_analysis / topic_scorer) while composing
        through ``PromptComposer.compose`` — which advertises these tools.
        Moving advertisement into the composer made it universal;
        enforcement stayed on the four ``build_agent_runner_stack`` paths.

        So the gate's presence is checked HERE, once, and its absence is a
        refusal. A runner with no capability gate has no business running
        capability-gated tools. This is deliberately NOT a duplicate
        capability check inside the handlers: re-deriving "may this agent
        write" in a second place is exactly the drifting second source of
        truth the handler docstring warns against. This asks a different,
        structural question — "is the enforcement mechanism installed at
        all?" — and answers it fail-closed.

        Without a recorder there is no ``agent_runs`` row, hence no scope,
        hence nothing these tools may touch. Say so explicitly instead of
        letting ``scope_for_run(None)`` return None and surfacing the generic
        "no project bound" message — an un-recorded run is a wiring problem,
        not a user-facing scope problem, and the two need different fixes.
        """
        from app.services.ai.tools.screenwriting_tools import SCREENWRITING_HANDLERS

        if not self._high_risk_gate_registered():
            logger.warning(
                f"[AgentRunner] refusing {tool_name}: no "
                f"HighRiskCapabilityGateHook on this runner — the tool would "
                f"run without write grading"
            )
            return {
                "ok": False,
                "error": (
                    f"{tool_name} is unavailable on this runner: its "
                    "capability gate is not installed, so the call cannot be "
                    "authorized."
                ),
                "error_code": "capability_gate_missing",
            }

        if recorder is None or recorder.run_id is None:
            return {
                "ok": False,
                "error": (
                    f"{tool_name} is unavailable: this turn is not recorded as "
                    "an agent run, so it carries no script scope."
                ),
                "error_code": "no_run_context",
            }
        handler = SCREENWRITING_HANDLERS.get(tool_name)
        if handler is None:  # pragma: no cover — names come from one frozenset
            return {"ok": False, "error": f"unknown screenwriting tool {tool_name}"}
        try:
            return await handler(
                args, self._media_run_context(recorder, composed, step=step)
            )
        except Exception as exc:  # noqa: BLE001 — never raise into the loop
            logger.warning(f"[AgentRunner] {tool_name} raised: {exc!r}")
            return {
                "ok": False,
                "error": f"{tool_name} failed: {exc.__class__.__name__}",
                "error_code": "tool_error",
            }

    def _media_run_context(
        self,
        recorder: Optional[RunRecorder],
        composed: "ComposedSystemPrompt",
        step: Optional[int] = None,
    ) -> dict:
        """Build a run-context dict for media generation handlers.

        Passed as the second argument to generate_image_handler /
        generate_video_handler so those callables know which run, user,
        team, and agent triggered the generation request.
        """
        return {
            "run_id": recorder.run_id if recorder else None,
            "user_id": str(recorder.user_id) if recorder else None,
            "team_id": recorder.team_id if recorder else None,
            "agent_id": str(composed.agent_id),
            # 产出登记要坐标才能把卡挂到正确的那一步（3a）。turn 与
            # ``_step_started`` 同源：目前恒为 1，改它要一起改。没有 recorder
            # 就没有 run，那一路的产出不该假装属于某个 turn。
            "turn": 1 if recorder else None,
            "step": step,
            # 3a T8c 缺陷 1：登记口没拿到 recorder 就退回
            # ``RunEventWriter.for_run`` —— 同一个 run 上的**第二个** writer。
            # 它把 ``view.outputs`` 折进 ``metadata_json``，活 recorder 的下
            # 一次镜像（写的是整个 ``view`` 值）再原样抹掉，于是座舱那一格
            # 唯一的数据源永远是空的；两个 writer 各记各的 seq，活 recorder
            # 的下一条 insert 还会撞唯一索引被丢掉。把活 recorder 交出去，
            # 这两半一起消失。⚠️ 只有**活着的**那条路有 recorder 可交；迟到
            # 的登记（run 已结束）照旧走 ``for_run``，那时没有第二个 writer。
            "recorder": recorder,
        }

    async def _preflight_compact_and_budget(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
        recorder: Optional[RunRecorder],
    ) -> tuple[list[dict], Optional[dict[str, Any]]]:
        """Shared pre-flight for run_turn AND stream_turn.

        Runs tiered compaction (prune tool results / emergency-cap bodies)
        then the context-budget guard. Returns
        ``(possibly_compacted_user_messages, error_or_None)``. The error dict
        (``error`` + ``error_code='context_budget_exceeded'``) lets each caller
        surface it in its own shape — run_turn returns it, stream_turn yields a
        terminal chunk. Extracted because the streaming path (the primary
        ChatPanel route) silently lacked BOTH protections, so a long
        conversation could overflow the window with a cryptic provider error
        instead of a clean rejection.
        """
        user_messages, compaction_stats = await _DEFAULT_COMPACTOR.maybe_compact(
            system_message=composed.system_message,
            user_messages=user_messages,
            model=composed.model,
            # W3-1: the compactor replays this conversation's own prefix on
            # its own adapter, so the summary's input tokens ride the
            # provider's warm cache. Same account, same routing — the
            # maintenance model has no cache of this conversation to hit.
            adapter=self.adapter,
            tools=composed.tools,
            # Phase 2: the compaction bracket (start/summary/end) lands in the
            # run transcript so a crash mid-summary is visible as an orphan.
            recorder=recorder,
        )
        if (
            recorder is not None
            and compaction_stats.tokens_saved > 0
            and hasattr(recorder, "note_compaction")
        ):
            recorder.note_compaction(compaction_stats)

        try:
            from app.agent_framework import ContextWindowError, check_context_budget

            check_context_budget(
                system_prompt=composed.system_message,
                user_messages=user_messages,
                model=composed.model,
            )
        except ContextWindowError as exc:
            logger.warning(f"[AgentRunner] context budget rejected: {exc}")
            return user_messages, {
                "error": str(exc),
                "error_code": "context_budget_exceeded",
            }
        return user_messages, None

    async def run_turn(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
        *,
        recorder: Optional[RunRecorder] = None,
        abort: Optional["AbortController"] = None,
        _emit_turn_end: bool = True,
    ) -> dict[str, Any]:
        """Run one turn, with retry telemetry attached for its duration.

        ``_emit_turn_end=False`` is for stream_turn's buffered fallback only:
        that path delegates here and then classifies the turn itself from
        the terminal chunk — two wrappers each filing a ``turn_end`` would
        double-count every buffered turn.

        W1: the adapter is built during wiring, before this run exists, so a
        recorder cannot be constructor-injected into the retry middleware.
        The observer is attached here and detached in ``finally`` — an adapter
        that outlived one turn while still holding a previous run's recorder
        would file THIS run's retries under THAT run.

        Adapters without an ``on_retry`` slot (every plain, non-chain adapter)
        are left untouched.
        """
        has_slot = recorder is not None and hasattr(self.adapter, "on_retry")
        if has_slot:
            from app.services.ai.llm.retry_events import make_retry_observer

            self.adapter.on_retry = make_retry_observer(recorder)
        # Phase-2 (443): same attach-for-the-turn discipline for the skill
        # tool, so Skill(todo) can file its whole-list snapshots under THIS
        # run — and detach after, or a tool outliving one turn would file the
        # next run's todos under this one.
        todo_slot = recorder is not None and hasattr(self.skill_tool, "recorder")
        if todo_slot:
            self.skill_tool.recorder = recorder
        # Phase 2: one typed ``turn_end`` per turn, classified from the
        # outcome in one place (see turn_end.py for why not per-exit tags).
        from app.services.ai.runner.turn_end import (
            classify_exception,
            classify_run_result,
            emit_turn_end,
        )

        try:
            result = await self._run_turn_inner(
                composed, user_messages, recorder=recorder, abort=abort
            )
        except BaseException as exc:
            if _emit_turn_end:
                reason, extra = classify_exception(exc)
                await emit_turn_end(recorder, reason, extra)
            raise
        else:
            if _emit_turn_end:
                reason, extra = classify_run_result(result)
                await emit_turn_end(recorder, reason, extra)
            return result
        finally:
            if has_slot:
                self.adapter.on_retry = None
            if todo_slot:
                self.skill_tool.recorder = None

    @staticmethod
    def _stopped_response(step_ctx: "StepContext", recorder: Any) -> dict:
        """The run_turn result for a hook STOP. ``stop_reason`` is the truth
        (turn_end.STOP_REASON_TO_TURN_END); ``cancelled`` stays for callers
        that predate typed stops. ``awaiting_input`` also hands back the
        parked question in ``Question.to_payload()`` shape (``question_id``,
        not the view's ``id``) so the dispatcher can build the marker
        without a second read of the views."""
        from app.services.ai.runner.question import payload_from_view

        reason = step_ctx.stop_reason
        base: dict[str, Any] = {
            "content": "",
            "raw": None,
            "stop_reason": reason,
            "cancelled": reason == "cancelled",
        }
        if reason == "awaiting_input":
            views = getattr(recorder, "views", None) or {}
            parked = (views.get("view") or {}).get("question")
            return {
                **base,
                "awaiting_input": True,
                "question": payload_from_view(parked) if parked else None,
            }
        return base

    def _bind_turn_recorder(self, recorder: Optional["RunRecorder"]) -> None:
        """Hand the per-turn recorder to BOTH tools that spawn work.

        Two things ride on this, and the second one is not telemetry. Phase 5
        of #199 rolls a spawn's envelope counters up to ``metadata.subagents``
        — and since Task 7a the recorder is also where a spawn reads the id of
        the run it is spawning FROM (``active_parent_run_id``), because the
        constructor value is ``None`` on every root run. Both turn loops bind
        it, and both tools receive it: a path that forgot to would silently
        detach every child it spawns, and for ``Delegate`` it would also turn
        cycle protection off.

        Set lazily and per tool — ``SkillToolService.subagent_task`` and
        ``delegate_tool`` are each optional, depending on what the chat layer
        installed for this run.
        """
        if recorder is None:
            return
        for owner, attr in (
            (getattr(self.skill_tool, "subagent_task", None), "subagent_task"),
            (getattr(self, "delegate_tool", None), "delegate_tool"),
        ):
            if owner is None:
                continue
            try:
                owner.parent_recorder = recorder
            except Exception:  # noqa: BLE001 — never fail a turn over a binding
                logger.exception(f"[runner] could not bind the recorder to {attr}")

    async def _run_turn_inner(
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
        # Pre-flight: tiered compaction (prune tool results / emergency-cap
        # bodies) + context-budget guard, shared with stream_turn via
        # _preflight_compact_and_budget so the two paths can never again
        # diverge on these protections. On green compaction is ~free; the
        # budget guard rejects a turn that wouldn't fit even after pruning.
        user_messages, preflight_err = await self._preflight_compact_and_budget(
            composed, user_messages, recorder
        )
        if preflight_err is not None:
            return {"content": "", "raw": None, **preflight_err}

        self._bind_turn_recorder(recorder)

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
        await emit_event(recorder, "user", _user_event_payload(composed, user_messages))

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

            _step_ctx = StepContext(
                turn=1,
                step=iteration,
                recorder=recorder,
                composed=composed,
                messages=messages,
                parent_run_id=self.parent_run_id,
            )
            if await self.step_hooks.run(_step_ctx) is StepDecision.STOP:
                return self._stopped_response(_step_ctx, recorder)
            if _step_ctx.injected:
                messages.extend(_step_ctx.injected)

            _t0 = await self._step_started(
                recorder, composed, iteration, is_stream=False
            )
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

            await self._step_ended(
                recorder,
                composed,
                iteration,
                _t0,
                resp.get("usage"),
                (resp.get("choices") or [{}])[0].get("finish_reason"),
            )
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
                # strip_reasoning drops a Qwen3 <think>…</think> block so every
                # caller (chat + summarize/translate/caption/… services) gets
                # only the answer; no-op for non-thinking models. raw stays full.
                content = strip_reasoning(msg.get("content") or "")
                await emit_event(recorder, "assistant", {"content": content})
                if recorder is not None and hasattr(recorder, "record_event"):
                    # A turn that ends with neither text nor a tool call
                    # leaves the user with nothing, and today leaves US with
                    # nothing either: 22% of `doubao-seed-2-0-lite-260428`
                    # runs closed exactly like this in the 30 days to
                    # 2026-08-23, 7 of 8 with billed completion tokens, and
                    # no record anywhere of what the response held. Record
                    # the field names and sizes (never the content) so the
                    # next occurrence says whether the text went somewhere we
                    # don't read — which decides whether retrying it would
                    # help or just buy more of the same.
                    diagnosis = diagnose_empty_response(resp)
                    if diagnosis is not None:
                        # Evidence is never worth losing the turn over —
                        # emit_event swallows and logs.
                        await emit_event(
                            recorder, "error", {"kind": "empty_response", **diagnosis}
                        )
                return {
                    "content": content,
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
                    # Never skip silently. An assistant tool_call left without a
                    # tool result is an orphan the NEXT request is rejected for
                    # (OpenAI: every tool_call_id must be answered; Anthropic:
                    # orphaned tool_use). Synthesize an error result so the log
                    # stays replay-legal AND the model learns the tool does not
                    # exist instead of calling it again (dsh tools rule: a call
                    # that never started still gets a paired result).
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "name": tool_name,
                            "content": json.dumps(
                                {
                                    "error": f"unknown tool: {tool_name}",
                                    "synthetic": True,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
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
                    result = await self._timed(tool_name, self.skill_tool.execute(args))
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
                            result = await self._timed(
                                tool_name, self.resource_fetch_handler(args)
                            )
                        except Exception as rf_exc:
                            logger.warning(
                                f"[AgentRunner] ResourceFetch handler raised: {rf_exc!r}"
                            )
                            result = {
                                "error": f"ResourceFetch failed: {rf_exc.__class__.__name__}"
                            }
                elif tool_name == ASK_USER_TOOL_NAME:
                    result = await self._timed(
                        tool_name,
                        self._dispatch_ask_user(args, recorder, iteration, composed),
                    )
                elif tool_name == "FinishIssue":
                    result = await self._timed(
                        tool_name, self._dispatch_finish_issue(args)
                    )
                elif tool_name == SCHEDULE_WAKEUP_TOOL_NAME:
                    result = await self._timed(
                        tool_name,
                        self._dispatch_schedule_wakeup(args, recorder),
                    )
                elif tool_name == "GenerateImage":
                    if self.generate_image_handler is None:
                        result = {
                            "ok": False,
                            "error": "GenerateImage not configured",
                        }
                    else:
                        result = await self._timed(
                            tool_name,
                            self.generate_image_handler(
                                args,
                                self._media_run_context(
                                    recorder, composed, step=iteration
                                ),
                            ),
                        )
                elif tool_name == "GenerateVideo":
                    if self.generate_video_handler is None:
                        result = {
                            "ok": False,
                            "error": "GenerateVideo not configured",
                        }
                    else:
                        result = await self._timed(
                            tool_name,
                            self.generate_video_handler(
                                args,
                                self._media_run_context(
                                    recorder, composed, step=iteration
                                ),
                            ),
                        )
                elif tool_name in SCREENWRITING_TOOL_NAMES:
                    result = await self._timed(
                        tool_name,
                        self._dispatch_screenwriting(
                            tool_name, args, recorder, composed, step=iteration
                        ),
                    )
                elif is_mcp:
                    # Q5: route to outbound MCP server. Tool errors
                    # (server returned isError=true) come back as a
                    # normal result dict with error info. Transport
                    # failures (network down, 4xx) → MCPClientError
                    # which we catch and convert to a result dict so
                    # the LLM gets feedback instead of crashing the run.
                    from app.agent_framework._metrics_helper import inc_metric

                    try:
                        result = await self._timed(
                            tool_name, self.mcp_registry.call(tool_name, args)
                        )
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
                        result = await self._timed(
                            tool_name, self.delegate_tool.execute(args)
                        )

                # Image promotion (mirrors stream_turn): strip base64 BEFORE
                # the trace/recorder capture the result; the pixels ride only
                # in the injected user-message image part below.
                promoted_parts: list[dict] = []
                if tool_name == "ResourceFetch":
                    img_blocks = _image_blocks(result)
                    if img_blocks:
                        if self.vision_capable:
                            promoted_parts = [
                                {"type": "image_url", "image_url": {"url": b["url"]}}
                                for b in img_blocks
                            ]
                            result = _strip_image_urls(result, _IMG_PROMOTED_NOTE)
                        else:
                            result = _strip_image_urls(result, _IMG_OMITTED_NOTE)

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
                await emit_event(
                    recorder,
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
                if promoted_parts:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "[image content from ResourceFetch]",
                                },
                                *promoted_parts,
                            ],
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

                if tool_name == ASK_USER_TOOL_NAME and result.get("asked"):
                    await self._skip_parked_siblings(
                        tool_calls, call, tool_call_trace, recorder, iteration, messages
                    )
                    return self._awaiting_input_response(recorder, tool_call_trace)

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
            hook_result = await self._safe_invoke_pre(
                entry.name, entry.hook, ctx, fail_closed=entry.fail_closed
            )
            if hook_result is None:
                continue
            self._dispatch_side_effect(entry.name, hook_result)
            last_result = hook_result
            if hook_result.decision in ("abort", "await_approval"):
                if (
                    hook_result.decision == "abort"
                    and hook_result.abort_code == "capability_denied"
                    and recorder is not None
                    and hasattr(recorder, "record_event")
                    and tool_name not in self._denied_tools_this_turn
                ):
                    # 同一 turn 同一工具只落第一条,防模型重试刷屏(spec §5)。
                    self._denied_tools_this_turn.add(tool_name)
                    await emit_event(
                        recorder,
                        "capability_denied",
                        {"tool": tool_name, "reason": hook_result.abort_reason or ""},
                    )
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
        *,
        fail_closed: bool = False,
    ) -> Optional[HookResult]:
        try:
            return await hook(ctx)
        except Exception as exc:  # noqa: BLE001
            if fail_closed:
                # Security-relevant gate (e.g. CapabilityGate): a bug in the
                # gate must BLOCK the tool, not silently let it through.
                logger.exception(
                    "[hook:%s] PreToolUse raised; failing CLOSED (blocking tool)",
                    name,
                )
                return HookResult(
                    decision="abort",
                    abort_reason=(
                        f"security hook '{name}' failed closed: "
                        f"{exc.__class__.__name__}"
                    ),
                )
            # Default fail-open: a buggy non-security hook must never break a run.
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
            parent_run_id=self.parent_run_id,
            agent_depth=self.agent_depth,
            delegation_chain=self.delegation_chain,
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
