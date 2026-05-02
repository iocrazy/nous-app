"""Agent framework primitives — robustness building blocks for AgentRunner.

Borrowed (and adapted to Python) from OpenClaw's agents/* modules. Each
primitive is an independent concern; agent_runner and llm_retry_middleware
compose them as needed.

Sprint F-1 modules:
- context_window: small-model protection (reject if AGENT spec eats too
  much of the model's window)
- api_key_rotation: same-provider multi-key rotation on 429/auth fail
- abort_controller: per-run asyncio.Event so user "cancel" interrupts
  in-flight LLM call (not just hooks between turns)
- kill_tree: graceful SIGTERM → grace → SIGKILL with Unix process group
  handling, for DBOS workflow cancel killing yt-dlp/ffmpeg/whisper

See docs/architecture/agent-harness.md (TBD) for the layered model.
"""
from app.agent_framework.abort_controller import (
    AbortController,
    RunAborted,
    race_until_abort,
)
from app.agent_framework.context_window import (
    ContextWindowError,
    ContextWindowWarning,
    check_context_budget,
    estimate_tokens,
    model_window_size,
)
from app.agent_framework.key_rotation import (
    AllKeysCooledDown,
    KeyRotator,
)

__all__ = [
    "AbortController",
    "AllKeysCooledDown",
    "ContextWindowError",
    "ContextWindowWarning",
    "KeyRotator",
    "RunAborted",
    "check_context_budget",
    "estimate_tokens",
    "model_window_size",
    "race_until_abort",
]
