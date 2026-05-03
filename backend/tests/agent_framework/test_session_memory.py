"""B3 — SessionMemory primitive: trigger + schema + service."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agent_framework.session_memory import (
    EMPTY_PLACEHOLDER,
    SECTION_ORDER,
    SessionMemoryService,
    SessionMemoryTrigger,
    SessionMetrics,
    build_update_prompt,
    compute_metrics,
    parse_md_sections,
    render_md,
)


# ─── Trigger ──────────────────────────────────────────────────────────


def _metrics(tokens=0, calls=0, turns=0):
    return SessionMetrics(total_tokens=tokens, tool_calls=calls, turn_count=turns)


@pytest.mark.unit
def test_trigger_min_total_gates_everything():
    """Below min_total_tokens → never fire, even with huge delta."""
    t = SessionMemoryTrigger(min_total_tokens=8000, delta_tokens=1)
    current = _metrics(tokens=5000)
    baseline = _metrics(tokens=0)
    assert t.should_update(current, baseline) is False


@pytest.mark.unit
def test_trigger_fires_on_token_delta():
    t = SessionMemoryTrigger(min_total_tokens=1000, delta_tokens=500)
    current = _metrics(tokens=2000)
    baseline = _metrics(tokens=1000)
    assert t.should_update(current, baseline) is True  # delta=1000 ≥ 500


@pytest.mark.unit
def test_trigger_fires_on_tool_call_delta():
    """Even tiny token delta — if tool_calls jumped, fire."""
    t = SessionMemoryTrigger(min_total_tokens=1000, delta_tokens=10000, delta_tool_calls=3)
    current = _metrics(tokens=2000, calls=10)
    baseline = _metrics(tokens=1900, calls=5)
    # token delta=100 < 10000, but tool delta=5 ≥ 3
    assert t.should_update(current, baseline) is True


@pytest.mark.unit
def test_trigger_no_fire_when_no_delta():
    t = SessionMemoryTrigger(min_total_tokens=1000)
    current = _metrics(tokens=2000)
    baseline = _metrics(tokens=2000)
    assert t.should_update(current, baseline) is False


# ─── Metrics ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_compute_metrics_counts_tool_calls():
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "ok"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "2", "function": {"name": "x", "arguments": "{}"}},
                {"id": "3", "function": {"name": "y", "arguments": "{}"}},
            ],
        },
    ]
    m = compute_metrics(msgs)
    assert m.turn_count == 4
    assert m.tool_calls == 3  # 1 + 2


@pytest.mark.unit
def test_compute_metrics_empty():
    assert compute_metrics([]).total_tokens == 0


# ─── Schema render + parse round-trip ─────────────────────────────────


@pytest.mark.unit
def test_render_emits_all_six_sections_in_order():
    md = render_md({})
    headings = [line for line in md.splitlines() if line.startswith("# ")]
    assert len(headings) == 6
    # First section is title
    assert "Session Title" in headings[0]


@pytest.mark.unit
def test_render_uses_placeholder_for_missing_sections():
    md = render_md({"title": "Topic X"})
    assert "Topic X" in md
    assert EMPTY_PLACEHOLDER in md  # other 5 sections empty


@pytest.mark.unit
def test_parse_recovers_sections():
    md = render_md(
        {
            "title": "My Topic",
            "current_state": "Building feature Y",
            "task_spec": "Spec body",
            "key_files": "- a.py\n- b.py",
            "workflow_steps": "1. ✅ done\n2. 🔄 active",
            "errors_fixes": "- err: fix",
        }
    )
    parsed = parse_md_sections(md)
    assert parsed["title"] == "My Topic"
    assert parsed["current_state"] == "Building feature Y"
    assert parsed["key_files"] == "- a.py\n- b.py"


@pytest.mark.unit
def test_parse_handles_empty_input():
    parsed = parse_md_sections("")
    assert all(parsed[k] == "" for k in SECTION_ORDER)


@pytest.mark.unit
def test_parse_treats_placeholder_as_empty():
    """`(none)` placeholder in body → parsed value is empty string."""
    md = render_md({"title": "X"})  # other sections placeholder
    parsed = parse_md_sections(md)
    assert parsed["title"] == "X"
    assert parsed["current_state"] == ""


@pytest.mark.unit
def test_parse_tolerates_extra_sections():
    """Unknown section headers in input → silently ignored."""
    md = render_md({"title": "X"}) + "\n# Some Random Other Header\nfoo bar"
    parsed = parse_md_sections(md)
    assert parsed["title"] == "X"
    # No KeyError, no crash


# ─── Update prompt format ─────────────────────────────────────────────


@pytest.mark.unit
def test_update_prompt_includes_previous_and_new():
    p = build_update_prompt(
        previous_md="# Session Title\nOld topic", new_activity="user said new things"
    )
    assert "Old topic" in p
    assert "user said new things" in p


@pytest.mark.unit
def test_update_prompt_handles_empty_previous():
    p = build_update_prompt(previous_md="", new_activity="hi")
    assert "initial" in p.lower() or "no previous" in p.lower()


# ─── Service end-to-end (mock repo + summarizer) ──────────────────────


@dataclass
class _StoredRow:
    session_id: str
    body_md: str
    tokens_at_last_update: int
    tool_calls_at_last_update: int
    turns_at_last_update: int


class _FakeRepo:
    def __init__(self):
        self._row = None
        self.upsert_calls = 0

    async def load(self, sid):
        return self._row

    async def upsert(self, sid, *, body_md, sections_json, tokens_at_update,
                     tool_calls_at_update, turns_at_update, **kw):
        self.upsert_calls += 1
        self._row = _StoredRow(
            session_id=sid,
            body_md=body_md,
            tokens_at_last_update=tokens_at_update,
            tool_calls_at_last_update=tool_calls_at_update,
            turns_at_last_update=turns_at_update,
        )
        return self._row


@pytest.mark.asyncio
async def test_service_does_not_fire_below_threshold():
    repo = _FakeRepo()

    async def _summ(prompt):
        return "should not be called"

    svc = SessionMemoryService(
        repo=repo,
        summarizer=_summ,
        trigger=SessionMemoryTrigger(min_total_tokens=10_000),
    )
    msgs = [{"role": "user", "content": "short"}]
    out = await svc.maybe_update("sess-1", msgs)
    assert out is None
    assert repo.upsert_calls == 0


@pytest.mark.asyncio
async def test_service_fires_above_threshold_and_persists():
    repo = _FakeRepo()
    captured = {}

    async def _summ(prompt):
        captured["prompt"] = prompt
        return render_md({"title": "Updated topic"})

    svc = SessionMemoryService(
        repo=repo,
        summarizer=_summ,
        trigger=SessionMemoryTrigger(min_total_tokens=100, delta_tokens=10),
    )
    # 200 chars = 50 tokens (heuristic) + per-msg overhead → above 100 threshold
    msgs = [{"role": "user", "content": "x" * 4000}]
    out = await svc.maybe_update("sess-1", msgs)
    assert out is not None
    assert repo.upsert_calls == 1
    assert "Updated topic" in repo._row.body_md


@pytest.mark.asyncio
async def test_service_force_overrides_trigger():
    """force=True ignores trigger gating."""
    repo = _FakeRepo()

    async def _summ(prompt):
        return render_md({"title": "Forced"})

    svc = SessionMemoryService(
        repo=repo,
        summarizer=_summ,
        trigger=SessionMemoryTrigger(min_total_tokens=10_000_000),  # never naturally fires
    )
    out = await svc.maybe_update("sess-1", [{"role": "user", "content": "tiny"}], force=True)
    assert out is not None
    assert "Forced" in repo._row.body_md


@pytest.mark.asyncio
async def test_service_summarizer_failure_returns_none():
    """Cheap LLM crashed → service returns None silently. Chat must keep working."""
    repo = _FakeRepo()

    async def _summ(prompt):
        raise RuntimeError("LLM down")

    svc = SessionMemoryService(
        repo=repo,
        summarizer=_summ,
        trigger=SessionMemoryTrigger(min_total_tokens=10),
    )
    out = await svc.maybe_update("sess-1", [{"role": "user", "content": "x" * 100}])
    assert out is None
    assert repo.upsert_calls == 0


@pytest.mark.asyncio
async def test_service_uses_baseline_for_delta():
    """First call fires; second call with same messages → baseline already
    matches current → no delta → no-op."""
    repo = _FakeRepo()

    async def _summ(prompt):
        return render_md({"title": "v1"})

    # Low thresholds so first call fires
    svc = SessionMemoryService(
        repo=repo,
        summarizer=_summ,
        trigger=SessionMemoryTrigger(min_total_tokens=10, delta_tokens=10),
    )
    msgs = [{"role": "user", "content": "x" * 200}]
    await svc.maybe_update("sess-1", msgs)
    assert repo.upsert_calls == 1
    # Second call with SAME messages — baseline matches current → no delta → no-op
    await svc.maybe_update("sess-1", msgs)
    assert repo.upsert_calls == 1
