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
from app.agent_framework.event_loop_ready import (
    measure_drift_ms,
    wait_for_loop_ready,
)
from app.agent_framework.kill_tree import kill_process_tree
from app.agent_framework.lane_queue import (
    Lane,
    LaneQueue,
    LaneTaskTimeout,
)
from app.agent_framework.workflow_timeout_policy import (
    DEFAULT_TIMEOUT_MINUTES,
    is_stuck,
    timeout_for_task_type,
    timeout_minutes,
)
from app.agent_framework.lifecycle_bus import (
    LifecycleBus,
    LifecycleEvent,
    EVT_AGENT_RUN_COMPLETE,
    EVT_AGENT_RUN_START,
    EVT_BOUNDARY_BLOCKED,
    EVT_DEPLOY_COMPLETE,
    EVT_WORKFLOW_COMPLETE,
    EVT_WORKFLOW_FAIL,
    EVT_WORKFLOW_START,
)

__all__ = [
    "AbortController",
    "AllKeysCooledDown",
    "ContextWindowError",
    "ContextWindowWarning",
    "DEFAULT_TIMEOUT_MINUTES",
    "EVT_AGENT_RUN_COMPLETE",
    "EVT_AGENT_RUN_START",
    "EVT_BOUNDARY_BLOCKED",
    "EVT_DEPLOY_COMPLETE",
    "EVT_WORKFLOW_COMPLETE",
    "EVT_WORKFLOW_FAIL",
    "EVT_WORKFLOW_START",
    "KeyRotator",
    "Lane",
    "LaneQueue",
    "LaneTaskTimeout",
    "LifecycleBus",
    "LifecycleEvent",
    "RunAborted",
    "check_context_budget",
    "estimate_tokens",
    "is_stuck",
    "kill_process_tree",
    "measure_drift_ms",
    "model_window_size",
    "race_until_abort",
    "timeout_for_task_type",
    "timeout_minutes",
    "wait_for_loop_ready",
]
