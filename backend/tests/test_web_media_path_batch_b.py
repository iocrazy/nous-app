"""B 批：PR-B 阶梯的另外两个消费点，以及它们**语义相反**的一课。

两处都因为只读 ``resources.file_path`` 而对 ``source_type='web'``（平台解析下
载的素材）失效，但**修法不同**，而且差别正是本文件要钉住的东西：

    取路径（``resolve_resource_import``）
        问的是"给我一个能读的**文件**"。图文相册是一个目录，不是单张图，所以
        阶梯把它解析成 ``None`` 是**保护** —— 404 是对这个问题的正确回答。

    存在性（``get_owned_platform_ids``）
        问的是"这东西**下载过没有**"。相册**下载过**。同一个目录守卫在这里会
        给出**错误答案**：把已下载的相册报成"没下载"，用户于是重复下载。

也就是说"目录形状"这个陷阱有两种表现：上一批是"目录当文件用会炸"，这一批是
"目录被守卫挡掉会答错"。**所以存在性判断刻意不复用取路径函数** —— 复用在这里
是错的，不是省事。

（顺带：存在性还必须留在 SQL 里。逐行调 Python 解析器会把一次批量查询变成
N+1，而这个方法的整个卖点就是"一次查完整张歌单"。）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.unit

FS_PATH = "teams/42/uploads/1/v1/a.png"
SB_PATH = "sb://library/t42/ab/cd/deadbeef.png"
ALBUM_PREFIX = "sb://library/t42/album/12345/"


# ══════════════════════════════════════════════════════════════════
# 一、取路径：库素材当 i2i/i2v 参考图
# ══════════════════════════════════════════════════════════════════


def test_import_uses_the_resolved_path_not_the_raw_column():
    """``resolve_resource_import`` 用**传进来的**路径，不再自己读 file_path。

    这正是修复的形状：定位文件需要查库（web 行的路径在 parsed_media），而这个
    函数的 docstring 承诺"纯函数、无 I/O"。把解析放到调用方，两个性质都保住。
    """
    from app.api.generated_media_router import resolve_resource_import

    # resources 行上没有 file_path（web 素材的真实形状），路径由阶梯给出。
    row = {"id": 1, "mime_type": "image/png"}
    args = resolve_resource_import(row, SB_PATH)

    assert args == {"file_path": SB_PATH, "mime": "image/png"}


def test_import_404s_when_the_ladder_resolves_nothing():
    """阶梯给 ``None``（含相册）→ 404。

    相册在这里 404 是**对的**：它是一叠图，不是单张参考图。
    """
    from app.api.generated_media_router import (
        ResourceImportError,
        resolve_resource_import,
    )

    with pytest.raises(ResourceImportError) as e:
        resolve_resource_import({"id": 1, "mime_type": "image/png"}, None)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_import_endpoint_resolves_web_backed_resource(monkeypatch, tmp_path):
    """端点对 web 素材（``resources.file_path`` 为空）必须真的取到文件。

    走完整端点而不是只调纯函数 —— 要证明的是**接线**：端点先过阶梯、再把结果
    喂给纯函数。反向验证：把端点改回 ``resolve_resource_import(resource,
    resource.get("file_path"))``，本例立刻红，红的样子正是用户遇到的 404
    "Resource has no local file"。
    """
    import app.services.library.generated_media_service as gm
    import app.services.library.resource_file_path as rfp
    from app.api.generated_media_router import (
        ResourceImportRequest,
        import_from_resource,
    )
    from app.core.config import settings
    from app.repositories import resources_repository as rr_mod

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/42/downloads/a.png"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"downloaded-bytes")

    # web 素材的真实形状：resources 行上没有路径，只有 media_id。
    web_row = {"id": 1, "file_path": None, "media_id": 7, "mime_type": "image/png"}
    seen: Dict[str, Any] = {}

    async def _fake_ladder(resource):
        seen["resource"] = resource
        return rel  # 第二级（parsed_media）给出的路径

    monkeypatch.setattr(rfp, "resolve_resource_file_path", _fake_ladder)

    captured: Dict[str, Any] = {}

    async def _fake_scope(_auth):
        return 42

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": 999}

    async def _fake_get(self, resource_id):
        return web_row

    async def _fake_access(resource_id, user_id, share_token):
        return True

    import importlib

    router_mod = importlib.import_module("app.api.generated_media_router")
    monkeypatch.setattr(router_mod, "_scope", _fake_scope)
    monkeypatch.setattr(gm, "register_generated_media", _fake_register)
    monkeypatch.setattr(rr_mod.ResourcesRepository, "get_resource_by_id", _fake_get)
    monkeypatch.setattr("app.api.media_permissions.check_media_access", _fake_access)

    class _Auth:
        user_id = "00000000-0000-4000-8000-000000000001"

    resp = await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())

    assert resp["data"]["id"] == "999"
    assert captured["mime"] == "image/png"
    assert seen["resource"]["media_id"] == 7, "阶梯必须拿到整行，否则第二级走不到"


# ══════════════════════════════════════════════════════════════════
# 二、存在性：这首曲子下载过没有
# ══════════════════════════════════════════════════════════════════


class _Row:
    """``result.all()`` 里的一行（只有 platform_id 一列）。"""

    def __init__(self, pid: str):
        self._pid = pid

    def __getitem__(self, i: int) -> str:
        return self._pid


class _CapturingSession:
    """执行时把编译后的 SQL 记下来，并按谓词筛选假数据。

    断言看的是**真实渲染出的 WHERE 子句**，而不是"我们调用了某个函数" ——
    后者用 mock 也能骗过去，前者只有谓词真的写对了才成立。
    """

    def __init__(self, rows: List[Dict[str, Optional[str]]]):
        self.rows = rows
        self.sql = ""

    async def execute(self, stmt: Any):
        from sqlalchemy.dialects import postgresql

        self.sql = str(stmt.compile(dialect=postgresql.dialect())).lower()

        matched = [
            r["platform_id"]
            for r in self.rows
            if (
                r.get("file_path") is not None
                or ("download_path" in self.sql and r.get("download_path") is not None)
            )
        ]

        class _Result:
            def all(_self):
                return [_Row(p) for p in matched]

        return _Result()


class _Scope:
    def __init__(self, session: _CapturingSession):
        self.session = session

    async def __aenter__(self) -> _CapturingSession:
        return self.session

    async def __aexit__(self, *_a: Any) -> bool:
        return False


@pytest.fixture
def owned(monkeypatch: pytest.MonkeyPatch):
    """接好 ``get_owned_platform_ids`` 的 session，返回捕获器。"""

    def _install(rows: List[Dict[str, Optional[str]]]) -> _CapturingSession:
        import app.repositories.resources_repository as rr

        session = _CapturingSession(rows)
        monkeypatch.setattr(rr, "read_scope", lambda: _Scope(session))
        return session

    return _install


@pytest.mark.asyncio
async def test_downloaded_web_track_counts_as_owned(owned):
    """``resources.file_path`` 为空但 ``parsed_media.download_path`` 有值 →
    已下载。

    这是 P1 本身。生产实测（单个账号）旧谓词漏判 197 条 —— 会被默认勾选、重复下载自己
    已经有的东西。把谓词改回只看 ``Resources.file_path``，本例立刻红。
    """
    from app.repositories.resources_repository import ResourcesRepository

    owned([{"platform_id": "vid-web", "file_path": None, "download_path": SB_PATH}])
    got = await ResourcesRepository().get_owned_platform_ids(["vid-web"], "u1")

    assert got == {"vid-web"}, "下载过的素材被判成没下载 → 用户会重复下载"


@pytest.mark.asyncio
async def test_album_counts_as_owned_even_though_it_has_no_single_file(owned):
    """⭐ 相册**下载过**，必须算已拥有 —— 哪怕它没有单个文件。

    这条是本批的核心一课：如果这里图省事复用 ``resolve_resource_file_path``，
    它的目录守卫会把相册解析成 ``None``，相册于是被判成"没下载"，用户重复下载
    整本图集。同一个守卫在取路径场景是保护、在存在性场景是错误答案。

    生产实测漏判的 197 条里有 8 条正是相册 —— 复用取路径函数只能修好 189 条，
    相册那 8 条照样错。已在生产逐行对照过两种做法的答案。
    """
    from app.repositories.resources_repository import ResourcesRepository

    owned(
        [{"platform_id": "vid-album", "file_path": None, "download_path": ALBUM_PREFIX}]
    )
    got = await ResourcesRepository().get_owned_platform_ids(["vid-album"], "u1")

    assert got == {
        "vid-album"
    }, "相册被判成没下载 —— 存在性判断错误地套用了取路径的目录守卫"


@pytest.mark.asyncio
async def test_predicate_is_an_or_over_both_columns(owned):
    """谓词真的渲染成了两列的 OR，而不是只换了一列。

    直接断言编译出来的 SQL：这是"存在性留在 SQL 里"这条设计的可证伪形式，
    也排除了"改成只看 download_path"这种把 upload 素材弄丢的写法。
    """
    from app.repositories.resources_repository import ResourcesRepository

    session = owned([{"platform_id": "x", "file_path": FS_PATH, "download_path": None}])
    await ResourcesRepository().get_owned_platform_ids(["x"], "u1")

    assert "resources.file_path is not null" in session.sql
    assert "parsed_media.download_path is not null" in session.sql
    assert " or " in session.sql


@pytest.mark.asyncio
async def test_uploaded_resource_still_counts(owned):
    """正向对照：只有 ``resources.file_path`` 的素材没被改丢。"""
    from app.repositories.resources_repository import ResourcesRepository

    owned([{"platform_id": "vid-up", "file_path": FS_PATH, "download_path": None}])
    got = await ResourcesRepository().get_owned_platform_ids(["vid-up"], "u1")

    assert got == {"vid-up"}


@pytest.mark.asyncio
async def test_never_downloaded_stays_unowned(owned):
    """正向对照：两列都空仍然算"没下载"。

    修复不能靠"什么都算已下载"通过 —— 那会让用户再也下载不了新东西。
    """
    from app.repositories.resources_repository import ResourcesRepository

    owned([{"platform_id": "vid-new", "file_path": None, "download_path": None}])
    got = await ResourcesRepository().get_owned_platform_ids(["vid-new"], "u1")

    assert got == set()


@pytest.mark.asyncio
async def test_empty_input_short_circuits(owned):
    """空输入不查库（既有性质，别改坏）。"""
    from app.repositories.resources_repository import ResourcesRepository

    session = owned([{"platform_id": "x", "file_path": FS_PATH, "download_path": None}])
    assert await ResourcesRepository().get_owned_platform_ids([], "u1") == set()
    assert session.sql == ""


@pytest.mark.asyncio
async def test_existence_check_stays_a_single_query(owned):
    """一次批量查询覆盖整张歌单 —— 不退化成 N+1。

    这条钉住的是"为什么存在性不能逐行调 Python 解析器"：那样每首曲子一次
    parsed_media 查询，而这个方法的全部意义就是一次查完。
    """
    from app.repositories.resources_repository import ResourcesRepository

    rows = [
        {"platform_id": f"v{i}", "file_path": None, "download_path": SB_PATH}
        for i in range(25)
    ]
    session = owned(rows)

    calls = {"n": 0}
    original = session.execute

    async def _counting(stmt):
        calls["n"] += 1
        return await original(stmt)

    session.execute = _counting  # type: ignore[method-assign]

    got = await ResourcesRepository().get_owned_platform_ids(
        [r["platform_id"] for r in rows], "u1"
    )

    assert len(got) == 25
    assert calls["n"] == 1, "存在性判断退化成了 N+1"
