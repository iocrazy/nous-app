"""图集前缀读取端 —— media_slides_router 支持 sb:// 前缀(list_prefix)。

背景(F1,spec 决策1 缺失的读端):PR-3 把图集迁到对象存储,
resource_versions.file_path 写成前缀 ``sb://library/t{scope}/album/{rid}/``
(``is_prefix=True``)。但 list_slides/serve_slide_file 只读 parsed_media.
download_path、纯文件系统枚举,对该前缀零感知 —— 图集 delete 本地目录后
会全 404,文件其实还在 S3 里。

本测试钉住:
- 已迁移图集(resource 当前 version 的 file_path 是 sb:// 前缀)→ list_prefix
  列出的对象经扩展名过滤后转成 slides 数组;serve_slide_file 把 filename
  拼到前缀上,委托给 serve_stored_file。
- 未迁移图集(resource 不存在 / 无 version / file_path 非 sb:// 前缀)→
  零回退,原文件系统 iterdir 逻辑逐行不变。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app.core.deps import AuthContext

pytestmark = pytest.mark.unit

MEDIA_ID = "700"
ALBUM_PREFIX = "sb://library/t5/album/123/"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _auth() -> AuthContext:
    return AuthContext(user_id="u1", auth_type="jwt")


def _patch_resources_repo(resource: dict | None, version: dict | None):
    """resources_repository.ResourcesRepository — 两次调用链
    (get_resource_by_media_id → get_version_by_number) 的 mock。"""
    repo = MagicMock()
    repo.get_resource_by_media_id = AsyncMock(return_value=resource)
    repo.get_version_by_number = AsyncMock(return_value=version)
    return patch(
        "app.repositories.resources_repository.ResourcesRepository",
        return_value=repo,
    )


def _patch_media_repo(media: dict):
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=media)
    return patch("app.repositories.media_repository.MediaRepository", return_value=repo)


def _patch_base_path(path="/tmp/fake-base"):
    return patch(
        "app.api.media_slides_router.Utils.get_download_base_path",
        return_value=path,
    )


# ── 已迁移(sb:// 前缀) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_slides_migrated_album_lists_prefix():
    """已迁移图集:list_slides 走 ObjectStore.list_prefix,按扩展名过滤后
    返回 slides,url 形如 /api/v1/media/{id}/slides/{name}。"""
    from app.api.media_slides_router import list_slides

    resource = {"id": "123", "current_version": 1}
    version = {"file_path": ALBUM_PREFIX}
    keys = [
        "t5/album/123/7613_0.jpg",
        "t5/album/123/7613_1.jpg",
        "t5/album/123/notes.txt",  # 非 slide 扩展名,应被过滤掉
    ]
    list_prefix = AsyncMock(return_value=keys)

    with (
        _patch_resources_repo(resource, version),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=list_prefix,
        ),
    ):
        result = await list_slides(MEDIA_ID, auth=_auth())

    list_prefix.assert_awaited_once_with("t5/album/123/")
    assert result["count"] == 2
    names = {s["name"] for s in result["slides"]}
    assert names == {"7613_0.jpg", "7613_1.jpg"}
    for s in result["slides"]:
        assert s["url"] == f"/api/v1/media/{MEDIA_ID}/slides/{s['name']}"
        assert s["type"] == "image"
        assert s["media_type"] == "image/jpg"


@pytest.mark.asyncio
async def test_list_slides_migrated_album_includes_cover_like_original():
    """cover.jpg 与原文件系统 iterdir 逻辑口径一致:原逻辑只按扩展名过滤,
    不特判 cover —— S3 分支同样不排除,避免与未迁移行为产生差异。"""
    from app.api.media_slides_router import list_slides

    resource = {"id": "123", "current_version": 1}
    version = {"file_path": ALBUM_PREFIX}
    keys = ["t5/album/123/7613_0.jpg", "t5/album/123/cover.jpg"]
    list_prefix = AsyncMock(return_value=keys)

    with (
        _patch_resources_repo(resource, version),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=list_prefix,
        ),
    ):
        result = await list_slides(MEDIA_ID, auth=_auth())

    names = {s["name"] for s in result["slides"]}
    assert "cover.jpg" in names
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_serve_slide_file_migrated_album_delegates_to_serve_stored_file():
    """已迁移图集:serve_slide_file 把 loc.key + filename 拼回 sb:// 值,
    委托给 serve_stored_file(而不是自己碰文件系统)。"""
    from app.api.media_slides_router import serve_slide_file

    resource = {"id": "123", "current_version": 1}
    version = {"file_path": ALBUM_PREFIX}
    sentinel = Response(content=b"jpg-bytes", media_type="image/jpeg")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_resources_repo(resource, version),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_slide_file(
            MEDIA_ID, "7613_0.jpg", _request(), auth=_auth(), token=None
        )

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == "sb://library/t5/album/123/7613_0.jpg"
    assert kwargs["request"] is not None
    assert kwargs["disposition"] == "inline"


@pytest.mark.asyncio
async def test_serve_slide_file_migrated_album_rejects_unsafe_filename():
    """filename 安全校验(/ \\ ..)在已迁移分支同样必须生效 —— 校验发生在
    存储位置判断之前,所以两个分支共享同一道闸门。"""
    from app.api.media_slides_router import serve_slide_file

    with pytest.raises(HTTPException) as exc_info:
        await serve_slide_file(MEDIA_ID, "../evil.jpg", _request(), auth=_auth())

    assert exc_info.value.status_code == 400


# ── 未迁移(文件系统,零回退) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_slides_unmigrated_falls_back_to_filesystem_no_resource(tmp_path):
    """resource 不存在(未走资源库,或图集从未建过 resource)→ 原 fs 逻辑,
    行为不变。"""
    from app.api.media_slides_router import list_slides

    download_path = "web/album700"
    slides_dir = tmp_path / download_path / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "001.jpg").write_bytes(b"x")
    (slides_dir / "002.mp4").write_bytes(b"x")
    (slides_dir / "readme.txt").write_bytes(b"x")

    media = {"id": MEDIA_ID, "download_path": download_path}

    with (
        _patch_resources_repo(None, None),
        _patch_media_repo(media),
        _patch_base_path(str(tmp_path)),
    ):
        result = await list_slides(MEDIA_ID, auth=_auth())

    assert result["count"] == 2
    names = {s["name"] for s in result["slides"]}
    assert names == {"001.jpg", "002.mp4"}


@pytest.mark.asyncio
async def test_list_slides_unmigrated_when_version_file_path_empty(tmp_path):
    """resource 存在但当前 version 的 file_path 为空/None → 视为未迁移,
    同样零回退到 fs 逻辑(边界情况,不能因为拿到 resource 就误判已迁移)。"""
    from app.api.media_slides_router import list_slides

    download_path = "web/album701"
    slides_dir = tmp_path / download_path / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "001.png").write_bytes(b"x")

    media = {"id": MEDIA_ID, "download_path": download_path}
    resource = {"id": "123", "current_version": 1}

    with (
        _patch_resources_repo(resource, {"file_path": None}),
        _patch_media_repo(media),
        _patch_base_path(str(tmp_path)),
    ):
        result = await list_slides(MEDIA_ID, auth=_auth())

    assert result["count"] == 1
    assert result["slides"][0]["name"] == "001.png"


@pytest.mark.asyncio
async def test_serve_slide_file_unmigrated_falls_back_to_filesystem(tmp_path):
    """未迁移图集:serve_slide_file 走原文件系统读取,不碰 serve_stored_file。"""
    from app.api.media_slides_router import serve_slide_file

    download_path = "web/album702"
    slides_dir = tmp_path / download_path / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "001.jpg").write_bytes(b"real-bytes")

    media = {"id": MEDIA_ID, "download_path": download_path}
    serve = AsyncMock()

    with (
        _patch_resources_repo(None, None),
        _patch_media_repo(media),
        _patch_base_path(str(tmp_path)),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_slide_file(
            MEDIA_ID, "001.jpg", _request(), auth=_auth(), token=None
        )

    serve.assert_not_awaited()
    assert resp.path.endswith("001.jpg")
