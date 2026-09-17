"""清扫器兜底的**提名语句**：少一个谓词都不会报错，只会让兜底安静地退化。

这一条盯的不是「捞到了谁」，而是那条 SQL 里到底带了哪几个条件 —— 因为每一个缺失
都有一个静默的后果：

* 少 ``billing.charged_at IS NULL`` → 已收口的树每轮被重提名，而且配上 LIMIT 会把
  新树饿死（它们永远排不进来）；
* 少 ``parent_run_id IS NULL`` → 同一棵树被它的每个子行重复喂进收口；
* 少时间窗上界 → 把本机制**上线前**的历史树扫进来，那些树在旧口径下已经逐 run
  扣过钱；
* ``ORDER BY`` 写成升序 → 窗口里攒下的老树顶满 LIMIT，新树同样饿死。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.workflows.agent_runs_sweeper import _stale_tree_candidates_stmt

pytestmark = [pytest.mark.unit]


def _compiled():
    from sqlalchemy.dialects.postgresql import dialect

    now = datetime.now(timezone.utc)
    stmt = _stale_tree_candidates_stmt(
        older_than=now - timedelta(hours=2), newer_than=now - timedelta(days=7)
    )
    return stmt.compile(dialect=dialect())


def _sql() -> str:
    return str(_compiled())


def test_the_nomination_skips_trees_that_are_already_settled():
    """⚠️ jsonb 路径的键是**绑定参数**，不出现在 SQL 文本里 —— 只断言文本会假绿。
    两侧都看：语句里有 ``->> … IS NULL``，绑定值里有 ``billing`` / ``charged_at``。"""
    compiled = _compiled()
    assert "IS NULL" in str(compiled)
    bound = set(compiled.params.values())
    assert {"billing", "charged_at"} <= bound, bound


def test_the_nomination_only_asks_about_root_rows():
    assert "parent_run_id IS NULL" in _sql()


def test_the_nomination_is_bounded_on_both_ends_of_time():
    sql = _sql()
    assert sql.count("ended_at") >= 3, "两个时间边界 + 排序"
    assert "LIMIT" in sql


def test_the_newest_trees_are_nominated_first():
    """升序 + LIMIT 会让窗口里攒下的老树把新结束的树饿死。"""
    assert "ended_at DESC" in _sql()


def test_running_rows_are_never_nominated():
    assert "status !=" in _sql()
