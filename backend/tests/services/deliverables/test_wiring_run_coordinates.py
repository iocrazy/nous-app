"""坐标从 runner 一路走到 ``GenerationOrigin``。

产出登记要 (turn, step) 才能把卡挂到正确的那一步。少了它，一次 40 步的 run
里所有产出挤在一堆，谁也说不出是哪一步产的。
"""

from types import SimpleNamespace

from app.services.ai.runner.agent_runner import AgentRunner


def _runner() -> AgentRunner:
    return AgentRunner.__new__(AgentRunner)


def test_media_run_context_carries_the_step_it_was_given():
    recorder = SimpleNamespace(run_id=777, user_id="u", team_id=3)
    composed = SimpleNamespace(agent_id="a1")

    ctx = _runner()._media_run_context(recorder, composed, step=5)

    assert ctx["run_id"] == 777
    # turn 与 ``_step_started`` 同源（目前恒为 1）；改一个要一起改。
    assert (ctx["turn"], ctx["step"]) == (1, 5)


def test_no_step_given_is_an_honest_none_not_a_zero():
    """没有坐标就说没有。伪造一个 0 会让血缘页把产出挂到不存在的第 0 步。"""
    recorder = SimpleNamespace(run_id=777, user_id="u", team_id=3)
    ctx = _runner()._media_run_context(recorder, SimpleNamespace(agent_id="a1"))
    assert ctx["step"] is None


def test_no_recorder_means_no_run_and_no_turn():
    """没有 recorder 就没有 run；这一路的产出不该假装属于某个 turn。"""
    ctx = _runner()._media_run_context(None, SimpleNamespace(agent_id="a1"))
    assert ctx["run_id"] is None and ctx["turn"] is None


def _stub_media_tools(monkeypatch, seen: dict, url_key: str):
    import app.services.ai.tools.generate_media_tools as gmt

    async def _fake_register(**kwargs):
        seen["origin"] = kwargs["origin"]
        return {"id": 1}

    monkeypatch.setattr(gmt, "register_generated_media", _fake_register)
    monkeypatch.setattr(gmt, "_resolve_provider_model", lambda _a, _k: ("openai", "m1"))

    class _Svc:
        async def generate_image(self, **_k):
            return {url_key: "http://cdn/x.png"}

        async def generate_video(self, **_k):
            return {url_key: "http://cdn/x.mp4"}

    monkeypatch.setattr(gmt.GenerateMediaTools, "_svc", lambda _self: _Svc())
    return gmt


async def test_generate_image_tool_puts_the_coordinates_on_the_origin(monkeypatch):
    seen: dict = {}
    gmt = _stub_media_tools(monkeypatch, seen, "url")
    ctx = {
        "run_id": 777,
        "user_id": "u",
        "team_id": 1,
        "agent_id": "a",
        "turn": 1,
        "step": 4,
    }

    out = await gmt.GenerateMediaTools().generate_image({"prompt": "a cat"}, ctx)

    assert out["ok"] is True
    assert (seen["origin"].turn, seen["origin"].step) == (1, 4)


async def test_generate_video_tool_puts_the_coordinates_on_the_origin(monkeypatch):
    seen: dict = {}
    gmt = _stub_media_tools(monkeypatch, seen, "url")
    ctx = {
        "run_id": 777,
        "user_id": "u",
        "team_id": 1,
        "agent_id": "a",
        "turn": 1,
        "step": 9,
    }

    out = await gmt.GenerateMediaTools().generate_video(
        {"prompt": "a cat", "source_image_url": "http://x/a.png"}, ctx
    )

    assert out["ok"] is True
    assert (seen["origin"].turn, seen["origin"].step) == (1, 9)
