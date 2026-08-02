"""Bounded fallback: force a FinishIssue declaration when the normal issue
turn produced content but the agent never called the tool.

Production evidence (2026-08-01 E2E probe): the issue-execution protocol
depends on the agent calling ``FinishIssue`` to declare its outcome, but in
production no agent run ever calls it — not even when the model's own prose
IS the declaration ("I cannot select the color scheme, I need you to make
the choice"). The model serving ~all issue-executing agents
(``doubao-seed-2-0-lite-260428``) simply ignores ``FINISH_ISSUE_INSTRUCTION``
because ``tool_choice`` is hardcoded ``"auto"`` on the primary turn
(``openai_compat.py``) — the model is free not to call anything.

This module makes exactly ONE short follow-up call with ``tool_choice``
FORCED to ``FinishIssue``, feeding the turn's own assistant text as the
judging context. It never retries and never raises to the caller — any
failure (missing session, adapter resolution failure, an adapter that
doesn't support forcing, a malformed tool call) degrades to ``(None, None)``,
which is today's exact behavior (the issue lifecycle's ``in_review``
default). This is a decoration on an already-succeeded turn, never a new
failure mode.

Quota safety: this call records its OWN ``agent_runs`` row (a fresh
``RunRecorder``) under a trigger DISTINCT from ``'issue_dispatch_auto'`` —
see ``AgentRunsRepository.count_auto_dispatches_today``, which filters the
autopilot daily quota on that EXACT string. A forced-declaration call must
never inflate that counter even when it follows an auto-dispatched turn.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.run_recorder import RunRecorder
from app.services.ai.tools.finish_issue_tool import (
    FINISH_ISSUE_INSTRUCTION,
    FINISH_ISSUE_TOOL_NAME,
    extract_issue_outcome,
    finish_issue_handler,
    finish_issue_spec,
)

# Bounded — this is a short declare-only turn, not a full agent response.
FORCED_DECLARE_MAX_TOKENS = 300
FORCED_DECLARE_TEMPERATURE = 0.2

# The forced call bypasses LLMFallbackChain (and therefore its
# _llm_total_deadline_s ceiling) — only the adapter's own client timeout
# (60s default) would otherwise bound it, inside a @DBOS.step. A
# declaration-only call has no business waiting that long; on timeout this
# degrades to (None, None) via the same outer fail-open handling as any
# other failure.
FORCED_DECLARE_TIMEOUT_S = 20.0

# Standard OpenAI-compatible "force this exact function" shape.
FORCED_DECLARE_TOOL_CHOICE: dict[str, Any] = {
    "type": "function",
    "function": {"name": FINISH_ISSUE_TOOL_NAME},
}


async def attempt_forced_finish_declaration(
    *,
    session_id: str,
    user_id: str,
    assistant_text: str,
    issue_id: int,
    trigger: str,
    attribution: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Public, fully fail-open entry point — see module docstring.

    ``attribution`` (W3c two-level cost tag) should be the SAME value the
    caller passed to the main turn's own RunRecorder (``rule_owner`` for a
    routine/pipeline-originated issue, ``None``/``direct_human`` otherwise)
    — this call opens its OWN RunRecorder, so without threading it through
    explicitly every forced-declare row would silently default to
    ``direct_human`` regardless of the issue's real origin.

    Returns ``(outcome, reason)``, both ``None`` on any failure. Callers
    should still wrap this in their own try/except as defense in depth (the
    issue-execution path does), but this function itself is designed to
    never raise.
    """
    try:
        return await _run_forced_declare_turn(
            session_id=session_id,
            user_id=user_id,
            assistant_text=assistant_text,
            issue_id=issue_id,
            trigger=trigger,
            attribution=attribution,
        )
    except Exception as exc:  # noqa: BLE001 — never break an already-succeeded turn
        logger.warning(
            f"[forced_finish_declaration] issue={issue_id} session={session_id} "
            f"failed; falling back to no-declaration default: {exc!r}"
        )
        return None, None


