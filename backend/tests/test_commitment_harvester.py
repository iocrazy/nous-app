"""E1 — commitment harvester: pre-filter + LLM extract + persist."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.ai.runner.commitment_harvester import (
    HarvestContext,
    HarvestedCommitment,
    HarvestResult,
    build_harvest_prompt,
    harvest_commitments,
    has_commitment_cues,
    parse_harvest_output,
)


# ─── has_commitment_cues ─────────────────────────────────────────────


@pytest.mark.unit
def test_cues_english_will():
    assert has_commitment_cues("I will check on this tomorrow")


@pytest.mark.unit
def test_cues_english_ill():
    assert has_commitment_cues("I'll remind you when the build finishes")


@pytest.mark.unit
def test_cues_chinese():
    assert has_commitment_cues("我会在下周提醒你")
    assert has_commitment_cues("明天再来跟你说")


@pytest.mark.unit
def test_no_cues_in_plain_response():
    assert has_commitment_cues("The capital of France is Paris.") is False


@pytest.mark.unit
def test_no_cues_in_empty():
    assert has_commitment_cues("") is False
    assert has_commitment_cues(None) is False  # type: ignore[arg-type]


# ─── build_harvest_prompt ────────────────────────────────────────────


@pytest.mark.unit
def test_prompt_includes_response_and_now():
    now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
    p = build_harvest_prompt(
        response_text="I will check tomorrow", now=now
    )
    assert "I will check tomorrow" in p
    assert "2026-05-03" in p
    # Mentions the trigger types
    assert "time" in p
    assert "event" in p
    assert "next_session" in p


# ─── parse_harvest_output ────────────────────────────────────────────


@pytest.mark.unit
def test_parse_empty_array():
    assert parse_harvest_output("[]") == []


@pytest.mark.unit
def test_parse_single_time_commitment():
    raw = '''[{"description": "remind user", "trigger_type": "time", "trigger_at": "2026-05-04T09:00:00+00:00"}]'''
    out = parse_harvest_output(raw)
    assert len(out) == 1
    assert out[0].description == "remind user"
    assert out[0].trigger_type == "time"
    assert out[0].trigger_at == datetime(2026, 5, 4, 9, 0, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_parse_event_commitment():
    raw = '[{"description": "ping when build done", "trigger_type": "event", "trigger_event": "build.finished:42"}]'
    out = parse_harvest_output(raw)
    assert len(out) == 1
    assert out[0].trigger_event == "build.finished:42"


@pytest.mark.unit
def test_parse_next_session_commitment():
    raw = '[{"description": "follow up next time", "trigger_type": "next_session"}]'
    out = parse_harvest_output(raw)
    assert len(out) == 1
    assert out[0].trigger_type == "next_session"
    assert out[0].trigger_at is None


@pytest.mark.unit
def test_parse_strips_markdown_fence():
    """LLMs sometimes wrap in ```json ``` despite instructions."""
    raw = '''```json
[{"description": "remind user", "trigger_type": "next_session"}]
```'''
    out = parse_harvest_output(raw)
    assert len(out) == 1


@pytest.mark.unit
def test_parse_extracts_array_from_trailing_prose():
    """LLM ignored 'no commentary' rule — extract the array anyway."""
    raw = 'Sure, here you go:\n[{"description": "remind", "trigger_type": "next_session"}]\nLet me know if more.'
    out = parse_harvest_output(raw)
    assert len(out) == 1


@pytest.mark.unit
def test_parse_skips_invalid_trigger_type():
    raw = '[{"description": "x", "trigger_type": "fictional"}]'
    assert parse_harvest_output(raw) == []


@pytest.mark.unit
def test_parse_demotes_time_without_trigger_at_to_next_session():
    """R3: a 'time' commitment without a concrete trigger_at is demoted
    to next_session rather than dropped — preserves user intent and
    routes it through G8 in-app delivery."""
    raw = '[{"description": "x", "trigger_type": "time"}]'
    out = parse_harvest_output(raw)
    assert len(out) == 1
    assert out[0].trigger_type == "next_session"
    assert out[0].trigger_at is None


@pytest.mark.unit
def test_parse_demotes_time_with_malformed_trigger_at_to_next_session():
    """R3: malformed trigger_at on a 'time' commit also demoted, not dropped."""
    raw = '[{"description": "x", "trigger_type": "time", "trigger_at": "tomorrow morning"}]'
    out = parse_harvest_output(raw)
    assert len(out) == 1
    assert out[0].trigger_type == "next_session"
    assert out[0].trigger_at is None


@pytest.mark.unit
def test_parse_skips_event_without_trigger_event():
    raw = '[{"description": "x", "trigger_type": "event"}]'
    assert parse_harvest_output(raw) == []


@pytest.mark.unit
def test_parse_garbled_returns_empty():
    assert parse_harvest_output("not json at all") == []
    assert parse_harvest_output("") == []


@pytest.mark.unit
def test_parse_object_root_returns_empty():
    """Wrong-shape (object instead of array) → empty, not crash."""
    raw = '{"description": "x"}'
    assert parse_harvest_output(raw) == []


# ─── harvest_commitments orchestration ────────────────────────────────


_CTX = HarvestContext(
    agent_id="00000000-0000-0000-0000-000000000001",
    user_id="00000000-0000-0000-0000-000000000002",
    session_id="00000000-0000-0000-0000-000000000003",
)


@pytest.mark.asyncio
async def test_harvest_skips_when_no_cues():
    """Pre-filter miss → no LLM call, no persist."""
    summ_called = {"n": 0}

    async def _summ(prompt):
        summ_called["n"] += 1
        return ""

    async def _persist(c, ctx):
        return "id"

    result = await harvest_commitments(
        response_text="The capital of France is Paris.",
        context=_CTX,
        summarizer=_summ,
        persistor=_persist,
    )
    assert result.candidate_text_had_cues is False
    assert result.commitments_extracted == 0
    assert summ_called["n"] == 0


@pytest.mark.asyncio
async def test_harvest_extracts_and_persists():
    async def _summ(prompt):
        return '[{"description": "remind tomorrow", "trigger_type": "next_session"}]'

    persisted = []

    async def _persist(c, ctx):
        persisted.append(c)
        return "new-commitment-id"

    result = await harvest_commitments(
        response_text="I will follow up tomorrow",
        context=_CTX,
        summarizer=_summ,
        persistor=_persist,
    )
    assert result.candidate_text_had_cues is True
    assert result.commitments_extracted == 1
    assert result.commitments_persisted == 1
    assert result.commitment_ids == ["new-commitment-id"]
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_harvest_summarizer_failure_returns_error():
    async def _broken(prompt):
        raise RuntimeError("LLM down")

    async def _persist(c, ctx):
        return "id"

    result = await harvest_commitments(
        response_text="I will check tomorrow",
        context=_CTX,
        summarizer=_broken,
        persistor=_persist,
    )
    assert result.candidate_text_had_cues is True
    assert result.commitments_extracted == 0
    assert "RuntimeError" in (result.error or "")


@pytest.mark.asyncio
async def test_harvest_one_persistor_failure_doesnt_sink_others():
    async def _summ(prompt):
        return '[{"description": "a", "trigger_type": "next_session"}, {"description": "b", "trigger_type": "next_session"}]'

    persist_count = {"n": 0}

    async def _flaky(c, ctx):
        persist_count["n"] += 1
        if c.description == "a":
            raise RuntimeError("first fails")
        return "id-b"

    result = await harvest_commitments(
        response_text="I will follow up",
        context=_CTX,
        summarizer=_summ,
        persistor=_flaky,
    )
    assert result.commitments_extracted == 2
    assert result.commitments_persisted == 1
    assert result.commitment_ids == ["id-b"]


@pytest.mark.asyncio
async def test_harvest_returns_immediately_on_no_cues_no_summarizer_invoked():
    """The 95% case: most chat turns DON'T have commitment language —
    cheap pre-filter saves money by skipping the LLM entirely."""
    counters = {"summ": 0, "persist": 0}

    async def _summ(p):
        counters["summ"] += 1
        return "[]"

    async def _persist(c, ctx):
        counters["persist"] += 1
        return "id"

    result = await harvest_commitments(
        response_text="hello world",
        context=_CTX,
        summarizer=_summ,
        persistor=_persist,
    )
    assert counters == {"summ": 0, "persist": 0}
    assert result.candidate_text_had_cues is False
