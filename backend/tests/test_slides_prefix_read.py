"""图集前缀读取端 —— media_slides_router 支持 sb:// 前缀(list_prefix)。

背景(F1,spec 决策1 缺失的读端):PR-3 把图集迁到对象存储,
resource_versions.file_path 写成前缀 ``sb://library/t{scope}/album/{rid}/``
(``is_prefix=True``)。但 list_slides/serve_slide_file 只读 parsed_media.
download_path、纯文件系统枚举,对该前缀零感知 —— 图集 delete 本地目录后
会全 404,文件其实还在 S3 里。

本测试钉住:
- 已迁移图集(resource 当前 version 的 file_path 是 sb:// 前缀)→ list_prefix
  列出的对象要先按布局分层(优先 slides/ 子目录、否则前缀根层)再按扩展名
  过滤,且排除封面;serve_slide_file 按同样两候选顺序探测对象是否存在,
  命中后委托给 serve_stored_file。
- 未迁移图集(resource 不存在 / 无 version / file_path 非 sb:// 前缀)→
  零回退,原文件系统 iterdir 逻辑逐行不变。

第二条断裂(2026-07-29):首版 sb 分支把整个前缀当成 slides 列表、并直接拼
``{prefix}{filename}`` 取文件。前者让 cover.jpg 冒充第 1 张,后者对 slides/
布局少了一段 key —— 净效果是"第 1 张显示封面、第 2 张起全 404"。两处都在
本文件里有对应的钉子测试。
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


def _patch_db_fetch_one(row: dict | None):
    """app.db.engine.fetch_one —— _resolve_album_location 的非 scoped 直查。

    row=None 模拟 SQL 查不到行(resource 不存在 / 无对应 version,
    INNER JOIN 直接筛掉);row={"file_path": ...} 模拟查到一行(file_path
    可能是 sb:// 前缀,也可能是文件系统相对路径或 NULL)。"""
    return patch(
        "app.db.engine.fetch_one",
        new=AsyncMock(return_value=row),
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


# ── _resolve_album_location 直查路径(F1 scope bug 回归) ─────────────────
#
# 根因:get_resource_by_media_id 用 scoped read_scope(),而 slides 端点上下文
# 没有打开 scope session → 报 "no scope is set" 被内部 except 吞成 None →
# 已迁图集被误判成未迁移,delete 本地目录后 404。修复后改走 db_engine.fetch_one
# 非 scoped 直查,以下测试钉住"打的是新路径"而不是旧的 ResourcesRepository。


@pytest.mark.asyncio
async def test_resolve_album_location_queries_db_engine_directly_not_scoped_repo():
    """确认新实现调用 db_engine.fetch_one(非 scoped),且完全不碰
    ResourcesRepository(scoped,需要 scope session,是本 bug 的根因)。"""
    from app.api.media_slides_router import _resolve_album_location

    fetch_one = AsyncMock(return_value={"file_path": ALBUM_PREFIX})
    repo_cls = MagicMock(
        side_effect=AssertionError(
            "ResourcesRepository 不应被调用 —— 需要 scope session,是本 bug 的根因"
        )
    )

    with (
        patch("app.db.engine.fetch_one", new=fetch_one),
        patch("app.repositories.resources_repository.ResourcesRepository", repo_cls),
    ):
        loc = await _resolve_album_location(MEDIA_ID)

    fetch_one.assert_awaited_once()
    sql, params = fetch_one.await_args.args
    assert params == {"media_id": 700}
    assert "resources" in sql.lower()
    assert "resource_versions" in sql.lower()
    assert loc is not None
    assert loc.is_object_store and loc.is_prefix


@pytest.mark.asyncio
async def test_resolve_album_location_no_row_returns_none():
    """无 resource / 无对应 version(INNER JOIN 查不到行)→ None,走 fs 回退。"""
    from app.api.media_slides_router import _resolve_album_location

    with _patch_db_fetch_one(None):
        loc = await _resolve_album_location(MEDIA_ID)

    assert loc is None


@pytest.mark.asyncio
async def test_resolve_album_location_non_prefix_file_path_returns_none():
    """file_path 是文件系统相对路径(非 sb:// 前缀)→ None,走 fs 回退。"""
    from app.api.media_slides_router import _resolve_album_location

    with _patch_db_fetch_one({"file_path": "web/album700/slides"}):
        loc = await _resolve_album_location(MEDIA_ID)

    assert loc is None


@pytest.mark.asyncio
async def test_resolve_album_location_invalid_media_id_returns_none():
    """media_id 非数字(防御性 int 转换失败)→ None,不抛异常。"""
    from app.api.media_slides_router import _resolve_album_location

    fetch_one = AsyncMock()
    with patch("app.db.engine.fetch_one", new=fetch_one):
        loc = await _resolve_album_location("not-a-number")

    fetch_one.assert_not_awaited()
    assert loc is None


# ── 已迁移(sb:// 前缀) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_slides_migrated_album_lists_prefix():
    """已迁移图集:list_slides 走 ObjectStore.list_prefix,按扩展名过滤后
    返回 slides,url 形如 /api/v1/media/{id}/slides/{name}。"""
    from app.api.media_slides_router import list_slides

    keys = [
        "t5/album/123/7613_0.jpg",
        "t5/album/123/7613_1.jpg",
        "t5/album/123/notes.txt",  # 非 slide 扩展名,应被过滤掉
    ]
    list_prefix = AsyncMock(return_value=keys)

    with (
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
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
async def test_list_slides_migrated_album_excludes_the_cover():
    """封面不是 slide —— 平铺布局里 cover.jpg 和真 slide 同级,不排除就会
    当成第 1 张、把每张真 slide 往后挤一位(用户症状:第 1 张是封面、
    第 2 张起全断)。dynamic_cover.* 同理。"""
    from app.api.media_slides_router import list_slides

    keys = [
        "t5/album/123/7613_0.jpg",
        "t5/album/123/cover.jpg",
        "t5/album/123/dynamic_cover.webp",
        "t5/album/123/audio.mp3",  # 扩展名已挡,和封面一样不该出现
    ]
    list_prefix = AsyncMock(return_value=keys)

    with (
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=list_prefix,
        ),
    ):
        result = await list_slides(MEDIA_ID, auth=_auth())

    assert [s["name"] for s in result["slides"]] == ["7613_0.jpg"]
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_list_slides_migrated_album_prefers_the_slides_subdirectory():
    """slides/ 子目录存在时只取子目录内容(name 是 basename),前缀根层的
    cover/audio 不参与 —— 与本地分支 iterdir 的优先级完全一致。

    生产 400 个 album 对象走的是这个布局:少了这层过滤,前缀根层的
    cover.jpg 会和 slides/001.jpg 混在一张列表里。"""
    from app.api.media_slides_router import list_slides

    keys = [
        "t5/album/123/cover.jpg",
        "t5/album/123/audio.mp3",
        "t5/album/123/slides/002.jpg",
        "t5/album/123/slides/001.jpg",
        "t5/album/123/slides/nested/deep.jpg",  # 单张路由寻址不到,不列
    ]
    list_prefix = AsyncMock(return_value=keys)

    with (
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=list_prefix,
        ),
    ):
        result = await list_slides(MEDIA_ID, auth=_auth())

    assert [s["name"] for s in result["slides"]] == ["001.jpg", "002.jpg"]
    for s in result["slides"]:
        # url 用 basename —— serve_slide_file 会自己探回 slides/ 段。
        assert s["url"] == f"/api/v1/media/{MEDIA_ID}/slides/{s['name']}"


@pytest.mark.asyncio
async def test_list_slides_migrated_album_single_real_slide():
    """只有 1 张真 slide 的图集(其余当年就没下载下来)修后返回 1 张,
    而不是"封面 + 1 张"凑成 2 张里第 2 张点不开。"""
    from app.api.media_slides_router import list_slides

    keys = [
        "t5/album/123/audio.mp3",
        "t5/album/123/cover.jpg",
        "t5/album/123/slides/001.jpg",
    ]

    with (
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=AsyncMock(return_value=keys),
        ),
    ):
        result = await list_slides(MEDIA_ID, auth=_auth())

    assert result["count"] == 1
    assert result["slides"][0]["name"] == "001.jpg"


