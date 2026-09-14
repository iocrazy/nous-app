"""GenerateImage / GenerateVideo 接 CLI provider 的本地文件路径（3a 补验缺陷 1）。

边界形状照抄真实 wire：``ImageGenerationService.generate_image`` 返回的是
``asdict(ImageGenResult)``（``video_providers/base.py:7``），所以 URL 类
provider 给 ``image_url``，CLI 类 provider（codex / jimeng-cli）给
``image_url=""`` + ``image_path=<本地文件>``；视频侧同形（``video_url`` /
``video_path``，见 ``_generate_video_via_cli`` 的 ``video_url=""``）。

两条不变量：
- 本地路径**绝不**回给模型——工具返回的 ``url`` 必须是登记行的服务 URL；
- 登记成功后 CLI 的临时目录被回收。
"""

from __future__ import annotations

import json
import os

import pytest

_CTX = {
    "run_id": "r1",
    "user_id": "u-uuid",
    "team_id": None,
    "agent_id": "ag",
}


def _tools(monkeypatch, captured: dict, gen_id: int, **svc_methods):
    import app.services.ai.tools.generate_media_tools as gmt

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": gen_id}

    async def _fake_scope(uid):
        return "42"

    monkeypatch.setattr(gmt, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(gmt, "register_generated_media", _fake_register)
    tools = gmt.GenerateMediaTools()
    stub = type("S", (), {k: staticmethod(v) for k, v in svc_methods.items()})()
    monkeypatch.setattr(tools, "_svc", lambda: stub)
    return tools


def _scratch(tmp_path, dirname: str, filename: str) -> str:
    d = tmp_path / dirname
    d.mkdir()
    f = d / filename
    f.write_bytes(b"x")
    return str(f)


@pytest.mark.asyncio
async def test_generate_image_routes_cli_local_path_to_source_path(
    monkeypatch, tmp_path
):
    local = _scratch(tmp_path, "codeximg_abc", "out.png")

    async def _gen(**kwargs):
        return {"image_url": "", "image_path": local}

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 555, generate_image=_gen)
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "codex")
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_MODEL", "gpt-6-astra")

    out = await tools.generate_image({"prompt": "a cat"}, dict(_CTX))

    assert out["ok"] is True
    assert out["generated_media_id"] == 555
    assert captured["source_path"] == local
    assert captured.get("source_url") is None
    # 路径绝不回给模型
    assert out["url"] == "/api/v1/generated-media/555/cover"
    assert local not in json.dumps(out)
    # 临时目录被回收
    assert not os.path.exists(os.path.dirname(local))


@pytest.mark.asyncio
async def test_generate_image_url_provider_still_goes_to_source_url(monkeypatch):
    async def _gen(**kwargs):
        return {"image_url": "https://cdn/x.png", "image_path": None}

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 556, generate_image=_gen)
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "ark")
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_MODEL", "m1")

    out = await tools.generate_image({"prompt": "a cat"}, dict(_CTX))

    assert out["ok"] is True
    assert captured["source_url"] == "https://cdn/x.png"
    assert captured.get("source_path") is None
    # 回登记行的服务 URL，不是会过期的 provider CDN 地址
    assert out["url"] == "/api/v1/generated-media/556/cover"


@pytest.mark.asyncio
async def test_generate_image_no_image_keeps_error_text_and_types_it(monkeypatch):
    async def _gen(**kwargs):
        return {"image_url": "", "image_path": None}

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 557, generate_image=_gen)
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "codex")

    out = await tools.generate_image({"prompt": "a cat"}, dict(_CTX))

    assert out["ok"] is False
    assert out["error_code"] == "no_image"
    assert out["error"] == "provider returned no image url"
    assert captured == {}


@pytest.mark.asyncio
async def test_generate_video_routes_cli_local_path_to_source_path(
    monkeypatch, tmp_path
):
    local = _scratch(tmp_path, "jimeng_abc", "out.mp4")

    async def _gen(**kwargs):
        return {"video_url": "", "video_path": local}

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 777, generate_video=_gen)
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_PROVIDER", "jimeng")
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_MODEL", "v1")

    out = await tools.generate_video(
        {"prompt": "pan", "source_image_url": "https://cdn/in.png"}, dict(_CTX)
    )

    assert out["ok"] is True
    assert out["generated_media_id"] == 777
    assert captured["source_path"] == local
    assert captured.get("source_url") is None
    # video 行在 /cover 上是 404（router: media_kind != "image" → no cover），
    # 所以耐久 URL 走 /stream，与 script_shot_video.py 的 durable 一致。
    assert out["url"] == "/api/v1/generated-media/777/stream"
    assert local not in json.dumps(out)
    assert not os.path.exists(os.path.dirname(local))


