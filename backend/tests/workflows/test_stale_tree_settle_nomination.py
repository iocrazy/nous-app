"""清扫器兜底的**提名语句**：少一个谓词都不会报错，只会让兜底安静地退化。

这一条盯的不是「捞到了谁」，而是那条 SQL 里到底带了哪几个条件 —— 因为每一个缺失
都有一个静默的后果：

* 少 ``billing.charged_at IS NULL`` → 已收口的树每轮被重提名，而且配上 LIMIT 会把
  新树饿死（它们永远排不进来）；
* 少 ``parent_run_id IS NULL`` → 同一棵树被它的每个子行重复喂进收口；
* 少时间窗下界 → 把本机制**上线前**的历史树扫进来。2026-09-17 这件事真的发生了：
  上线首轮 81 棵 09-10~09-15 的历史树被提名并整棵扣了 125 分，因为那时它们一分钱都
  没扣过，``settle`` 那道「扣过钱没有」的正查放行了它们。下界现在取
  ``max(now − 7 天, 切换点)``；
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


# ── 下界必须是切换点（2026-09-17 事故） ─────────────────────────────────


def _captured_bounds(monkeypatch, cutover: str):
    """真跑一次那个 step，把它实际发出去的语句的时间绑定值捞出来。

    断在 step 上而不是 builder 上：builder 不认识切换点（它只忠实带上传进来的
    下界），把两个下界取晚的那一步在 step 里 —— 事故就发生在那一层，所以钉在那一层。
    """
    from contextlib import asynccontextmanager

    import app.core.config as config_mod
    import app.db.session as db_session

    monkeypatch.setattr(config_mod.settings, "AGENT_POINTS_TREE_CUTOVER", cutover)

    seen: list = []

    class _Session:
        async def execute(self, stmt):
            seen.append(stmt)

            class _Res:
                def all(self):
                    return []

            return _Res()

    @asynccontextmanager
    async def _read():
        yield _Session()

    monkeypatch.setattr(db_session, "read_scope", _read)
    return seen


async def test_the_nomination_never_reaches_back_past_the_cutover(monkeypatch):
    from sqlalchemy.dialects.postgresql import dialect

    from app.workflows.agent_runs_sweeper import force_settle_stale_pending_trees_step

    # 切换点在「7 天前」之后 —— 它必须赢，否则上线前的历史树又会被扫进来。
    cutover = datetime.now(timezone.utc) - timedelta(hours=6)
    seen = _captured_bounds(monkeypatch, cutover.isoformat().replace("+00:00", "Z"))

    assert await force_settle_stale_pending_trees_step() == 0
    assert len(seen) == 1
    bounds = sorted(
        v
        for v in seen[0].compile(dialect=dialect()).params.values()
        if isinstance(v, datetime)
    )
    assert len(bounds) == 2
    # 下界（较早的那个）就是切换点本身，不是 now − 7 天。
    assert abs((bounds[0] - cutover).total_seconds()) < 1, bounds


async def test_the_seven_day_window_still_applies_once_the_cutover_is_old(monkeypatch):
    """正对照：切换点变成陈年旧事之后，积压上界回到 7 天 —— 少了它，上一条用
    「永远只取切换点」也能通过，而那会让窗口无限长回去。"""
    from sqlalchemy.dialects.postgresql import dialect

    from app.workflows.agent_runs_sweeper import force_settle_stale_pending_trees_step

    seen = _captured_bounds(monkeypatch, "2020-01-01T00:00:00Z")
    assert await force_settle_stale_pending_trees_step() == 0
    bounds = sorted(
        v
        for v in seen[0].compile(dialect=dialect()).params.values()
        if isinstance(v, datetime)
    )
    expected = datetime.now(timezone.utc) - timedelta(days=7)
    assert abs((bounds[0] - expected).total_seconds()) < 60, bounds


async def test_an_unreadable_cutover_nominates_nothing(monkeypatch):
    """配置坏掉时不提名任何树 —— 别凭一个读不出来的边界去动余额。"""
    from app.workflows.agent_runs_sweeper import force_settle_stale_pending_trees_step

    seen = _captured_bounds(monkeypatch, "not-a-timestamp")
    assert await force_settle_stale_pending_trees_step() == 0
    assert seen == [], "一条语句都不该发出去"
