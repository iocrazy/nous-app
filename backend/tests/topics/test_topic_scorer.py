import json

import pytest

from app.services.topics.topic_scorer import _MAX_OUTPUT_TOKENS, TopicScorerService


def test_normalize_result_clamps_and_validates():
    svc = TopicScorerService()
    raw = [
        {
            "i": 0,
            "dims": {"impact": 1.5, "novelty": -1},  # clamp -> 1.0 / 0.0
            "confidence": 1.4,  # clamp -> 1.0
            "reason": "  好东西  ",
            "ai_summary": "摘要",
            "category": "model",
            "tags": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],  # cap 8
        },
        {
            "i": 1,
            "dims": "not-an-object",  # -> None
            "confidence": "x",  # -> None
            "category": "bogus",  # invalid -> None
            "tags": "notalist",  # -> []
        },
    ]
    out = svc.normalize_result(raw)
    assert out[0]["dims"] == {"impact": 1.0, "novelty": 0.0}
    assert out[0]["confidence"] == 1.0
    assert out[0]["reason"] == "好东西"  # trimmed
    assert out[0]["category"] == "model"
    assert len(out[0]["tags"]) == 8
    assert out[1]["dims"] is None
    assert out[1]["confidence"] is None
    assert out[1]["category"] is None
    assert out[1]["tags"] == []


def test_normalize_result_drops_malformed():
    svc = TopicScorerService()
    out = svc.normalize_result(
        ["nope", {"no_index": 1}, {"i": "x"}, {"i": 3, "dims": {"impact": 0.5}}]
    )
    assert list(out.keys()) == [3]
    assert out[3]["dims"] == {"impact": 0.5}


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
    """Build a minimal composed-prompt stand-in with model_copy support.

    ``model_copy(update=...)`` carries every key through (not just ``model``)
    so tests can assert on ``max_tokens`` and other overrides.
    """

    class _Composed:
        def model_copy(self, *, update=None):
            new_obj = _Composed()
            new_obj.model = self.model  # type: ignore[attr-defined]
            new_obj.max_tokens = self.max_tokens  # type: ignore[attr-defined]
            for key, value in (update or {}).items():
                setattr(new_obj, key, value)
            return new_obj

    obj = _Composed()
    obj.model = model  # type: ignore[attr-defined]
    obj.max_tokens = 4096  # type: ignore[attr-defined]
    return obj


def _patch_governance(monkeypatch, governance_obj):
    async def _fake(module):
        return governance_obj

    monkeypatch.setattr("app.services.topics.topic_scorer.get_module_governance", _fake)


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


def _patch_nous(monkeypatch, *, allowed=False, models=None):
    """Patch the Nous-provider resolution. Default: not allowed (skip Nous)."""

    async def _allowed(module):
        return allowed

    monkeypatch.setattr(
        "app.services.ai.governance.ai_governance.is_nous_allowed", _allowed
    )
    # Also patch the name imported into topic_scorer's namespace.
    monkeypatch.setattr("app.services.topics.topic_scorer.is_nous_allowed", _allowed)

    class _Repo:
        async def list_enabled(self, type_filter=None):
            return models or []

        async def get_by_name(self, name):
            for m in models or []:
                if m["name"] == name:
                    return m
            return None

    monkeypatch.setattr(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        lambda: _Repo(),
    )


@pytest.mark.asyncio
async def test_score_items_uses_nous_when_governance_unconfigured(monkeypatch):
    """No module governance + Nous allowed with an enabled llm → Nous is used
    (Nous is the fallback platform default)."""
    svc = TopicScorerService()

    class _Runner:
        async def run_turn(self, composed, user_messages):
            return {
                "content": '[{"i":0,"dims":{"impact":0.9},"category":"product","tags":["a"]}]'
            }

    class _Composer:
        async def compose(self, inp):
            return _make_composed("qwen3-6-35b")

    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.OpenAICompatibleAdapter",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.get_skill_repository", lambda: None
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.SkillToolService", lambda repo: None
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.AgentRunner", lambda **_: _Runner()
    )
    _patch_governance(monkeypatch, _FakeGovernanceUnconfigured())
    _patch_nous(
        monkeypatch,
        allowed=True,
        models=[
            {
                "name": "nous-qwen3-llm",
                "actual_model": "qwen3-6-35b",
                "base_url": "http://10.0.0.10:8000/v1",
                "api_key": "k",
            }
        ],
    )
    out = await svc.score_items([{"i": 0, "title": "t", "content": "c"}])
    assert out[0]["dims"] == {"impact": 0.9}
    assert out[0]["category"] == "product"