@pytest.mark.asyncio
async def test_serve_slide_file_migrated_album_probes_the_slides_subdirectory():
    """已迁移图集:真 slide 的对象在 {prefix}slides/{filename},所以必须先探
    slides/ 候选 —— 旧实现直接拼 {prefix}{filename},除恰好落在前缀根上的
    封面之外每张都 404(这就是"第 2 张打不开"的直接原因)。"""
    from app.api.media_slides_router import serve_slide_file

    sentinel = Response(content=b"jpg-bytes", media_type="image/jpeg")
    serve = AsyncMock(return_value=sentinel)
    exists = AsyncMock(side_effect=lambda key: key.endswith("slides/001.jpg"))

    with (
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch("app.services.library.media_storage.ObjectStore.exists", new=exists),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_slide_file(
            MEDIA_ID, "001.jpg", _request(), auth=_auth(), token=None
        )

    assert resp is sentinel
    args, kwargs = serve.await_args
    assert args[0] == "sb://library/t5/album/123/slides/001.jpg"
    assert kwargs["disposition"] == "inline"


@pytest.mark.asyncio
async def test_serve_slide_file_migrated_album_falls_back_to_the_prefix_root():
    """平铺布局(slides/ 不存在)退化到 {prefix}{filename},与本地分支的
    两候选顺序一致。"""
    from app.api.media_slides_router import serve_slide_file

    sentinel = Response(content=b"jpg-bytes", media_type="image/jpeg")
    serve = AsyncMock(return_value=sentinel)
    probed: list[str] = []

    async def _exists(key: str) -> bool:
        probed.append(key)
        return "slides/" not in key

    with (
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.exists",
            new=AsyncMock(side_effect=_exists),
        ),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        await serve_slide_file(
            MEDIA_ID, "7613_0.jpg", _request(), auth=_auth(), token=None
        )

    assert probed == [
        "t5/album/123/slides/7613_0.jpg",
        "t5/album/123/7613_0.jpg",
    ]
    assert serve.await_args.args[0] == "sb://library/t5/album/123/7613_0.jpg"


@pytest.mark.asyncio
async def test_serve_slide_file_migrated_album_404_when_neither_candidate_exists():
    """两个候选 key 都不在 → 404,不是 500(前端能区分"这张没下载下来")。"""
    from app.api.media_slides_router import serve_slide_file

    serve = AsyncMock()

    with (
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.exists",
            new=AsyncMock(return_value=False),
        ),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
        pytest.raises(HTTPException) as exc_info,
    ):
        await serve_slide_file(
            MEDIA_ID, "009.jpg", _request(), auth=_auth(), token=None
        )

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()


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
        _patch_db_fetch_one(None),
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

    with (
        _patch_db_fetch_one({"file_path": None}),
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
        _patch_db_fetch_one(None),
        _patch_media_repo(media),
        _patch_base_path(str(tmp_path)),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_slide_file(
            MEDIA_ID, "001.jpg", _request(), auth=_auth(), token=None
        )

    serve.assert_not_awaited()
    assert resp.path.endswith("001.jpg")