async def _resolve_agent_and_adapter(
    session_id: str, user_id: str
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    """Load the issue session's agent record + a SINGLE adapter (no fallback
    chain, no retries — this is a one-shot bounded call) for its configured
    model.

    Mirrors the credential resolution ``build_agent_runner_stack`` does
    (platform ``mediahub_models`` catalog → user BYOK) minus memory recall /
    hooks / delegate wiring, none of which a declare-only call needs.
    """
    from app.services.ai.adapters.factory import (
        get_adapter_for_key,
        get_adapter_for_user,
        resolve_provider_key,
    )
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.ai.chat.ai_library_chat_wiring import (
        _load_user_provider_config,
    )
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_chat_config,
        resolve_mediahub_model,
    )

    uid = UUID(str(user_id))
    session = await AILibraryChatService().get_session(session_id, user_id=uid)
    agent_slug = session.get("agent_slug")
    if not agent_slug:
        raise RuntimeError(f"session {session_id} has no agent_slug bound")

    agent_record = await get_agent_repository().get_by_slug(
        agent_slug,
        override_user_id=uid,
        override_team_id=session.get("team_id"),
    )
    if not agent_record:
        raise RuntimeError(f"agent slug not found: {agent_slug}")

    model = agent_record.get("model") or "qwen-max"
    hit = await resolve_mediahub_model(model, "chat")
    if hit:
        actual_provider, cfg, actual_model = hit
        creds = {"api_key": cfg["api_key"], "base_url": cfg["base_url"]}
        key = resolve_provider_key(actual_provider, actual_model)
        adapter = get_adapter_for_key(key, actual_model, {key: creds})
        model = actual_model
    else:
        chat_cfg = await resolve_chat_config(
            uid,
            model=model,
            load_user_config=lambda: _load_user_provider_config(uid),
            agent_slug=agent_slug,
        )
        adapter = get_adapter_for_user(model, chat_cfg.provider_config, None)

    resolved_agent = {**agent_record, "model": model}
    return resolved_agent, adapter, session


