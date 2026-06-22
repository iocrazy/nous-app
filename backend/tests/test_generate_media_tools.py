import pytest


@pytest.mark.asyncio
async def test_generate_image_registers_and_returns_ref(monkeypatch):
    import app.services.ai.tools.generate_media_tools as gmt

    async def _fake_generate_image(**kwargs):
        assert kwargs["prompt"] == "a cat"
        return {"url": "http://x/cat.png"}

    captured = {}

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": 555}

    async def _fake_scope(uid):
        return "42"

    monkeypatch.setattr(gmt, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(gmt, "register_generated_media", _fake_register)
    tools = gmt.GenerateMediaTools()
    monkeypatch.setattr(
        tools,
        "_svc",
        lambda: type("S", (), {"generate_image": staticmethod(_fake_generate_image)})(),
    )
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "prov")
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_MODEL", "m1")

    out = await tools.generate_image(
        {"prompt": "a cat"},
        {"run_id": "r1", "user_id": "u-uuid", "team_id": None, "agent_id": "ag"},
    )
    assert out["ok"] is True and out["generated_media_id"] == 555
    assert captured["scope_id"] == 42  # personal team fallback (team_id None)
    assert captured["origin"].kind == "agent_run" and captured["origin"].run_id == "r1"


@pytest.mark.asyncio
async def test_generate_image_no_provider_returns_clean_error(monkeypatch):
    import app.services.ai.tools.generate_media_tools as gmt

    monkeypatch.delenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", raising=False)
    tools = gmt.GenerateMediaTools()
    out = await tools.generate_image(
        {"prompt": "x"}, {"run_id": "r", "user_id": "u", "team_id": 7, "agent_id": "a"}
    )
    assert out["ok"] is False and "provider" in out["error"]


@pytest.mark.asyncio
async def test_generate_image_provider_failure_is_clean(monkeypatch):
    import app.services.ai.tools.generate_media_tools as gmt

    async def _boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "prov")
    tools = gmt.GenerateMediaTools()
    monkeypatch.setattr(
        tools,
        "_svc",
        lambda: type("S", (), {"generate_image": staticmethod(_boom)})(),
    )
    out = await tools.generate_image(
        {"prompt": "x"}, {"run_id": "r", "user_id": "u", "team_id": 7, "agent_id": "a"}
    )
    assert out["ok"] is False and "error" in out


@pytest.mark.asyncio
async def test_generate_video_registers_and_returns_ref(monkeypatch):
    import app.services.ai.tools.generate_media_tools as gmt

    async def _fake_generate_video(**kwargs):
        assert kwargs["prompt"] == "pan"
        assert kwargs["source_image_url"] == "http://x/in.png"
        return {"url": "http://x/clip.mp4"}

    captured = {}

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": 555}

    async def _fake_scope(uid):
        return "42"

    monkeypatch.setattr(gmt, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(gmt, "register_generated_media", _fake_register)
    tools = gmt.GenerateMediaTools()
    monkeypatch.setattr(
        tools,
        "_svc",
        lambda: type("S", (), {"generate_video": staticmethod(_fake_generate_video)})(),
    )
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_PROVIDER", "prov")
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_MODEL", "m1")

    out = await tools.generate_video(
        {"prompt": "pan", "source_image_url": "http://x/in.png"},
        {"run_id": "r1", "user_id": "u-uuid", "team_id": None, "agent_id": "ag"},
    )
    assert out["ok"] is True and out["generated_media_id"] == 555
    assert captured["scope_id"] == 42  # personal team fallback (team_id None)
    assert (
        captured["origin"].kind == "agent_run"
        and captured["origin"].derivation_kind == "video_gen"
        and captured["origin"].run_id == "r1"
    )
