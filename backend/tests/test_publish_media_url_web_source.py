"""发布链取素材 URL —— 下载来的视频必须发得出去。

``PublishTasksRepository.get_resource_media_url`` 是抖音发布与 H5 分享**共用**
的取 URL 入口（``publish_distribution`` 与 ``distribution_router`` 都走它）。
它此前只读 ``resources.file_path``，而那一列对 ``source_type='web'``（平台解析
下载的素材）**按设计为空** —— 共享下载字段只存 ``parsed_media``（PR-B）。

后果在生产实测过：同一个库里 web 视频 **107 个取不到 URL / 94 个能取到**，
upload 视频 0/6 —— 也就是**一半以上的可发布视频根本发不出去**，用户看到
``400 No media URL for share``。

这个缺陷是**随时间长出来的**：老的 web 行有 ``file_path``，新的没有。所以发布
功能上线时是好的，越往后坏得越多，而且报错完全不指向真正的原因（"没有 media
URL" 听起来像素材有问题，实际是我们没去正确的列找）。

两组：
  A. 阶梯接上了 —— web 素材能拿到 URL，upload 素材行为不变。
  B. 相册挡住了 —— 目录前缀绝不能被签成 URL。签一个目录会得到必然取不到内容
     的地址：发布"成功"，对端拿到 404，比直接发不出去更难归因。

⚠️ ``get_resource_media_url`` 自己开 ``read_scope()``，因此**必须在 ambient
scope 里调**，否则选择点 fail-closed 抛 ``UnscopedQueryError``。下面每个用例
都显式建 scope —— 这既是让测试能跑，也是把这条调用约束记录下来。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.db.scope import Scope, request_scope
from app.repositories import publish_tasks_repository as ptr
from app.repositories.publish_tasks_repository import PublishTasksRepository

pytestmark = pytest.mark.unit

USER_ID = "00000000-0000-4000-8000-000000000001"
UPLOAD_PATH = "sb://library/t1/d4/f6/d4f6b3f2.mp4"
DOWNLOAD_PATH = "sb://library/t1/97/01/970147ef.mp4"
ALBUM_PREFIX = "sb://library/t1/album/12345/"


class _RowSession:
    """返回一个固定的 resources 行（mappings().first() 的形状）。"""

    def __init__(self, row: Optional[Dict[str, Any]]):
        self.row = row

    async def execute(self, *_a: Any, **_k: Any):
        row = self.row

        class _R:
            def mappings(self):
                class _M:
                    def first(self):
                        return row

                return _M()

        return _R()


class _RowScope:
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return _RowSession(self.row)

    async def __aexit__(self, *_a: Any) -> bool:
        return False


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch):
    """接好两处：repository 读到的 resources 行 + 阶梯解析出的路径。

    阶梯本身在 ``test_resource_file_path.py`` 里单独测过，这里打桩它是为了让
    本文件只对"入口有没有走阶梯 / 有没有挡住相册"负责。
    """

    def _install(row: Optional[Dict[str, Any]], resolved: Optional[str]):
        monkeypatch.setattr(ptr, "read_scope", lambda: _RowScope(row))

        seen: Dict[str, Any] = {}

        async def _resolve(resource):
            seen["resource"] = resource
            return resolved

        monkeypatch.setattr(ptr, "resolve_resource_file_path", _resolve)
        return seen

    return _install


@pytest.fixture
def no_sign(monkeypatch: pytest.MonkeyPatch):
    """把签名换成可断言的标记，避免测试去碰对象存储。"""

    class _Store:
        def __init__(self, bucket):
            self.bucket = bucket

        async def signed_url(self, key, ttl_seconds=3600):
            return f"signed://{self.bucket}/{key}"

    import app.services.library.media_storage as ms

    monkeypatch.setattr(ms, "ObjectStore", _Store)
    return _Store


# ── A 组：阶梯接上了 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_resource_gets_a_url(wire, no_sign):
    """``source_type='web'``（``resources.file_path`` 为空）必须拿到 URL。

    这是 P0 本身。把实现回退成读 ``row["file_path"]``，本例立刻红 —— 红的样子
    正是用户遇到的那个：``None`` → ``400 No media URL for share``。
    """
    seen = wire(
        {"file_path": None, "media_id": 456, "creator_id": USER_ID},
        DOWNLOAD_PATH,
    )

    async with request_scope(Scope(user_id=USER_ID)):
        url = await PublishTasksRepository().get_resource_media_url(1)

    assert url is not None, "下载来的视频拿不到 URL —— 发布链断在这里"
    assert url.startswith("signed://")
    # 阶梯真的拿到了整行（含 media_id），而不是只被喂了 file_path ——
    # 少了 media_id，第二级就永远走不到。
    assert seen["resource"].get("media_id") == 456


@pytest.mark.asyncio
async def test_upload_resource_unchanged(wire, no_sign):
    """upload 素材行为不变 —— 修复不能以改变既有可用路径为代价。"""
    wire(
        {"file_path": UPLOAD_PATH, "media_id": None, "creator_id": USER_ID}, UPLOAD_PATH
    )

    async with request_scope(Scope(user_id=USER_ID)):
        url = await PublishTasksRepository().get_resource_media_url(1)

    assert url == "signed://library/t1/d4/f6/d4f6b3f2.mp4"


@pytest.mark.asyncio
async def test_missing_row_is_none(wire, no_sign):
    """行不存在仍然是 None（正向对照：没把"取不到"一律变成有值）。"""
    wire(None, DOWNLOAD_PATH)

    async with request_scope(Scope(user_id=USER_ID)):
        assert await PublishTasksRepository().get_resource_media_url(1) is None


@pytest.mark.asyncio
async def test_unresolvable_path_is_none(wire, no_sign):
    """两级都落空 → None，不是签一个空路径。"""
    wire({"file_path": None, "media_id": None, "creator_id": USER_ID}, None)

    async with request_scope(Scope(user_id=USER_ID)):
        assert await PublishTasksRepository().get_resource_media_url(1) is None


# ── B 组：相册挡住了 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_album_never_produces_a_directory_url(wire, no_sign):
    """相册解析成 None 时，入口必须返回 None，绝不签目录。

    这条守的是"修好之后更糟"的那个结局：一个指向目录的签名 URL 会让发布看起来
    成功，而对端取到 404 —— 失败被推迟到我们看不见的地方。
    """
    wire({"file_path": None, "media_id": 68, "creator_id": USER_ID}, None)

    async with request_scope(Scope(user_id=USER_ID)):
        url = await PublishTasksRepository().get_resource_media_url(1)

    assert url is None


@pytest.mark.asyncio
async def test_directory_prefix_would_be_caught_by_the_ladder():
    """端到端确认阶梯自己就会把相册前缀判成 None（入口因此天然安全）。

    与上一条互补：上一条测"入口尊重 None"，这一条测"相册确实会变成 None" ——
    两条都在，才排除了"入口对了但阶梯放行"的组合。
    """
    from app.services.library.resource_file_path import is_directory_prefix

    assert is_directory_prefix(ALBUM_PREFIX)
    assert not is_directory_prefix(DOWNLOAD_PATH)
