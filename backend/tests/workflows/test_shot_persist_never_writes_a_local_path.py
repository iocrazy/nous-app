"""登记失败时的回退不许把**本地路径**写进分镜行（2026-09-14 真栈）。

`persist_generation` 的回退写的是「保留 provider url，至少别烂成空」。对
Ark 那种 http CDN url 这条成立——它会过期，但在过期前图是能看的。对 CLI 类
provider（codex / jimeng-cli）给的**本地文件路径**则完全不成立：

- 那个路径是 worker 容器里的 `/tmp/codeximg_*`，浏览器从来打不开；
- 公用 `scratch_reaper`（#2274）就在同一个 `finally` 里把那个目录删了，所以
  回退写下的路径在函数返回前就已经不存在了。

真实生产行：`script_shots.id=337650953731886`，`status='done'`，
`image_url='/tmp/codeximg_y5nblpq4/gen.png'`，目录已被 reaper 删除。
状态说成功、图永远打不开——比诚实地失败更坏（用户看不出该重试）。

所以：本地路径 + 登记失败 → raise（workflow 既有失败分支把分镜标 failed）；
http(s) url + 登记失败 → 保持今天的回退。
"""

from __future__ import annotations

import pytest


async def _call_step(step_fn, **kwargs):
    """``@DBOS.step()`` 包过的函数——拿它底下的真身来调，单测里不起引擎。"""
    fn = getattr(step_fn, "__wrapped__", step_fn)
    return await fn(**kwargs)


async def _fake_scope_id(_scene, _user_id):
    return 1


def _stub_repos(monkeypatch, wf):
    class _Repo:
        async def get_by_id(self, _sid):
            return {"id": 1, "scene_id": 2, "description": "d"}

    monkeypatch.setattr(
        "app.repositories.script_shot_repository.get_script_shot_repository",
        lambda: _Repo(),
    )
    monkeypatch.setattr(
        "app.repositories.script_scene_repository.get_script_scene_repository",
        lambda: _Repo(),
    )
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)


def _stub_register_boom(monkeypatch, exc: Exception):
    async def _boom(**_kwargs):
        raise exc

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        _boom,
    )


def _spy_reaper(monkeypatch, wf) -> list[str]:
    reaped: list[str] = []
    monkeypatch.setattr(wf, "reap_scratch_dir", lambda p: reaped.append(p))
    return reaped


@pytest.mark.asyncio
async def test_local_path_plus_registration_failure_raises(monkeypatch):
    """真栈那一发：int run_id 让 INSERT 抛，回退把 /tmp 路径写进了分镜。"""
    import app.workflows.script_shot_generate as wf

    _stub_repos(monkeypatch, wf)
    _stub_register_boom(
        monkeypatch, RuntimeError("invalid input for query argument $9")
    )
    reaped = _spy_reaper(monkeypatch, wf)

    local = "/tmp/codeximg_y5nblpq4/gen.png"
    with pytest.raises(Exception) as excinfo:
        await _call_step(
            wf.persist_generation,
            shot_id="337650953731886",
            provider_url=local,
            model="m",
            provider="codex",
            user_id="u",
        )

    # 原始错因要穿出去（DBOS 记的就是它），不能被换成一句泛泛的 RuntimeError；
    # 而不管抛不抛，临时目录照收（H1 不受影响）。
    assert "invalid input for query argument $9" in str(excinfo.value)
    assert reaped == [local]


@pytest.mark.asyncio
async def test_local_path_without_user_id_raises_too(monkeypatch):
    """同一条回退的另一个入口：没有 user_id 时也 return 了 provider_url。
    本地路径在这里同样是一个永远打不开的值。"""
    import app.workflows.script_shot_generate as wf

    _stub_repos(monkeypatch, wf)
    reaped = _spy_reaper(monkeypatch, wf)

    with pytest.raises(Exception):
        await _call_step(
            wf.persist_generation,
            shot_id="1",
            provider_url="/tmp/jimeng_abc/out.png",
            model="m",
            provider="jimeng-cli",
            user_id=None,
        )
    assert reaped == ["/tmp/jimeng_abc/out.png"]


@pytest.mark.asyncio
async def test_http_url_plus_registration_failure_still_falls_back(monkeypatch):
    """既有行为不变：CDN url 会过期，但过期前它是能看的，保留它比把
    这次用户已经付过钱的生成判失败要好。"""
    import app.workflows.script_shot_generate as wf

    _stub_repos(monkeypatch, wf)
    _stub_register_boom(monkeypatch, RuntimeError("boom"))

    url = "https://cdn.example.com/x.png"
    out = await _call_step(
        wf.persist_generation,
        shot_id="1",
        provider_url=url,
        model="m",
        provider="ark",
        user_id="u",
    )
    assert out == {"image_url": url, "thumbnail_url": url}


@pytest.mark.asyncio
async def test_http_url_without_user_id_still_falls_back(monkeypatch):
    import app.workflows.script_shot_generate as wf

    _stub_repos(monkeypatch, wf)
    url = "http://cdn.example.com/x.png"
    out = await _call_step(
        wf.persist_generation,
        shot_id="1",
        provider_url=url,
        model="m",
        provider="ark",
        user_id=None,
    )
    assert out == {"image_url": url, "thumbnail_url": url}


@pytest.mark.asyncio
async def test_the_success_path_is_unchanged(monkeypatch):
    """正向对照：本地路径 + 登记成功 → 仍然返回 durable /cover url。"""
    import app.workflows.script_shot_generate as wf

    _stub_repos(monkeypatch, wf)

    async def _ok(**_kwargs):
        return {"id": 55}

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media", _ok
    )
    reaped = _spy_reaper(monkeypatch, wf)

    out = await _call_step(
        wf.persist_generation,
        shot_id="1",
        provider_url="/tmp/codeximg_ok/gen.png",
        model="m",
        provider="codex",
        user_id="u",
    )
    assert out == {
        "image_url": "/api/v1/generated-media/55/cover",
        "thumbnail_url": "/api/v1/generated-media/55/cover",
    }
    assert reaped == ["/tmp/codeximg_ok/gen.png"]


@pytest.mark.asyncio
async def test_video_persist_has_no_url_fallback_at_all(monkeypatch):
    """视频侧的产物永远是本地文件，所以它从一开始就没有回退分支。
    钉住这一点，免得有人「照着出图那条补一个回退」。"""
    import app.workflows.script_shot_video as wf

    _stub_repos(monkeypatch, wf)
    _stub_register_boom(monkeypatch, RuntimeError("boom"))
    reaped = _spy_reaper(monkeypatch, wf)

    with pytest.raises(Exception):
        await _call_step(
            wf.persist_video_generation,
            shot_id="1",
            local_path="/tmp/jimeng_v/out.mp4",
            model="m",
            provider="jimeng-cli",
            user_id="u",
        )
    assert reaped == ["/tmp/jimeng_v/out.mp4"]
