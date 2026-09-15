"""自动化取标签必须说清「谁的那一份」（2026-09-14 用户裁定）。

初始化标签正在从「全局共享一行」改成「每人一份」：它们不是系统标签，是发给
你的初始标签，你自己的那份自己改。分叉之后同一个 slug 在库里有 N 行，于是
**不带 user_id 的 slug 查询会把 A 的标签挂到 B 的资源上** —— 这个文件钉的就是
不许再出现无主查询。

两组：

* **每个自动化入口都带上了归属人** —— 抓取意图带 ``auth.user_id``，自动打标带
  资源主人。这是分叉迁移能安全上线的前提。
* **兜底那一臂的方向是对的** —— 迁移与后端部署没有先后保证（CLAUDE.md「已知
  缺口」），所以两种 schema 都要能跑：自己的那份永远赢过共享行。方向反了会在
  分叉后长期错挂而没有任何报错。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

OWNER = "11111111-1111-1111-1111-111111111111"


def _null_scope(*_a, **_kw):
    """``system_request_scope`` 的替身 —— 单测里没有真 session 要挂 scope。"""

    class _Ctx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    return _Ctx()


# --------------------------------------------------------------------------
# 入口都带归属人
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intent_resolution_scopes_to_the_caller():
    """抓取意图按**发起人**取标签，不是按 slug 随便取一行。"""
    from app.api import media_fetch_helpers as h

    repo = MagicMock()
    repo.get_tag_ids_by_slugs = AsyncMock(return_value={"transcript": 11})
    with patch.object(h, "get_tags_repository", return_value=repo):
        ids = await h.resolve_intent_tag_ids(
            transcribe=True, summarize=False, analyze=False, user_id=OWNER
        )

    assert ids == ["11"]
    repo.get_tag_ids_by_slugs.assert_awaited_once_with(["transcript"], OWNER)


@pytest.mark.asyncio
async def test_intent_resolution_refuses_to_run_without_a_user():
    """``user_id`` 是必填关键字：漏传是 TypeError，不是静默取到别人的标签。

    这条守的是回归。一个可选参数在这里等于「默认跨用户」—— 而那正是分叉之后
    最贵、最不会报错的一类错误。
    """
    from app.api import media_fetch_helpers as h

    with pytest.raises(TypeError):
        await h.resolve_intent_tag_ids(transcribe=True, summarize=False, analyze=False)


@pytest.mark.asyncio
async def test_auto_classification_uses_the_resource_owners_copy():
    """自动打标查的是**资源主人**那份分类标签，不是触发方、也不是任意一行。"""
    from app.services.ai.visual import classification_service as cs

    repo = MagicMock()
    repo.resolve_media_id_to_resource_id = AsyncMock(return_value="900")
    repo.get_resource_owner_id = AsyncMock(return_value=OWNER)
    repo.get_automation_tag = AsyncMock(return_value={"id": 7, "name": "Food"})
    repo.add_tag_to_resource = AsyncMock()

    result = cs.ClassificationResult(
        primary_tag="Food", confidence=0.9, secondary_tag=None, source="auto"
    )
    with (
        patch.object(cs, "get_tags_repository", return_value=repo),
        patch.object(
            cs.ClassificationService, "classify_by_keywords", return_value=result
        ),
        patch.object(cs, "system_request_scope", _null_scope),
    ):
        await cs.ClassificationService.auto_tag_media(media_id=500, title="Food time")

    repo.get_resource_owner_id.assert_awaited_once_with("900")
    repo.get_automation_tag.assert_awaited_once_with("Food", OWNER)


# --------------------------------------------------------------------------
# 归属人从哪来
# --------------------------------------------------------------------------
#
# 这里曾经有两条钉「共享行兜底方向」的用例（自己那份要赢过 user_id IS NULL 的
# 那行；分叉前也要还能取到共享行）。兜底本身是分期部署的脚手架，迁移上线对账
# 之后已随实现一起删除 —— 钉一个不存在的分支就是让下一个人以为它还在。取代
# 它们的是文件末尾那两条：查询里不许再出现 IS NULL，以及「不知道是谁」返回空。


@pytest.mark.asyncio
async def test_resource_owner_reads_creator_id_not_user_id():
    """``resources`` 的归属列是 ``creator_id``；写 ``user_id`` 会拿 PG 42703。"""
    from app.repositories.tags_repository import TagsRepository

    captured = {}

    class _Session:
        async def scalar(self, stmt):
            captured["sql"] = str(stmt)
            return OWNER

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch("app.repositories.tags_repository.read_scope", lambda: _Session()):
        owner = await TagsRepository().get_resource_owner_id("900")

    assert owner == OWNER
    assert "resources.creator_id" in captured["sql"]


# ---------------------------------------------------------------------------
# 共享行兜底已删除 —— 「不知道是谁」的答案是「一个都不给」
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slug_lookup_no_longer_reaches_for_a_shared_row():
    """查询里不许再出现 ``user_id IS NULL`` 那一臂。

    它是为分期部署存在的：迁移与后端部署没有先后保证，那段时间共享行是唯一
    存在的行。分叉迁移已上线并对账（生产无主标签行为 0），那一臂再无可命中的
    行 —— 留着它只意味着「将来某天冒出一个无主行，自动化会去挂它」。
    """
    from app.repositories.tags_repository import TagsRepository

    captured = {}

    class _Result:
        @staticmethod
        def all():
            return [("transcript", 99)]

    class _Session:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch("app.repositories.tags_repository.read_scope", lambda: _Session()):
        found = await TagsRepository().get_tag_ids_by_slugs(["transcript"], OWNER)

    assert found == {"transcript": 99}
    sql = captured["sql"]
    assert "tags.user_id = " in sql, "必须按人限定"
    assert "IS NULL" not in sql, "共享行兜底应当已经删除"
    assert "ORDER BY" not in sql, "只剩一行可命中，不需要排序来挑赢家"


@pytest.mark.asyncio
async def test_no_user_means_no_tag_not_someone_elses():
    """``user_id`` 为空时返回空，而不是「不过滤 → 随便给一行」。

    删掉兜底那一臂之前，无 user 的查询是**不带 where 的**，于是拿到的是库里
    任意一个持有该 slug 的人的标签 —— 分叉之后这正是跨用户错挂。
    """
    from app.repositories.tags_repository import TagsRepository

    class _Session:
        async def execute(self, stmt):  # pragma: no cover - 不该被调用
            raise AssertionError("没有 user_id 时不该查库，更不该挂上别人的标签")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    repo = TagsRepository()
    with patch("app.repositories.tags_repository.read_scope", lambda: _Session()):
        assert await repo.get_tag_ids_by_slugs(["transcript"], "") == {}
        assert await repo.get_tag_by_slug("transcript", "") is None
        assert await repo.get_automation_tag("Food", "") is None


@pytest.mark.asyncio
async def test_a_resource_with_no_owner_is_reported_not_silently_skipped():
    """没有主人 → 不打标，**并且留下一条日志**。

    「不知道挂谁的」是一个真实结果，不是无事发生。仓库的纪律是触发路径不许
    silent no-op；这里唯一的痕迹就是这条 WARNING。

    这条同时钉住 ``logger`` 这个名字在两个模块里真的存在 —— 第一版
    ``analyze_l1`` 里它压根没 import，而那是个只在真的撞上无主资源时才会炸的
    NameError。
    """
    from app.services.ai.visual import classification_service as cs

    repo = MagicMock()
    repo.resolve_media_id_to_resource_id = AsyncMock(return_value="900")
    repo.get_resource_owner_id = AsyncMock(return_value=None)
    repo.get_automation_tag = AsyncMock()
    repo.add_tag_to_resource = AsyncMock()

    result = cs.ClassificationResult(
        primary_tag="Food", confidence=0.9, secondary_tag=None, source="auto"
    )
    with (
        patch.object(cs, "get_tags_repository", return_value=repo),
        patch.object(
            cs.ClassificationService, "classify_by_keywords", return_value=result
        ),
        patch.object(cs, "system_request_scope", _null_scope),
        patch.object(cs.logger, "warning") as warned,
    ):
        added = await cs.ClassificationService.auto_tag_media(
            media_id=500, title="Food time"
        )

    assert added == []
    repo.get_automation_tag.assert_not_awaited()
    repo.add_tag_to_resource.assert_not_awaited()
    warned.assert_called_once()
    assert "creator_id" in warned.call_args.args[0]


def test_analyze_l1_has_a_logger_to_call():
    """``analyze_l1`` 的无主分支要打日志 —— 那个名字得真的存在。

    单独一条，因为那段代码只有在撞上无主资源时才会执行，NameError 会一直潜伏
    到那一刻。"""
    import app.workflows.analyze_l1 as m

    assert hasattr(m, "logger")
