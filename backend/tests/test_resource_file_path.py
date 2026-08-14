""" "这个 resource 的文件在哪" —— PR-B 两级阶梯的测试。

背景：``resources.file_path`` 对 ``source_type='web'``（平台解析下载的素材）
**按设计为空** —— 共享下载字段只存 ``parsed_media``。生产实测 207 个视频里
107 个是这个形状，也就是说**只读第一级会漏掉一半以上的素材**，而且漏的方式
是静默的：调用方拿到 None，然后如实汇报"这个素材没有文件"，用户于是被告知一
个完好的视频不能用。

封面抽帧就是这么第二次坏的。第一次是缺 tenant scope（假 404），修好之后紧接着
撞上这一层（假 400）—— 两次都不是"报错了"，而是**给了一个关于用户数据的、听起
来很合理的错误答案**。所以这里测的重点不只是"能解析"，还包括"该为空时才为空"。

阶梯为什么不含 ``resource_versions``：它是镜像不是真相（migration 061/097 是从
resources 回填过去的），而且生产里有 2 行版本表根本没有路径 —— 靠它反而解不出
来。完整论证见 ``app/services/library/resource_file_path`` 的模块 docstring。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.library import resource_file_path as rfp
from app.services.library.resource_file_path import resolve_resource_file_path

pytestmark = pytest.mark.unit

UPLOAD_PATH = "sb://library/t1/d4/f6/d4f6b3f2.mp4"
DOWNLOAD_PATH = "sb://library/t1/97/01/970147ef.mp4"


class _PmSession:
    """最小 session 替身：``execute(...).scalar()`` 返回给定的 download_path。"""

    def __init__(self, value: Any):
        self.value = value
        self.queries = 0

    async def execute(self, *_a: Any, **_k: Any):
        self.queries += 1
        outer = self

        class _R:
            def scalar(self):
                return outer.value

        return _R()


class _PmScope:
    def __init__(self, session: _PmSession):
        self.session = session

    async def __aenter__(self) -> _PmSession:
        return self.session

    async def __aexit__(self, *_a: Any) -> bool:
        return False


@pytest.fixture
def pm(monkeypatch: pytest.MonkeyPatch):
    """打桩 parsed_media 查询，返回持有查询计数的 session。"""

    def _install(value: Any) -> _PmSession:
        session = _PmSession(value)
        monkeypatch.setattr(rfp, "read_scope", lambda: _PmScope(session), raising=False)
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "read_scope", lambda: _PmScope(session))
        return session

    return _install


@pytest.mark.asyncio
async def test_upload_resource_uses_its_own_file_path(pm):
    """第一级命中：上传/生成/派生素材的路径就在 resources 行上。"""
    session = pm(DOWNLOAD_PATH)  # 若被查到就说明短路失败
    row = {"file_path": UPLOAD_PATH, "media_id": 123, "source_type": "upload"}

    assert await resolve_resource_file_path(row) == UPLOAD_PATH
    # 不查库 —— 纯上传素材不该为一次多余的 parsed_media 往返买单，也证明
    # 第一级真的短路了（否则下面那条 web 用例的通过可能只是巧合）。
    assert session.queries == 0


@pytest.mark.asyncio
async def test_web_resource_falls_through_to_parsed_media(pm):
    """第二级命中：``source_type='web'`` 的行，路径在 parsed_media。

    这正是修复前会被判成"没有文件"的形状 —— 生产里过半的视频。
    """
    pm(DOWNLOAD_PATH)
    row = {"file_path": None, "media_id": 456, "source_type": "web"}

    assert await resolve_resource_file_path(row) == DOWNLOAD_PATH


@pytest.mark.asyncio
async def test_no_path_anywhere_is_none(pm):
    """两级都落空才是 None —— 这时"没有文件"是真话。"""
    pm(None)
    assert (
        await resolve_resource_file_path({"file_path": None, "media_id": 789}) is None
    )


@pytest.mark.asyncio
async def test_no_media_id_does_not_query(pm):
    """没有 media_id 就没有第二级可走，不该白查一次库。"""
    session = pm(DOWNLOAD_PATH)
    assert (
        await resolve_resource_file_path({"file_path": None, "media_id": None}) is None
    )
    assert session.queries == 0


@pytest.mark.asyncio
async def test_parsed_media_lookup_failure_degrades_to_none(monkeypatch):
    """补充查询炸了按"解析不出来"处理，不把整个请求变成 500。

    与 ``serve_resource_file`` 的既有行为一致：调用方本来就要处理 None。
    """

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("connection reset")

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", lambda: _Boom())

    assert await resolve_resource_file_path({"file_path": None, "media_id": 1}) is None


@pytest.mark.asyncio
async def test_resource_versions_is_not_consulted(pm):
    """阶梯里没有 ``resource_versions`` —— 它是镜像，不是真相。

    钉住这一点，是因为"改读版本表"是一个很有说服力的错误方案：它对大多数行
    看起来能用（镜像通常同步），却对生产里那 2 行版本表无路径的资源解不出来，
    而且与 migration 061/097 的回填方向相反。
    """
    pm(DOWNLOAD_PATH)
    row = {
        "file_path": None,
        "media_id": 456,
        # 就算行里带着一个版本路径也不该被采纳。
        "resource_versions": [{"file_path": "sb://library/t1/ff/ff/decoy.mp4"}],
    }
    assert await resolve_resource_file_path(row) == DOWNLOAD_PATH


# ── 抽帧链路真的走了阶梯 ──────────────────────────────────────────


class _WebRepo:
    """一个 ``source_type='web'`` 的源视频：``resources.file_path`` 为空。"""

    async def get_resource_by_id(self, resource_id: str):
        return {
            "id": resource_id,
            "filename": "downloaded.mp4",
            "file_type": "video",
            "mime_type": "video/mp4",
            "file_path": None,
            "media_id": 456,
            "source_type": "web",
        }

    async def get_first_resource_item(self, resource_id: str):
        return {"scope_id": "1", "folder_id": None, "library_id": None}


@pytest.mark.asyncio
async def test_load_source_video_resolves_web_backed_source(pm):
    """``load_source_video`` 对 web 素材必须解析成功，而不是 400。

    反向验证：把实现改回 ``source.get("file_path")``，本例立刻红 —— 那正是修
    scope 之后用户会撞上的第二堵墙（404 换成 400，视频依然用不了）。
    """
    from app.services.distribution.cover_frames import load_source_video

    pm(DOWNLOAD_PATH)
    source = await load_source_video(_WebRepo(), "500")

    assert source.file_path == DOWNLOAD_PATH


@pytest.mark.asyncio
async def test_load_source_video_still_400s_when_truly_fileless(pm):
    """正向对照：两级都空时仍然是 400。

    修复不能靠"永远不报 400"来通过 —— 那只是把错误答案换了个方向。
    """
    from app.services.distribution.cover_frames import (
        CoverFrameError,
        load_source_video,
    )

    pm(None)
    with pytest.raises(CoverFrameError) as exc:
        await load_source_video(_WebRepo(), "500")
    assert exc.value.status_code == 400