@pytest.mark.asyncio
async def test_governance_overrides_nous(monkeypatch):
    """Explicit module governance WINS over the Nous platform default — this is
    how the operator switches topic scoring to another reachable platform."""
    svc = TopicScorerService()
    seen = {}

    class _Runner:
        async def run_turn(self, composed, user_messages):
            seen["model"] = composed.model
            return {
                "content": '[{"i":0,"dims":{"impact":0.7},"category":"tips","tags":[]}]'
            }

    class _Composer:
        async def compose(self, inp):
            return _make_composed("seed")

    # Governance configured AND Nous also available — governance must win.
    _patch_governance(monkeypatch, _FakeGovernanceConfigured())
    _patch_nous(
        monkeypatch,
        allowed=True,
        models=[
            {
                "name": "nous-qwen3-llm",
                "actual_model": "qwen3-6-35b",
                "base_url": "http://10.0.0.10:8000/v1",
                "api_key": "k",
            }
        ],
    )
    _patch_adapter_and_runner(monkeypatch, _Runner())
    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())

    out = await svc.score_items([{"i": 0, "title": "t", "content": "c"}])
    assert out[0]["category"] == "tips"
    # composed.model set to governance model (deepseek-chat), NOT the qwen Nous model
    assert seen["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_score_items_happy_path(monkeypatch):
    svc = TopicScorerService()

    class _Runner:
        async def run_turn(self, composed, user_messages):
            return {
                "content": '[{"i":0,"dims":{"impact":0.8},"category":"model","tags":["x"]}]'
            }

    composed_obj = _make_composed("deepseek-chat")

    class _Composer:
        async def compose(self, inp):
            return composed_obj

    _patch_nous(monkeypatch)
    _patch_governance(monkeypatch, _FakeGovernanceConfigured())
    _patch_adapter_and_runner(monkeypatch, _Runner())
    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())

    out = await svc.score_items([{"i": 0, "title": "t", "content": "c"}])
    assert out[0]["dims"] == {"impact": 0.8}
    assert out[0]["category"] == "model"


@pytest.mark.asyncio
async def test_score_items_raises_output_token_budget(monkeypatch):
    """The composed prompt handed to the runner must carry the raised
    max_tokens so qwen3 finishes the JSON answer without truncating."""
    svc = TopicScorerService()
    seen = {}

    class _Runner:
        async def run_turn(self, composed, user_messages):
            seen["model"] = composed.model
            seen["max_tokens"] = composed.max_tokens
            return {"content": '[{"i":0,"score":0.7,"category":"tips","tags":[]}]'}

    class _Composer:
        async def compose(self, inp):
            return _make_composed("deepseek-chat")

    _patch_nous(monkeypatch)
    _patch_governance(monkeypatch, _FakeGovernanceConfigured())
    _patch_adapter_and_runner(monkeypatch, _Runner())
    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())

    await svc.score_items([{"i": 0, "title": "t", "content": "c"}])
    assert seen["max_tokens"] == _MAX_OUTPUT_TOKENS
    assert seen["model"] == "deepseek-chat"


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

    _patch_nous(monkeypatch)
    _patch_governance(monkeypatch, _FakeGovernanceConfigured())
    _patch_adapter_and_runner(monkeypatch, _Runner())
    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())

    assert await svc.score_items([{"i": 0, "title": "t", "content": "c"}]) == {}


