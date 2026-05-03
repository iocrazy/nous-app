"""SessionMemory — fixed-schema notes maintained continuously per session.

Wave 5b (B3). The structural primitive layer:
  - SessionMemoryTrigger: dual-threshold decision (when to fire updater)
  - SessionMemorySchema: 6 fixed sections + parser + renderer
  - SessionMemoryUpdater: orchestrates LLM call to maintain the doc

The repo (B2) handles persistence; the chat-side wire-up (B4) does the
fire-and-forget call. This module is DB-agnostic so it's testable
without Supabase.

Why six fixed sections (matches OpenClaw / Claude Code pattern):
  - title:        1-line, evolves only when topic shifts
  - current_state: what user is trying to do RIGHT NOW
  - task_spec:    requirements + constraints + acceptance criteria
  - key_files:    bullet list of paths/symbols touched
  - workflow_steps: 1-N ordered list with status emoji prefix
  - errors_fixes: bullet list of {symptom: fix} pairs
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.agent_framework.tokenizer import count_messages_tokens


# ─── Trigger ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionMemoryTrigger:
    """Dual-threshold decision: should the updater fire NOW?

    Mirrors OpenClaw's (total_tokens reach min) AND
    (delta tokens OR delta tool_calls big enough).
    """

    min_total_tokens: int = 8_000
    delta_tokens: int = 4_000
    delta_tool_calls: int = 5

    def should_update(self, current: "SessionMetrics", baseline: "SessionMetrics") -> bool:
        if current.total_tokens < self.min_total_tokens:
            return False
        token_delta = current.total_tokens - baseline.total_tokens
        tool_delta = current.tool_calls - baseline.tool_calls
        return (
            token_delta >= self.delta_tokens
            or tool_delta >= self.delta_tool_calls
        )


@dataclass(frozen=True)
class SessionMetrics:
    """Snapshot of counts used by the trigger."""

    total_tokens: int = 0
    tool_calls: int = 0
    turn_count: int = 0


def compute_metrics(messages: list[dict], model: str = "") -> SessionMetrics:
    """Compute current SessionMetrics from a messages list."""
    if not messages:
        return SessionMetrics()
    tool_calls = sum(
        len(m.get("tool_calls") or [])
        for m in messages
        if m.get("role") == "assistant"
    )
    return SessionMetrics(
        total_tokens=count_messages_tokens(messages, model),
        tool_calls=tool_calls,
        turn_count=len(messages),
    )


# ─── Schema ───────────────────────────────────────────────────────────


# Section names + the markdown headings that delimit them in the document.
# Order is canonical — renderer always emits in this order.
SECTION_ORDER: tuple[str, ...] = (
    "title",
    "current_state",
    "task_spec",
    "key_files",
    "workflow_steps",
    "errors_fixes",
)

SECTION_HEADINGS: dict[str, str] = {
    "title": "# Session Title",
    "current_state": "# Current Working State",
    "task_spec": "# Task Specification",
    "key_files": "# Key Files / Functions",
    "workflow_steps": "# Workflow Steps",
    "errors_fixes": "# Errors & Fixes",
}

EMPTY_PLACEHOLDER = "(none)"


def render_md(sections: dict[str, str]) -> str:
    """Render a sections dict to canonical markdown. Missing sections get
    `(none)` placeholder — never omitted (so structure is invariant)."""
    parts: list[str] = []
    for key in SECTION_ORDER:
        heading = SECTION_HEADINGS[key]
        body = (sections.get(key) or "").strip() or EMPTY_PLACEHOLDER
        parts.append(f"{heading}\n{body}")
    return "\n\n".join(parts) + "\n"


def parse_md_sections(body_md: str) -> dict[str, str]:
    """Parse a session-memory markdown document back into the 6 fixed
    sections. Tolerant of:
      - Missing sections (returns empty string)
      - Extra/unknown sections (ignored — we only extract the 6 canonical ones)
      - Leading/trailing whitespace
    """
    if not body_md:
        return {key: "" for key in SECTION_ORDER}

    # Build pattern: split on any of our headings. Keeps capture so we
    # know which heading delimited each chunk.
    heading_to_key = {v: k for k, v in SECTION_HEADINGS.items()}
    pattern = "|".join(re.escape(h) for h in SECTION_HEADINGS.values())
    splitter = re.compile(rf"({pattern})", re.MULTILINE)
    parts = splitter.split(body_md)
    # parts: [pre, heading_1, body_1, heading_2, body_2, ...]
    out: dict[str, str] = {key: "" for key in SECTION_ORDER}
    i = 1
    while i < len(parts):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        key = heading_to_key.get(heading)
        if key:
            # Strip leading/trailing whitespace; treat (none) as empty for parsed view
            out[key] = "" if body == EMPTY_PLACEHOLDER else body
        i += 2
    return out


# ─── Update prompt ────────────────────────────────────────────────────


UPDATE_PROMPT_TEMPLATE = """\
You maintain an evolving session memory document for an agent
conversation. Output ONLY the updated document, no commentary.

