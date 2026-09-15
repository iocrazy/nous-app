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
# 兜底那一臂的方向
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slug_lookup_prefers_the_callers_own_row_over_the_shared_one():
    """同一个 slug 既有共享行又有自己那行时，取自己那行。

    分叉迁移与后端部署谁先落地不确定，所以两种形态都要能跑。方向反了不会报错
    —— 只会长期把共享行（迁移后甚至是别人的行）挂上去。
    """
    from app.repositories.tags_repository import TagsRepository

    captured = {}

    class _Result:
        @staticmethod
        def all():
            # DB 按 ORDER BY 给的顺序：自己的那行在前。
            return [("transcript", 99), ("transcript", 11)]

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

    assert found == {"transcript": 99}, "先出现的（自己那行）必须赢"
    # 方向必须钉死：``IS NULL ASC`` = false(自己那行) 在前、true(共享行) 在后。
    # 写成 DESC 一样能跑、一样不报错，只是长期挂错标签 —— 所以断言的是 SQL
    # 里的方向本身，不是桩 session 还回来的顺序（那是我自己写的，证明不了什么）。
    assert (
        "ORDER BY public.tags.slug, public.tags.user_id IS NULL ASC" in captured["sql"]
    ), "自己那份必须排在共享行前面"


@pytest.mark.asyncio
async def test_slug_lookup_still_finds_the_shared_row_before_the_fork():
    """分叉迁移还没跑时，库里只有共享行 —— 照样要取到。"""
    from app.repositories.tags_repository import TagsRepository

    class _Result:
        @staticmethod
        def all():
            return [("summary", 22)]

    class _Session:
        async def execute(self, stmt):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch("app.repositories.tags_repository.read_scope", lambda: _Session()):
        found = await TagsRepository().get_tag_ids_by_slugs(["summary"], OWNER)

    assert found == {"summary": 22}


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