@pytest.mark.asyncio
async def test_generate_video_no_video_keeps_error_text_and_types_it(monkeypatch):
    async def _gen(**kwargs):
        return {"video_url": "", "video_path": None}

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 778, generate_video=_gen)
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_PROVIDER", "jimeng")

    out = await tools.generate_video(
        {"prompt": "pan", "source_image_url": "https://cdn/in.png"}, dict(_CTX)
    )

    assert out["ok"] is False
    assert out["error_code"] == "no_video"
    assert out["error"] == "provider returned no video url"
    assert captured == {}


@pytest.mark.asyncio
async def test_reaper_failure_does_not_fail_the_tool(monkeypatch, tmp_path):
    """回收是尽力而为——它炸了不该把一次已经付过钱的生成判失败。"""
    import app.services.ai.tools.generate_media_tools as gmt

    local = _scratch(tmp_path, "codeximg_abc", "out.png")

    async def _gen(**kwargs):
        return {"image_url": "", "image_path": local}

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 558, generate_image=_gen)

    def _boom(_p):
        raise OSError("read-only fs")

    monkeypatch.setattr(gmt, "reap_scratch_dir", _boom)
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "codex")

    out = await tools.generate_image({"prompt": "a cat"}, dict(_CTX))

    assert out["ok"] is True
    assert out["url"] == "/api/v1/generated-media/558/cover"


@pytest.mark.asyncio
async def test_scratch_dir_is_reaped_even_when_registration_fails(
    monkeypatch, tmp_path
):
    """入库失败那份文件同样没人再引用——不收就是 worker /tmp 的无界增长。"""
    import app.services.ai.tools.generate_media_tools as gmt

    local = _scratch(tmp_path, "codeximg_abc", "out.png")

    async def _gen(**kwargs):
        return {"image_url": "", "image_path": local}

    async def _boom(**kwargs):
        raise RuntimeError("object store down")

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 559, generate_image=_gen)
    monkeypatch.setattr(gmt, "register_generated_media", _boom)
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "codex")

    out = await tools.generate_image({"prompt": "a cat"}, dict(_CTX))

    assert out["ok"] is False
    assert out["error_code"] == "generation_failed"
    assert not os.path.exists(os.path.dirname(local))


@pytest.mark.asyncio
async def test_generate_image_registers_the_resolved_provider_and_model(
    monkeypatch, tmp_path
):
    """目录行名 + 空模型不是归因；查价靠的是 adapter 解析出的真值。"""
    local = _scratch(tmp_path, "codeximg_res", "out.png")

    async def _gen(**kwargs):
        return {
            "image_url": "",
            "image_path": local,
            "provider": "ark",
            "model": "doubao-seedream-4-0",
        }

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 556, generate_image=_gen)
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_PROVIDER", "nous-image")
    monkeypatch.setenv("GENMEDIA_DEFAULT_IMAGE_MODEL", "")

    assert (await tools.generate_image({"prompt": "a cat"}, dict(_CTX)))["ok"] is True
    origin = captured["origin"]
    assert (origin.provider, origin.model) == ("ark", "doubao-seedream-4-0")


@pytest.mark.asyncio
async def test_generate_video_registers_the_resolved_provider_and_model(
    monkeypatch, tmp_path
):
    local = _scratch(tmp_path, "jimengvid_res", "out.mp4")

    async def _gen(**kwargs):
        return {
            "video_url": "",
            "video_path": local,
            "provider": "jimeng-cli",
            "model": "jimeng-video-3.0",
        }

    captured: dict = {}
    tools = _tools(monkeypatch, captured, 557, generate_video=_gen)
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_PROVIDER", "nous-video")
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_MODEL", "")

    out = await tools.generate_video(
        {"prompt": "pan", "source_image_url": "https://cdn/in.png"}, dict(_CTX)
    )
    assert out["ok"] is True, out
    origin = captured["origin"]
    assert (origin.provider, origin.model) == ("jimeng-cli", "jimeng-video-3.0")
