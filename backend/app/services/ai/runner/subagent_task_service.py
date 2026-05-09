"""SubAgent Task tool — synchronous spawn-and-return.

Phase 3b of issue #199. The main agent calls
``Skill(skill="task", subagent_type=..., prompt=...)`` and the call
blocks until the sub-agent has finished its turn, then returns a
compact envelope. Distinct from ``DelegateToolService``:

  - DelegateTool — workforce / inbox-based / target must be persistent
    / can run async-await across many turns
  - SubAgentTask — same-turn / same-process / ephemeral / target needs
    no persistent flag / returns one self-contained summary

Rationale: an agent that's mid-turn often needs to fan out a chunk of
self-contained work (research, summarization, code-search) without
flooding its own context. The pattern is well-known — Claude Code's
``Task`` tool, Deep Agents' ``sub_agent`` — and is the design Phase 1
+ Phase 2 of #199 prepared the ground for (compactor frees room,
summarizer can shrink the head, sub-agent isolates the tangent).

What this DOES:
  - Verify caller depth + cycle (reuses DelegateToolService limits +
    parent-run walk so two ways of spawning sub-agents can't bypass
    each other)
  - Build a fresh AgentRunner via ``build_agent_runner_stack`` —
    same wiring chat layer uses for top-level turns, just tagged with
    the parent run id and a ``+1`` depth
  - Compose the sub-agent's system prompt via PromptComposer
  - Run one ``run_turn`` to completion under a child RunRecorder
  - Wire ``agent_runs.parent_run_id`` so Runs tab can render a tree
  - Return a compact envelope (summary / status / sub_run_id / cost)

What this does NOT do (deferred to Phase 5+):
  - Stream sub-agent progress back to the parent agent (D5: v1 returns
    after completion, no mid-flight events)
  - Handle multi-turn sub-conversations — ``run_turn`` runs once
  - Spawn multiple sub-agents in parallel — ``Task(parallel=true,...)``
    is a Phase 6 idea
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from loguru import logger

# Reuse DelegateToolService's depth + rate-limit + cycle helpers so
# the two spawning paths share the same safety net.
from app.services.workforce.delegate_tool import (
    MAX_DELEGATION_DEPTH,
    _check_rate_limit,
)


# Envelope keys returned to the parent agent. Pinned in tests so
# downstream consumers (parent agent prompt, frontend Runs UI) can
# rely on the shape.
ENVELOPE_KEYS = (
    "summary",
    "key_findings",
    "files_created",
    "tokens_used",
    "sub_run_id",
    "status",
)


class SubAgentTaskService:
    """Per-turn service: caller context baked in at construction.

    The parent agent's chat-wiring layer constructs one of these per
    turn (mirroring how DelegateToolService is wired) and hands it to
    the SkillToolService so the ``task`` built-in can dispatch.
    """

    def __init__(
        self,
        *,
        caller_agent_id: UUID,
        caller_user_id: UUID,
        parent_run_id: Optional[UUID],
        agent_depth: int = 0,
        session_id: Optional[UUID] = None,
    ) -> None:
        self.caller_agent_id = caller_agent_id
        self.caller_user_id = caller_user_id
        self.parent_run_id = parent_run_id
        self.agent_depth = agent_depth
        self.session_id = session_id

    async def spawn(self, args: dict[str, Any]) -> dict[str, Any]:
        """Entry point invoked by the ``task`` built-in skill.

        ``args`` schema:
            subagent_type (str, required) — agent slug to spawn
            prompt (str, required)        — user-style instruction to
                                            the sub-agent
            description (str, optional)   — short label, currently
                                            surfaced in run metadata
                                            but not in the LLM call

        Returns an envelope dict with ENVELOPE_KEYS. Errors are
        returned as ``{"status": "failed", "error": ...}`` rather than
        raised so the parent agent's tool loop keeps moving — a
        crashing sub-agent must never crash its parent.
        """
        slug = (args.get("subagent_type") or args.get("agent_slug") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        description = (args.get("description") or "").strip()

        if not slug:
            return self._failed("subagent_type required")
        if not prompt:
            return self._failed("prompt required")

        # Rate limit shares the DelegateTool sliding window per caller
        # — a runaway loop that spawns 30+ sub-agents in 60s gets cut
        # off the same way as runaway Delegate loops.
        rl_error = _check_rate_limit(self.caller_agent_id)
        if rl_error is not None:
            logger.warning(
                "[subagent_task] rate-limit hit caller={} slug={}",
                self.caller_agent_id, slug,
            )
            return self._failed(rl_error.get("error") or "rate limit")

        # Depth check before any DB roundtrip. agent_depth is the
        # CALLER's depth; the sub-agent will run at depth + 1, so we
        # reject when caller is already at the cap.
        if self.agent_depth >= MAX_DELEGATION_DEPTH:
            return self._failed(
                f"max sub-agent depth ({MAX_DELEGATION_DEPTH}) reached "
                f"at caller depth {self.agent_depth}"
            )

        # Reject self-spawn — same agent should branch via plan/loop,
        # not by recursing on itself. Delegate also rejects this; we
        # match for symmetry.
        try:
            from app.repositories.agent_repository import AgentRepository

            target = await AgentRepository().get_by_slug(slug)
        except Exception as exc:
            logger.warning("[subagent_task] agent lookup failed: {}", exc)
            return self._failed(f"agent lookup failed: {exc!s:.120}")

        if not target:
            return self._failed(f"unknown agent slug: {slug!r}")

        target_agent_id = UUID(target["id"])
        if target_agent_id == self.caller_agent_id:
            return self._failed(
                "cannot spawn self as sub-agent; refactor as a plan step"
            )

        # Run the sub-agent. Imports kept local — these pull the full
        # agent_runner stack which we don't want at module-import time
        # for any code path that doesn't actually spawn sub-agents.
        try:
            from app.core.config import settings
            from app.services.ai.adapters.factory import provider_key_for_model
            from app.services.ai.chat.ai_library_chat_wiring import (
                build_agent_runner_stack,
            )
            from app.services.ai.prompts.prompt_composer import (
                ComposerInput,
                PromptComposer,
            )
            from app.services.ai.runner.run_recorder import RunRecorder
            from app.repositories.skill_repository import SkillRepository
            from app.repositories.agent_repository import AgentRepository
            from app.services.workforce.agent_worker import _attach_to_parent_run
        except Exception as exc:
            logger.error("[subagent_task] import wiring failed: {}", exc)
            return self._failed(f"import failed: {exc!s:.120}")

        try:
            stack = await build_agent_runner_stack(
                agent=target,
                skill_repo=SkillRepository(),
                user_id=self.caller_user_id,
                session_id=self.session_id,
                user_query=prompt,
                settings=settings,
                parent_run_id=self.parent_run_id,
                agent_depth=self.agent_depth + 1,
            )
        except Exception as exc:
            logger.warning("[subagent_task] stack build failed: {}", exc)
            return self._failed(f"stack build failed: {exc!s:.120}")

        try:
            composer = PromptComposer(AgentRepository(), SkillRepository())
            composed = await composer.compose(
                ComposerInput(
                    agent_slug=slug,
                    request_instructions=(
                        "You are running as a sub-agent spawned by a parent "
                        "agent that needs a self-contained answer to the "
                        "task below. Produce a complete response — the "
                        "parent only sees your final output, not your "
                        "intermediate steps."
                    ),
                    recalled_memories=stack.recalled_memories,
                )
            )
        except Exception as exc:
            logger.warning("[subagent_task] prompt compose failed: {}", exc)
            return self._failed(f"prompt compose failed: {exc!s:.120}")

        model = composed.model or ""
        try:
            provider = provider_key_for_model(model) if model else None
        except ValueError:
            provider = None

        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=self.caller_user_id,
                trigger="subagent_task",
                session_id=self.session_id,
                team_id=None,
                project_id=None,
                model=model or None,
                provider=provider,
                input_summary=prompt[:240],
                metadata={
                    "subagent_type": slug,
                    "description": description or None,
                    "parent_run_id": str(self.parent_run_id) if self.parent_run_id else None,
                    "agent_depth": self.agent_depth + 1,
                },
            ) as recorder:
                if self.parent_run_id is not None:
                    try:
                        await _attach_to_parent_run(
                            run_id=recorder.run_id,
                            parent_run_id=self.parent_run_id,
                            agent_depth=self.agent_depth + 1,
                        )
                    except Exception as exc:
                        # Non-fatal: parent_run_id is for the Runs tab
                        # tree; losing it doesn't break the sub-run
                        # itself. Log and continue.
                        logger.warning(
                            "[subagent_task] _attach_to_parent_run failed: {}",
                            exc,
                        )

                result = await stack.runner.run_turn(
                    composed,
                    user_messages=[{"role": "user", "content": prompt}],
                    recorder=recorder,
                )

                return self._build_envelope(
                    result=result,
                    sub_run_id=recorder.run_id,
                )
        except Exception as exc:
            logger.warning("[subagent_task] run_turn failed: {}", exc)
            return self._failed(f"sub-agent crashed: {exc!s:.120}")

    @staticmethod
    def _build_envelope(
        *,
        result: dict[str, Any],
        sub_run_id: Any,
    ) -> dict[str, Any]:
        """Map AgentRunner.run_turn's verbose result into the compact
        envelope the parent agent reads. Keep this small — extra
        fields have token cost in the parent's context."""
        content = result.get("content") or ""
        error = result.get("error")
        status = "failed" if error else "success"

        return {
            "summary": content,
            "key_findings": [],
            "files_created": [],
            "tokens_used": (result.get("usage") or {}).get("total_tokens", 0),
            "sub_run_id": str(sub_run_id) if sub_run_id else None,
            "status": status,
            **({"error": error} if error else {}),
        }

    @staticmethod
    def _failed(error_msg: str) -> dict[str, Any]:
        """Standard envelope shape for the never-launched failure
        cases (depth, slug, lookup). Parent agent reads ``status`` and
        ``error`` to decide whether to retry / fall back."""
        return {
            "summary": "",
            "key_findings": [],
            "files_created": [],
            "tokens_used": 0,
            "sub_run_id": None,
            "status": "failed",
            "error": error_msg,
        }


__all__ = [
    "ENVELOPE_KEYS",
    "MAX_DELEGATION_DEPTH",
    "SubAgentTaskService",
]
