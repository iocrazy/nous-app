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
from app.agent_framework.agent_todo import (
    AgentTodoList,
    TodoItem,
    TodoStatus,
    TodoValidationError,
)
from app.agent_framework.bounds import (
    BoundsAdvertisement,
    BoundsRegistry,
)
from app.agent_framework.bounds_inventory import (
    inventory_agent_slugs,
    inventory_providers,
    inventory_workflow_names,
    merge_lane_capacity,
)
from app.agent_framework.bounds_redis import RedisBoundsRegistry
from app.agent_framework.cancel_watcher import watch_cancel_loop
from app.agent_framework.commitments import (
    Commitment,
    CommitmentStatus,
    InvalidCommitmentError,
    TriggerType,
)
from app.agent_framework.context_compactor import (
    CompactionStats,
    CompactionThresholds,
    CompactionTier,
    ContextCompactor,
)
from app.agent_framework.context_engine import (
    ContextEngine,
    ContextEngineRegistry,
    ContextPayload,
    DuplicateContextEngineError,
)
from app.agent_framework.context_window import (
    ContextWindowError,
    ContextWindowWarning,
    check_context_budget,
    estimate_tokens,
    model_window_size,
)
from app.agent_framework.db_pool_probe import (
    DbPoolCapacityReport,
)
from app.agent_framework.db_pool_probe import (
    log_capacity_report as log_db_pool_capacity,
)
from app.agent_framework.db_pool_probe import (
    probe_db_pool_capacity,
)
from app.agent_framework.event_loop_ready import (
    measure_drift_ms,
    wait_for_loop_ready,
)
from app.agent_framework.hooks_bridge import (
    LegacyPostToolUseHook,
    LegacyPreToolUseHook,
    wrap_legacy_post,
    wrap_legacy_pre,
)
from app.agent_framework.hooks_protocol import (
    DuplicateHookError,
    Hook,
    HookContext,
    HookDecision,
    HookEvent,
    HookRegistry,
    HookResult,
)
from app.agent_framework.key_rotation import (
    AllKeysCooledDown,
    KeyRotator,
)
from app.agent_framework.kill_tree import kill_process_tree
from app.agent_framework.lane_dispatch import (
    Origin,
    classify_lane,
    dispatch_in_lane,
)
from app.agent_framework.lane_queue import (
    Lane,
    LaneQueue,
    LaneTaskTimeout,
)
from app.agent_framework.lifecycle_bus import (
    EVT_AGENT_RUN_COMPLETE,
    EVT_AGENT_RUN_START,
    EVT_BOUNDARY_BLOCKED,
    EVT_DEPLOY_COMPLETE,
    EVT_WORKFLOW_COMPLETE,
    EVT_WORKFLOW_FAIL,
    EVT_WORKFLOW_START,
    LifecycleBus,
    LifecycleEvent,
)
from app.agent_framework.lifecycle_bus_redis import RedisLifecycleBus
from app.agent_framework.loop_guard import ToolCallLoopGuard
from app.agent_framework.mcp_client import (
    MCPClient,
    MCPClientError,
    MCPServerConfig,
)
from app.agent_framework.mcp_descriptor import (
    DuplicateToolError,
    MCPToolRegistry,
    Tool,
    ToolCallResult,
    ToolInputSchema,
    agent_to_tool,
    skill_to_tool,
)
from app.agent_framework.mcp_outbound_registry import (
    MCPOutboundRegistry,
    QualifiedTool,
)
from app.agent_framework.mcp_stdio import serve as serve_mcp_stdio
from app.agent_framework.message_truncation import (
    DEFAULT_PER_MESSAGE_TOKEN_CAP,
    TruncationOutcome,
    cap_message_tokens,
    cap_messages_tokens,
)
from app.agent_framework.model_health import (
    ModelHealth,
    ModelHealthRegistry,
)
from app.agent_framework.model_health_redis import RedisModelHealthRegistry
from app.agent_framework.multimodal import (
    Attachment,
    AttachmentKind,
)
from app.agent_framework.multimodal import (
    build_user_message as build_multimodal_user_message,
)
from app.agent_framework.multimodal import flatten_to_text as flatten_multimodal_to_text
from app.agent_framework.output_budget import (
    OutputBudget,
    derive_output_budget,
    should_auto_continue,
)
from app.agent_framework.plan_mode import (
    ApprovalDecision,
    PlanMode,
    PlanStep,
    PlanValidationError,
    ProposedPlan,
    build_plan_prompt,
    parse_approval,
    parse_plan_response,
    render_plan_for_user,
)
from app.agent_framework.process_lifecycle import install_cleanup_handlers
from app.agent_framework.prometheus_exporter import render_prometheus
from app.agent_framework.prometheus_pusher import (
    PrometheusPusher,
)
from app.agent_framework.prometheus_pusher import from_env as prometheus_pusher_from_env
from app.agent_framework.role import ProcessRole, role_from_env
from app.agent_framework.root_abort_registry import RootAbortRegistry
from app.agent_framework.rotating_adapter import RotatingAdapter
from app.agent_framework.session_memory import (
    SECTION_ORDER,
    SessionMemoryService,
    SessionMemoryTrigger,
    SessionMetrics,
)
from app.agent_framework.session_memory import (
    build_update_prompt as build_session_memory_update_prompt,
)
from app.agent_framework.session_memory import (
    compute_metrics as compute_session_metrics,
)
from app.agent_framework.session_memory import (
    parse_md_sections as parse_session_memory_sections,
)
from app.agent_framework.session_memory import render_md as render_session_memory
from app.agent_framework.subprocess_registry import (
    cancel_workflow_subprocesses,
    register_subprocess,
    registered_pids,
    unregister_subprocess,
)
from app.agent_framework.telemetry import COUNTER_NAMES, AgentMetrics
from app.agent_framework.tokenizer import (
    count_messages_tokens,
    count_tokens,
)
from app.agent_framework.tool_result_cache import (
    DEFAULT_MAX_ENTRIES as DEFAULT_TOOL_CACHE_MAX_ENTRIES,
)
from app.agent_framework.tool_result_cache import (
    DEFAULT_TTL_SECONDS as DEFAULT_TOOL_CACHE_TTL_SECONDS,
)
from app.agent_framework.tool_result_cache import (
    ToolResultCache,
)
from app.agent_framework.tool_result_pruner import (
    PruneStats,
    age_old_tool_results,
    dedupe_tool_results,
)
from app.agent_framework.tool_result_pruner import prune as prune_tool_results
from app.agent_framework.workflow_timeout_policy import (
    DEFAULT_TIMEOUT_MINUTES,
    is_stuck,
    timeout_for_task_type,
    timeout_minutes,
)