The document MUST have these 6 sections, in this order, each section
header appearing exactly once:

# Session Title
{a 1-line topic title}

# Current Working State
{what user is trying to do RIGHT NOW}

# Task Specification
{requirements, constraints, acceptance criteria}

# Key Files / Functions
{bulleted: path/to/x.py:func — purpose}

# Workflow Steps
{numbered list with status emoji: ✅ done, 🔄 in progress, ⏸ pending}

# Errors & Fixes
{bulleted: symptom → fix}

If a section has no content, write "(none)" — NEVER omit a section header.

PREVIOUS DOCUMENT:
{previous}

NEW ACTIVITY (since last update):
{new_activity}

UPDATED DOCUMENT (output only the markdown):
"""


def build_update_prompt(*, previous_md: str, new_activity: str) -> str:
    """Render the LLM prompt to maintain the document."""
    prev = previous_md.strip() or "(initial — no previous document)"
    return UPDATE_PROMPT_TEMPLATE.replace(
        "{previous}", prev
    ).replace("{new_activity}", new_activity.strip() or "(none)")


# ─── Updater ──────────────────────────────────────────────────────────


# Caller-injected: takes the prompt, returns the updated markdown.
SessionMemorySummarizer = Callable[[str], Awaitable[str]]


@dataclass
class SessionMemoryService:
    """Stateless orchestrator. Pass-in repo + summarizer so the service
    is independently testable."""

    repo: Any  # SessionMemoryRepository
    summarizer: SessionMemorySummarizer
    trigger: SessionMemoryTrigger = field(default_factory=SessionMemoryTrigger)

    async def maybe_update(
        self,
        session_id: str,
        messages: list[dict],
        *,
        model: str = "",
        force: bool = False,
    ) -> Optional[Any]:
        """If trigger says yes (or ``force=True``), summarize + persist.
        Returns the updated SessionMemoryRow or None when no-op / failed."""
        existing = await self.repo.load(session_id)
        baseline = SessionMetrics(
            total_tokens=existing.tokens_at_last_update if existing else 0,
            tool_calls=existing.tool_calls_at_last_update if existing else 0,
            turn_count=existing.turns_at_last_update if existing else 0,
        )
        current = compute_metrics(messages, model)

        if not force and not self.trigger.should_update(current, baseline):
            return None

        previous_md = (existing.body_md if existing else "") or ""
        new_activity = _render_messages_for_summarizer(
            messages, baseline.turn_count
        )
        prompt = build_update_prompt(
            previous_md=previous_md, new_activity=new_activity
        )

        try:
            updated_md = await self.summarizer(prompt)
        except Exception:
            return None  # best-effort; chat path must not break

        sections = parse_md_sections(updated_md)
        return await self.repo.upsert(
            session_id,
            body_md=updated_md,
            sections_json=sections,
            tokens_at_update=current.total_tokens,
            tool_calls_at_update=current.tool_calls,
            turns_at_update=current.turn_count,
        )


def _render_messages_for_summarizer(
    messages: list[dict], baseline_turn_count: int
) -> str:
    """Convert messages newer than baseline into a compact text block
    for the summarizer prompt. Older messages are assumed already
    captured in the previous_md."""
    new_msgs = messages[baseline_turn_count:] if baseline_turn_count > 0 else messages
    parts = []
    for m in new_msgs:
        role = m.get("role", "?")
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(p.get("text") or p) if isinstance(p, dict) else str(p)
                for p in content
            )
        elif content is None:
            content = ""
        # Cap individual rendered turn so a 50KB tool result doesn't
        # dominate the prompt; first ~500 chars + tag is enough signal.
        text = str(content).strip()
        if len(text) > 500:
            text = text[:500] + f"... [truncated, original {len(content)} chars]"
        parts.append(f"[{role}] {text}")
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = (fn.get("arguments") or "")[:200]
            parts.append(f"[tool_call:{fn.get('name')}] {args}")
    return "\n".join(parts)


__all__ = [
    "EMPTY_PLACEHOLDER",
    "SECTION_HEADINGS",
    "SECTION_ORDER",
    "SessionMemoryService",
    "SessionMemorySummarizer",
    "SessionMemoryTrigger",
    "SessionMetrics",
    "build_update_prompt",
    "compute_metrics",
    "parse_md_sections",
    "render_md",
]
