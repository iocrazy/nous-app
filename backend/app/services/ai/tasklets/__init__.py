"""Tasklet — single-turn cheap-model micro-LLM-calls.

Phase 0.5-B of Canvas + AI Infrastructure 17-week upgrade plan. See
``docs/plans/canvas-ai-upgrade-plan.md`` v1.2 §0.5 baseline + §5 Phase 0.5.

A **Tasklet** is the bottom-tier unit of LLM work:

- Single LLM call
- Fixed system prompt
- Optional JSON schema for structured output
- Cheap default model (``qwen-turbo``)
- No multi-turn loops, no tool use, no subagent dispatch

Used for: title generation, intent classification, tag inference, routing
decisions, sentiment checks, prompt rewriting, summarisation, key/entity
extraction. Anything that fits "give LLM one short context and get one
short structured answer back".

10k-user economics (canvas-ai-upgrade-plan v1.2 §0.5):
- qwen-turbo  ~¥0.0003/k in + ¥0.0006/k out → ≈ ¥0.00012 / call
- qwen-plus   ~¥0.004/k in + ¥0.012/k out  → ≈ ¥0.0020  / call
- 16x cheaper on classification tasks; saves ~¥56k/month at 10k users.

This module complements (does not replace) :mod:`app.services.memory.extractor`
which has its own ``LLMCall`` type for memory-specific extraction; a future
refactor may have the extractors subclass :class:`Tasklet`.

Public surface:
- :class:`Tasklet` — base class
- :mod:`builtins` — 10 ready-to-use tasklets (title_generator etc)
"""

from __future__ import annotations

from app.services.ai.tasklets.base import (
    Tasklet,
    TaskletResult,
    parse_json_response,
)

__all__ = [
    "Tasklet",
    "TaskletResult",
    "parse_json_response",
]