async def _run_forced_declare_turn(
    *,
    session_id: str,
    user_id: str,
    assistant_text: str,
    issue_id: int,
    trigger: str,
    attribution: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    agent_record, adapter, session = await _resolve_agent_and_adapter(
        session_id, user_id
    )

    composed = ComposedSystemPrompt(
        agent_id=UUID(str(agent_record["id"])),
        agent_slug=agent_record.get("slug") or "",
        model=agent_record.get("model") or "",
        temperature=FORCED_DECLARE_TEMPERATURE,
        max_tokens=FORCED_DECLARE_MAX_TOKENS,
        system_message=(
            "You just worked on an assigned issue but your turn ended "
            "without declaring an outcome.\n\n" + FINISH_ISSUE_INSTRUCTION
        ),
        tools=[finish_issue_spec()],
        skill_manifest=[],
        cache_fingerprint="forced-finish-declare",
    )
    messages = [
        {
            "role": "user",
            "content": (
                "Your last message on this issue was:\n\n"
                f"{assistant_text}\n\n"
                "Call FinishIssue now to declare the outcome that best "
                "matches what you just did."
            ),
        }
    ]

    provider: Optional[str] = None
    if composed.model:
        try:
            from app.services.ai.adapters.factory import provider_key_for_model

            provider = provider_key_for_model(composed.model)
        except ValueError:
            provider = None

    # #1637: the Runs panel groups run-groups by
    # COALESCE('conv:'||conversation_id, 'run:'||id) — mirror the SAME
    # store_kind dispatch ai_library_chat_service.py uses (lines ~862-877)
    # so this row attaches to the issue's own conversation instead of
    # rendering as an orphan standalone "..._finish_declare" card next to
    # the run it followed.
    _is_conv_store = session.get("store_kind") == "conversations"

    outcome: Optional[str] = None
    reason: Optional[str] = None
    async with RunRecorder(
        agent_id=composed.agent_id,
        user_id=UUID(str(user_id)),
        session_id=None if _is_conv_store else session_id,
        conversation_id=int(session_id) if _is_conv_store else None,
        # Distinct from 'issue_dispatch' / 'issue_dispatch_auto' — the
        # autopilot quota counter (count_auto_dispatches_today) filters on
        # the LITERAL string 'issue_dispatch_auto'. This value never matches
        # it, so a forced-declare call can never inflate the daily quota.
        trigger=f"{trigger}_finish_declare",
        team_id=session.get("team_id"),
        project_id=session.get("project_id"),
        issue_id=issue_id,
        model=composed.model or None,
        provider=provider,
        input_summary=assistant_text,
        # W3c: same two-level cost tag as the main turn — without this every
        # forced-declare row silently defaults to direct_human regardless of
        # the issue's real origin (see attempt_forced_finish_declaration's
        # docstring).
        attribution=attribution,
        metadata={"forced_declare": True},
    ) as recorder:
        # Explicit capability probe rather than try/except TypeError: a
        # TypeError raised from INSIDE a working adapter (a real bug) must
        # not be swallowed and misreported as "adapter doesn't support
        # tool_choice", which would silently burn a second full paid API
        # call with a misleading log line.
        call_params = inspect.signature(adapter.call).parameters
        supports_tool_choice = "tool_choice" in call_params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in call_params.values()
        )
        if supports_tool_choice:
            call_coro = adapter.call(
                composed, messages, tool_choice=FORCED_DECLARE_TOOL_CHOICE
            )
        else:
            # Adapter doesn't accept tool_choice (e.g. ClaudeAdapter, or a
            # RotatingAdapter wrapper). Fall back to an ordinary call —
            # FINISH_ISSUE_INSTRUCTION is still in the system message, so
            # this is strictly better than not trying, even without a hard
            # force.
            logger.info(
                f"[forced_finish_declaration] {type(adapter).__name__} does "
                f"not accept tool_choice; calling without forcing"
            )
            call_coro = adapter.call(composed, messages)

        resp = await asyncio.wait_for(call_coro, timeout=FORCED_DECLARE_TIMEOUT_S)

        usage = resp.get("usage") or {}
        recorder.record_usage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        trace: list[dict[str, Any]] = []
        for call in tool_calls:
            fn = call.get("function") or {}
            if fn.get("name") != FINISH_ISSUE_TOOL_NAME:
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await finish_issue_handler(args)
            trace.append(
                {"name": FINISH_ISSUE_TOOL_NAME, "args": args, "result": result}
            )

        outcome, reason = extract_issue_outcome(trace)

        if outcome == "continue":
            # Controller decision (review round 2): unlocking continuations
            # from the FORCED path is a separate, observable behavior change
            # from "the agent finally declares an outcome", not a side
            # effect of this fallback existing. Before this module existed,
            # outcome was ALWAYS None here, so issue_lifecycle.py's
            # `outcome == "continue" and attempt < max_continuations` branch
            # (each continuation = a full run_issue_agent — quota-counted
            # when auto) never fired in production; the autopilot daily
            # quota was calibrated assuming it never would. A forced call
            # only has the turn's own last message as context (not the
            # full conversation), so it is far more likely to hedge with
            # "continue" than a model that organically decides to declare —
            # letting it drive continuation could turn one autopilot
            # dispatch into up to 3 full agent runs / quota units instead of
            # 1. Narrow to "no declaration" (today's in_review default).
            # A NORMALLY-declared continue (the agent called FinishIssue on
            # its own, read from the ORIGINAL turn's tool_calls before this
            # module is ever invoked) is untouched by this and still drives
            # continuation exactly as before.
            logger.info(
                f"[forced_finish_declaration] issue={issue_id} forced call "
                f"returned 'continue'; narrowing to no-declaration — "
                f"continuations only come from an organic FinishIssue call"
            )
            outcome, reason = None, None

        recorder.set_summaries(output_summary=f"forced declare: outcome={outcome!r}")

    logger.info(
        f"[forced_finish_declaration] issue={issue_id} session={session_id} "
        f"fired; outcome={outcome!r} reason={reason!r}"
    )
    return outcome, reason


__all__ = [
    "FORCED_DECLARE_TOOL_CHOICE",
    "attempt_forced_finish_declaration",
]
