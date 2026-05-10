"""Unit tests for memory extractors — dual-prompt isolation."""

from __future__ import annotations

import json

import pytest

from app.services.ai.memory import ExtractedFrom
from app.services.ai.memory.extractor import (
    AssistantMemoryExtractor,
    UserMemoryExtractor,
    _parse_facts,
)


def _ok_response(facts: list[dict]) -> str:
    return json.dumps({"facts": facts})


# ---------------------------------------------------------------------------
# UserMemoryExtractor
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_extractor_returns_parsed_facts():
    captured_prompt: dict = {}

    async def fake_llm(prompt: str) -> str:
        captured_prompt["text"] = prompt
        return _ok_response(
            [
                {
                    "summary": "User prefers short replies",
                    "when_to_use": "When drafting responses",
                },
                {
                    "summary": "User works in marketing",
                    "when_to_use": "When choosing tone",
                },
            ]
        )

    extractor = UserMemoryExtractor(llm_call=fake_llm)
    facts = await extractor.extract(["I want shorter answers", "I'm in marketing"])

    assert len(facts) == 2
    assert facts[0].summary == "User prefers short replies"
    assert facts[0].extracted_from == ExtractedFrom.USER_MSG
    assert "USER MESSAGES:" in captured_prompt["text"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_extractor_empty_list_skips_llm():
    calls = []

    async def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return _ok_response([])

    extractor = UserMemoryExtractor(llm_call=fake_llm)
    facts = await extractor.extract([])

    assert facts == []
    assert calls == []  # LLM not called for empty input


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_extractor_llm_failure_returns_empty():
    async def broken_llm(prompt: str) -> str:
        raise RuntimeError("LLM down")

    extractor = UserMemoryExtractor(llm_call=broken_llm)
    facts = await extractor.extract(["something"])

    assert facts == []


# ---------------------------------------------------------------------------
# AssistantMemoryExtractor — dual-prompt isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_assistant_extractor_marks_assistant_msg_source():
    async def fake_llm(prompt: str) -> str:
        return _ok_response(
            [
                {
                    "summary": "Used cinematic tone",
                    "when_to_use": "Future scenes for this user",
                }
            ]
        )

    extractor = AssistantMemoryExtractor(llm_call=fake_llm)
    facts = await extractor.extract(["I crafted a cinematic opening"])

    assert facts[0].extracted_from == ExtractedFrom.ASSISTANT_MSG


@pytest.mark.unit
@pytest.mark.asyncio
async def test_assistant_extractor_uses_distinct_prompt():
    """Verify the two extractors have different prompts (dual-prompt isolation)."""
    user_prompt = []
    asst_prompt = []

    async def user_llm(prompt: str) -> str:
        user_prompt.append(prompt)
        return _ok_response([])

    async def asst_llm(prompt: str) -> str:
        asst_prompt.append(prompt)
        return _ok_response([])

    await UserMemoryExtractor(llm_call=user_llm).extract(["msg"])
    await AssistantMemoryExtractor(llm_call=asst_llm).extract(["msg"])

    assert "USER MESSAGES" in user_prompt[0]
    assert "ASSISTANT MESSAGES" in asst_prompt[0]
    assert user_prompt[0] != asst_prompt[0]


# ---------------------------------------------------------------------------
# JSON parsing tolerance
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_facts_accepts_clean_json():
    raw = json.dumps({"facts": [{"summary": "x", "when_to_use": "y"}]})
    facts = _parse_facts(raw, ExtractedFrom.USER_MSG)
    assert len(facts) == 1
    assert facts[0].summary == "x"


@pytest.mark.unit
def test_parse_facts_strips_markdown_fences():
    raw = (
        "```json\n"
        + json.dumps({"facts": [{"summary": "x", "when_to_use": "y"}]})
        + "\n```"
    )
    facts = _parse_facts(raw, ExtractedFrom.USER_MSG)
    assert len(facts) == 1


@pytest.mark.unit
def test_parse_facts_drops_malformed_items():
    raw = json.dumps(
        {
            "facts": [
                {"summary": "ok", "when_to_use": "ok"},
                {"summary": "", "when_to_use": "no summary"},
                {"summary": "no when", "when_to_use": ""},
                "not even a dict",
            ]
        }
    )
    facts = _parse_facts(raw, ExtractedFrom.USER_MSG)
    assert len(facts) == 1


@pytest.mark.unit
def test_parse_facts_invalid_json_returns_empty():
    facts = _parse_facts("not json {[", ExtractedFrom.USER_MSG)
    assert facts == []


@pytest.mark.unit
def test_parse_facts_missing_facts_key_returns_empty():
    facts = _parse_facts(json.dumps({"other": "thing"}), ExtractedFrom.USER_MSG)
    assert facts == []


@pytest.mark.unit
def test_parse_facts_caps_summary_length():
    huge = "x" * 2000
    raw = json.dumps({"facts": [{"summary": huge, "when_to_use": huge}]})
    facts = _parse_facts(raw, ExtractedFrom.USER_MSG)
    assert len(facts[0].summary) == 500
    assert len(facts[0].when_to_use) == 500
