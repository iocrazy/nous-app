import json

import pytest

from app.services.topics.topic_scorer import TopicScorerService


def test_normalize_result_clamps_and_validates():
    svc = TopicScorerService()
    raw = [
        {
            "i": 0,
            "score": 1.5,  # clamp -> 1.0
            "reason": "  好东西  ",
            "ai_summary": "摘要",
            "category": "model",
            "tags": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],  # cap 8
        },
        {
            "i": 1,
            "score": "not-a-number",  # -> None
            "category": "bogus",  # invalid -> None
            "tags": "notalist",  # -> []
        },
    ]
    out = svc.normalize_result(raw)
    assert out[0]["score"] == 1.0
    assert out[0]["reason"] == "好东西"  # trimmed
    assert out[0]["category"] == "model"
    assert len(out[0]["tags"]) == 8
    assert out[1]["score"] is None
    assert out[1]["category"] is None
    assert out[1]["tags"] == []


def test_normalize_result_drops_malformed():
    svc = TopicScorerService()
    out = svc.normalize_result(
        ["nope", {"no_index": 1}, {"i": "x"}, {"i": 3, "score": 0.5}]
    )
    assert list(out.keys()) == [3]
    assert out[3]["score"] == 0.5


def test_normalize_result_non_list():
    assert TopicScorerService().normalize_result({"i": 0}) == {}


def test_build_user_payload_trims_and_shapes():
    svc = TopicScorerService()
    payload = svc.build_user_payload(
        [{"i": 0, "source": "Weibo", "title": "T", "content": "x" * 5000}]
    )
    parsed = json.loads(payload)
    assert parsed[0]["i"] == 0
    assert parsed[0]["source"] == "Weibo"
    assert len(parsed[0]["content"]) == 600  # _MAX_CONTENT_CHARS


def test_extract_json_strips_fences():
    svc = TopicScorerService()
    assert svc._extract_json('```json\n[{"i":0}]\n```') == [{"i": 0}]
    assert svc._extract_json('[{"i":1}]') == [{"i": 1}]


class _FakeGovernanceConfigured:
    model = "deepseek-chat"
    api_key = "k"
    api_key_present = True
    base_url = "http://x"


class _FakeGovernanceUnconfigured:
    model = ""
    api_key = ""
    api_key_present = False
    base_url = ""


def _make_composed(model: str = "deepseek-chat"):
    """Build a minimal composed-prompt stand-in with model_copy support."""

    class _Composed:
        pass

    obj = _Composed()
    obj.model = model  # type: ignore[attr-defined]

    def _model_copy(*, update=None):
        new_obj = _Composed()
        new_obj.model = (update or {}).get("model", obj.model)  # type: ignore[attr-defined]
        new_obj._model_copy = _model_copy  # type: ignore[attr-defined]
        return new_obj

    obj.model_copy = _model_copy  # type: ignore[attr-defined]
    return obj


def _patch_governance(monkeypatch, governance_obj):
    async def _fake(module):
        return governance_obj

    monkeypatch.setattr(
        "app.services.topics.topic_scorer.get_module_governance", _fake
    )


def _patch_adapter_and_runner(monkeypatch, runner_instance):
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.get_adapter_for_user",
        lambda m, cfg, s: object(),
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.get_skill_repository", lambda: None
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.SkillToolService", lambda repo: None
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.AgentRunner",
        lambda **_: runner_instance,
    )


@pytest.mark.asyncio
async def test_score_items_happy_path(monkeypatch):
    svc = TopicScorerService()

    class _Runner:
        async def run_turn(self, composed, user_messages):
            return {
                "content": '[{"i":0,"score":0.8,"category":"model","tags":["x"]}]'
            }

    composed_obj = _make_composed("deepseek-chat")

    class _Composer:
        async def compose(self, inp):
            return composed_obj

    _patch_governance(monkeypatch, _FakeGovernanceConfigured())
    _patch_adapter_and_runner(monkeypatch, _Runner())
    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())

    out = await svc.score_items([{"i": 0, "title": "t", "content": "c"}])
    assert out[0]["score"] == 0.8
    assert out[0]["category"] == "model"


@pytest.mark.asyncio
async def test_score_items_runner_error_returns_empty(monkeypatch):
    svc = TopicScorerService()

    class _Runner:
        async def run_turn(self, composed, user_messages):
            return {"error": "boom", "content": ""}

    composed_obj = _make_composed("deepseek-chat")

    class _Composer:
        async def compose(self, inp):
            return composed_obj

    _patch_governance(monkeypatch, _FakeGovernanceConfigured())
    _patch_adapter_and_runner(monkeypatch, _Runner())
    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())

    assert await svc.score_items([{"i": 0, "title": "t", "content": "c"}]) == {}


@pytest.mark.asyncio
async def test_score_items_skips_when_governance_unconfigured(monkeypatch):
    """No model or api_key_present → skip scoring and return {} without hitting LLM."""
    svc = TopicScorerService()
    _patch_governance(monkeypatch, _FakeGovernanceUnconfigured())
    result = await svc.score_items([{"i": 0, "title": "t", "content": "c"}])
    assert result == {}


@pytest.mark.asyncio
async def test_score_items_skips_when_model_empty_key_present(monkeypatch):
    """api_key_present=True but model='' → still skip."""
    svc = TopicScorerService()

    class _Gov:
        model = ""
        api_key = "k"
        api_key_present = True
        base_url = "http://x"

    _patch_governance(monkeypatch, _Gov())
    assert await svc.score_items([{"i": 0, "title": "t", "content": "c"}]) == {}


@pytest.mark.asyncio
async def test_score_items_empty_input():
    assert await TopicScorerService().score_items([]) == {}
