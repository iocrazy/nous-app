"""标签 slug 解耦（mig 467）：自动化不再拿用户可见的显示名当主键。

这次重构的理由是一个具体的坏处：系统标签之所以只读，是因为 ~24 处代码按英文
``name`` 查它们，而**每一处查不到都是静默失败** —— AI 不跑、不打标，日志里最多
一条 WARNING。所以「让用户能改自己的标签名」这件事，前提是先把自动化的键换成
用户碰不到的 ``slug``。

这个文件钉的就是那个前提本身。两组：

* **改名之后自动化照常** —— 用 slug 查，行里的 name 是什么都不影响。这是本次
  重构唯一的目的，也是唯一能证明它做到了的断言。
* **顺带堵上的洞** —— 触发链以前读的是 name 且**不过滤 type**，所以手建一个叫
  "Summary" 的普通标签就能让模型跑起来（真金白银）。现在读 slug，而 slug 只有
  迁移写得进去。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_USER = "11111111-1111-1111-1111-111111111111"

# --------------------------------------------------------------------------
# 意图 → 标签 id：按 slug 解析
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renamed_pipeline_tag_still_resolves():
    """用户把 Transcript 改成「转录啦」，意图照样挂得上标签。

    这就是整次重构要换来的东西。旧实现按 name 查，此刻会查空、WARNING、跳过。
    """
    from app.api import media_fetch_helpers as h

    repo = MagicMock()
    # 仓库按 slug 查，所以行的显示名叫什么都与它无关。
    repo.get_tag_ids_by_slugs = AsyncMock(return_value={"transcript": 11})
    with patch.object(h, "get_tags_repository", return_value=repo):
        ids = await h.resolve_intent_tag_ids(
            transcribe=True, summarize=False, analyze=False, user_id=_USER
        )

    assert ids == ["11"]
    repo.get_tag_ids_by_slugs.assert_awaited_once_with(["transcript"], _USER)


@pytest.mark.asyncio
async def test_intent_resolution_never_passes_a_display_name():
    """三个意图传下去的全是 slug，一个显示名都不许出现。

    这条守的是回归：有人把 ``INTENT_TAG_SLUGS`` 的值改回 "Transcript" 这类
    英文名，链路会重新变得怕改名，而单测不会自己红。
    """
    from app.api import media_fetch_helpers as h

    repo = MagicMock()
    repo.get_tag_ids_by_slugs = AsyncMock(return_value={})
    with patch.object(h, "get_tags_repository", return_value=repo):
        await h.resolve_intent_tag_ids(
            transcribe=True, summarize=True, analyze=True, user_id=_USER
        )

    (passed, _uid), _ = repo.get_tag_ids_by_slugs.await_args
    assert passed == ["transcript", "summary", "analyze"]
    assert all(s == s.lower() for s in passed), "slug 必须是小写稳定键，不是显示名"


# --------------------------------------------------------------------------
# 触发链读取侧：slug-only
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_reader_asks_for_slugs_and_ignores_slugless_rows():
    """读取侧只取有 slug 的行。

    旧实现 ``select(Tags.name)`` 没有任何 type 过滤，于是任何叫 "Summary" 的
    标签都能触发摘要 —— 用户手建一个就能花掉模型预算。slug 只有迁移写得进去，
    所以「有 slug」本身就是授权。
    """
    import app.tasks.download_helpers as dh

    captured = {}

    class _Result:
        @staticmethod
        def all():
            return [("summary",), ("transcript",)]

    class _Session:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch("app.db.session.read_scope", lambda: _Session()):
        slugs = await dh.read_resource_tag_slugs("42")

    assert slugs == {"summary", "transcript"}
    sql = captured["sql"]
    assert "tags.slug" in sql, "读的必须是 slug 列"
    assert "tags.slug IS NOT NULL" in sql, "没有 slug 的普通标签不得进入触发判断"
    assert "tags.name" not in sql, "显示名不该再参与触发判断"


# --------------------------------------------------------------------------
# 自动分类：slug 优先、显示名兜底
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_automation_tag_prefers_slug():
    """策展分类标签改名后，按小写 slug 仍然命中。"""
    from app.repositories.tags_repository import TagsRepository

    repo = TagsRepository()
    with (
        patch.object(repo, "get_tag_by_slug", AsyncMock(return_value={"id": 7})),
        patch.object(repo, "get_tag_by_name", AsyncMock()) as by_name,
    ):
        assert await repo.get_automation_tag("Food", _USER) == {"id": 7}
    by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_automation_tag_falls_back_to_name():
    """13 个策展标签以外的类别仍走显示名。

    视觉分析的 category 词表存在库里的 prompt 行里，可能吐出策展集合以外的词，
    那时匹配用户同名标签是**今天就有的行为**。本次重构的目标是改名不坏事，不是
    顺手砍掉这条路 —— 砍它是另一个决定。
    """
    from app.repositories.tags_repository import TagsRepository

    repo = TagsRepository()
    with (
        patch.object(repo, "get_tag_by_slug", AsyncMock(return_value=None)),
        patch.object(repo, "get_tag_by_name", AsyncMock(return_value={"id": 9})),
    ):
        assert await repo.get_automation_tag("Cooking", _USER) == {"id": 9}


@pytest.mark.asyncio
async def test_automation_tag_lowercases_before_the_slug_lookup():
    """类别名是 ``Food``，slug 是 ``food``；大小写由这里统一，不推给调用方。"""
    from app.repositories.tags_repository import TagsRepository

    repo = TagsRepository()
    with (
        patch.object(
            repo, "get_tag_by_slug", AsyncMock(return_value={"id": 1})
        ) as by_slug,
        patch.object(repo, "get_tag_by_name", AsyncMock()),
    ):
        await repo.get_automation_tag("Tutorial", _USER)
    by_slug.assert_awaited_once_with("tutorial", _USER)
