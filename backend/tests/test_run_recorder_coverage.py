"""Regression test: detect new LLM-adapter call-sites that bypass RunRecorder.

The telemetry pipeline relies on all LLM invocations flowing through either
AgentRunner (which accepts a RunRecorder) or VisualAnalysisService (which
wraps its direct OpenAI call in RunRecorder). Any new code that calls an
adapter directly without recorder plumbing creates a blind spot in the
agent_runs table.

This test greps the backend for direct LLM-call shapes and compares against
a known-exempt allow-list. A new bypass fails the test and forces a human
to explicitly add it to ALLOWED_BYPASS_PATHS or wire it through RunRecorder.

Update ALLOWED_BYPASS_PATHS when:
- You intentionally add a new telemetry-exempt path (and document why)
- A file gets renamed (update the path)

Do NOT update it just to make the test pass without addressing the bypass.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"

# Files that are permitted to call LLM adapters without RunRecorder,
# with a documented reason for each. The regression test treats any
# other occurrence as a failure.
ALLOWED_BYPASS_PATHS: dict[str, str] = {
    # RunRecorder is the telemetry anchor itself — no recorder to wrap.
    "services/run_recorder.py": "telemetry implementation",
    # AgentRunner drives the adapter — callers wrap the whole runner in
    # a RunRecorder context, not each adapter.call inside.
    "services/agent_runner.py": "adapter.call inside recorder-aware runner",
    # Adapter implementations themselves call OpenAI / Anthropic SDKs.
    "services/ai_adapters/": "adapter SDK internals",
    "services/ai_provider.py": "adapter re-exports + legacy factory",
    # VisualAnalysisService calls OpenAI directly for image multimodal;
    # both analyze_l1 and analyze_l2 wrap the call in RunRecorder context.
    "services/visual_analysis_service.py": "image multimodal, wrapped in RunRecorder",
    # Embedding service calls OpenAI embeddings API, not chat completions
    # — separate concern from agent telemetry.
    "services/embedding_service.py": "embeddings API, not chat completions",
    # ASR: speech-to-text via Volcengine, not an LLM chat completion.
    "services/volcengine_asr_service.py": "ASR API, not an agent invocation",
    # M1.A retry middleware: wraps adapter.call() for callers that DO
    # supply a RunRecorder via AgentRunner. The middleware itself is
    # recorder-agnostic; coverage is enforced one level up.
    "services/llm_retry_middleware.py": "retry wrapper, recorder lives in caller",
    # M1.A FALLBACK chain: walks fallback_models list, each attempt goes
    # through LLMRetryMiddleware. Same coverage shape as the retry MW.
    "services/llm_fallback_chain.py": "fallback chain, recorder lives in caller",
    # Sprint 2 #5: RotatingAdapter wraps N single-key adapters and
    # rotates on 429/auth fail. Same coverage shape as fallback chain
    # — each wrapped adapter still goes through LLMRetryMiddleware
    # when the caller wraps the RotatingAdapter in AgentRunner.
    "agent_framework/rotating_adapter.py": "key rotation wrapper, recorder lives in caller",
    # M1.B memory writer Celery task: runs OFF the chat path, no chat
    # session to record against. Memory extraction LLM calls are tracked
    # via Celery task metrics, not RunRecorder.
    "tasks/memory_tasks.py": "memory extraction in Celery task, off-chat-path",
    # PR-D3c: DBOS port of memory_tasks. Same justification as the
    # Celery original — workflow_id memoization + DBOS workflow status
    # provide telemetry separately from RunRecorder.
    "workflows/write_memory.py": "memory extraction DBOS workflow, off-chat-path",
    # PR-D3c (PoC #8): DBOS port of the ai_summary Celery task. The
    # OpenAI-compatible call lives directly in the workflow's @DBOS.step
    # for retry control. Telemetry comes from DBOS workflow status +
    # the ai_rewrite_text persistence row, not RunRecorder.
    "workflows/ai_summary.py": "summary DBOS workflow, off-chat-path",
    # M1.5 wiring: cheap-model auxiliary LLM call for memory ranker.
    # Side-channel from the main agent run; cost tracked separately.
    "services/ai_library_chat_wiring.py": "memory ranker auxiliary LLM, side-channel",
    # M1.5 chat compactor: cheap-model summarizer for history compaction.
    # Side-channel from the main agent run; cost tracked separately.
    "services/ai_library_chat_service.py": "compaction summariser auxiliary LLM, side-channel",
    # Wave 5b (B4) session-memory updater: cheap-model maintenance call
    # for the running session-memory.md document. Off-chat-path,
    # fire-and-forget — telemetry tracked via session_memory.version
    # bumps + last_updated_at, not RunRecorder.
    "services/session_memory_runner.py": "session-memory maintenance auxiliary LLM, fire-and-forget",
    # Wave F (F6) memory consolidation sweeper: weekly DBOS workflow.
    # cheap-model merges similar memories into super-memories. Off-chat-
    # path, scheduled job — telemetry via DBOS workflow status.
    "workflows/scheduled_memory_consolidation.py": "memory consolidation auxiliary LLM, scheduled DBOS workflow",
}

# Patterns that indicate a direct LLM call. If any of these appear in a
# file not in ALLOWED_BYPASS_PATHS, the test fails.
DIRECT_LLM_CALL_PATTERNS = [
    re.compile(r"\badapter\.call\("),
    re.compile(r"\.chat\.completions\.create\("),
    re.compile(r"\.messages\.create\("),  # Anthropic SDK
]


def _is_exempt(rel_path: str) -> bool:
    """True if rel_path (relative to backend/app) matches an allowed bypass."""
    for allowed in ALLOWED_BYPASS_PATHS:
        if rel_path == allowed or rel_path.startswith(allowed.rstrip("/") + "/"):
            return True
    return False


def test_no_direct_llm_calls_outside_run_recorder() -> None:
    """Fail if any backend .py file makes a direct LLM adapter call outside
    the allow-list. New bypasses must be either wired through RunRecorder
    or explicitly added to ALLOWED_BYPASS_PATHS with a justification."""
    offenders: list[tuple[str, int, str]] = []

    for py_file in BACKEND_APP.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_APP).as_posix()
        if _is_exempt(rel):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            # Skip comments — false positives for doc references.
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for pattern in DIRECT_LLM_CALL_PATTERNS:
                if pattern.search(line):
                    offenders.append((rel, line_no, line.strip()))
                    break

    if offenders:
        lines = [
            f"  {path}:{lineno}  →  {text}"
            for path, lineno, text in offenders
        ]
        msg = (
            "Direct LLM adapter calls outside RunRecorder coverage:\n"
            + "\n".join(lines)
            + "\n\nEither route the call through AgentRunner (with a RunRecorder) "
            "or add the file to ALLOWED_BYPASS_PATHS in this test with a justification."
        )
        raise AssertionError(msg)