@pytest.mark.asyncio
async def test_score_items_skips_when_governance_unconfigured(monkeypatch):
    """No model or api_key_present → skip scoring and return {} without hitting LLM."""
    svc = TopicScorerService()
    _patch_nous(monkeypatch)
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

    _patch_nous(monkeypatch)
    _patch_governance(monkeypatch, _Gov())
    assert await svc.score_items([{"i": 0, "title": "t", "content": "c"}]) == {}


@pytest.mark.asyncio
async def test_score_items_fails_over_to_next_model(monkeypatch):
    """First model is unreachable (connection error) → scorer fails over to the
    next enabled Nous model instead of silently returning nothing."""
    svc = TopicScorerService()

    class _Runner:
        async def run_turn(self, composed, user_messages):
            if composed.model == "offline-model":
                raise RuntimeError("All connection attempts failed")
            return {
                "content": '[{"i":0,"dims":{"impact":0.7},"category":"model","tags":[]}]'
            }

    class _Composer:
        async def compose(self, inp):
            return _make_composed("seed")

    _patch_governance(monkeypatch, _FakeGovernanceUnconfigured())
    _patch_nous(
        monkeypatch,
        allowed=True,
        models=[
            {
                "name": "off",
                "actual_model": "offline-model",
                "base_url": "http://x",
                "api_key": "k",
            },
            {
                "name": "on",
                "actual_model": "online-model",
                "base_url": "http://y",
                "api_key": "k",
            },
        ],
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.OpenAICompatibleAdapter", lambda **_: object()
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.get_skill_repository", lambda: None
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.SkillToolService", lambda repo: None
    )
    monkeypatch.setattr(
        "app.services.topics.topic_scorer.AgentRunner", lambda **_: _Runner()
    )
    monkeypatch.setattr(svc, "_build_composer", lambda: _Composer())

    out = await svc.score_items([{"i": 0, "title": "t", "content": "c"}])
    assert out[0]["dims"] == {"impact": 0.7}  # the reachable model answered


@pytest.mark.asyncio
async def test_score_items_empty_input():
    assert await TopicScorerService().score_items([]) == {}


@pytest.mark.asyncio
async def test_run_recorded_wraps_with_run_recorder(monkeypatch):
    """Scoring calls must land in agent_runs: when the agent row resolves,
    _run_recorded wraps run_turn in RunRecorder (system user, topic_scorer
    trigger, model+provider attached) and passes the recorder into run_turn.
    This is the regression guard for the invisible 2026-06 deepseek burn."""
    from unittest.mock import AsyncMock
    from uuid import UUID

    from app.services.topics.topic_scorer import SYSTEM_RUN_USER_ID

    svc = TopicScorerService()
    captured: dict = {}

    class _FakeRecorder:
        def __init__(self, **kwargs):
            captured["recorder_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("app.services.topics.topic_scorer.RunRecorder", _FakeRecorder)

    runner = type("R", (), {})()
    runner.run_turn = AsyncMock(return_value={"content": "[]"})

    agent_id = UUID("33333333-3333-3333-3333-333333333333")
    result = await svc._run_recorded(
        runner, object(), [], model="deepseek-v4-flash", agent_id=agent_id
    )

    assert result == {"content": "[]"}
    kw = captured["recorder_kwargs"]
    assert kw["agent_id"] == agent_id
    assert kw["user_id"] == SYSTEM_RUN_USER_ID
    assert kw["trigger"] == "topic_scorer"
    assert kw["model"] == "deepseek-v4-flash"
    assert kw["provider"] == "deepseek"
    # the recorder instance reached run_turn (usage lands in agent_runs)
    assert isinstance(runner.run_turn.call_args.kwargs["recorder"], _FakeRecorder)


@pytest.mark.asyncio
async def test_run_recorded_without_agent_id_still_runs(monkeypatch):
    """Telemetry never blocks scoring: no agent row → plain run_turn."""
    from unittest.mock import AsyncMock

    svc = TopicScorerService()
    runner = type("R", (), {})()
    runner.run_turn = AsyncMock(return_value={"content": "[]"})

    result = await svc._run_recorded(runner, object(), [], model="m", agent_id=None)
    assert result == {"content": "[]"}
    assert "recorder" not in runner.run_turn.call_args.kwargs