__all__ = [
    "AbortController",
    "AgentMetrics",
    "AllKeysCooledDown",
    "BoundsAdvertisement",
    "BoundsRegistry",
    "COUNTER_NAMES",
    "Commitment",
    "CommitmentStatus",
    "CompactionStats",
    "CompactionThresholds",
    "CompactionTier",
    "ContextCompactor",
    "ContextEngine",
    "ContextEngineRegistry",
    "ContextPayload",
    "DuplicateContextEngineError",
    "DuplicateHookError",
    "DuplicateToolError",
    "ContextWindowError",
    "ContextWindowWarning",
    "DEFAULT_PER_MESSAGE_TOKEN_CAP",
    "DEFAULT_TIMEOUT_MINUTES",
    "EVT_AGENT_RUN_COMPLETE",
    "EVT_AGENT_RUN_START",
    "EVT_BOUNDARY_BLOCKED",
    "EVT_DEPLOY_COMPLETE",
    "EVT_WORKFLOW_COMPLETE",
    "EVT_WORKFLOW_FAIL",
    "EVT_WORKFLOW_START",
    "Hook",
    "HookContext",
    "HookDecision",
    "HookEvent",
    "HookRegistry",
    "HookResult",
    "InvalidCommitmentError",
    "KeyRotator",
    "Lane",
    "LaneQueue",
    "LaneTaskTimeout",
    "LegacyPostToolUseHook",
    "LegacyPreToolUseHook",
    "LifecycleBus",
    "LifecycleEvent",
    "MCPToolRegistry",
    "ModelHealth",
    "ModelHealthRegistry",
    "Origin",
    "OutputBudget",
    "ProcessRole",
    "PruneStats",
    "RedisBoundsRegistry",
    "RootAbortRegistry",
    "RotatingAdapter",
    "RunAborted",
    "SECTION_ORDER",
    "SessionMemoryService",
    "SessionMemoryTrigger",
    "SessionMetrics",
    "Tool",
    "ToolCallLoopGuard",
    "TruncationOutcome",
    "ToolCallResult",
    "ToolInputSchema",
    "TriggerType",
    "age_old_tool_results",
    "agent_to_tool",
    "build_session_memory_update_prompt",
    "cap_message_tokens",
    "cap_messages_tokens",
    "compute_session_metrics",
    "count_messages_tokens",
    "count_tokens",
    "dedupe_tool_results",
    "derive_output_budget",
    "parse_session_memory_sections",
    "inventory_agent_slugs",
    "inventory_providers",
    "inventory_workflow_names",
    "merge_lane_capacity",
    "prune_tool_results",
    "render_session_memory",
    "role_from_env",
    "should_auto_continue",
    "wrap_legacy_post",
    "wrap_legacy_pre",
    "serve_mcp_stdio",
    "skill_to_tool",
    "cancel_workflow_subprocesses",
    "check_context_budget",
    "classify_lane",
    "dispatch_in_lane",
    "estimate_tokens",
    "is_stuck",
    "kill_process_tree",
    "measure_drift_ms",
    "model_window_size",
    "race_until_abort",
    "register_subprocess",
    "registered_pids",
    "timeout_for_task_type",
    "timeout_minutes",
    "unregister_subprocess",
    "wait_for_loop_ready",
    "watch_cancel_loop",
]
