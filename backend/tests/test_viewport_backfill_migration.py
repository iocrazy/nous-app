"""mig 434 的候选窗口尺寸必须与 Python 侧逐字一致。

为什么需要这条测试
==================
候选表在两处各写了一份：`account_environment.SESSION_VIEWPORTS`（新账号绑定时
用）和 mig 434 的 `VALUES`（回填已存在的账号用）。SQL 调不到 Python，这个副本
无法避免 —— 能避免的是它**悄悄漂移**。

漂移的后果不是报错。Playwright 设了 viewport 之后会把 `screen` 也覆盖成同样的
值，所以这个数会同时作为"屏幕分辨率"被平台读到；一个只存在于我们数据库里的
分辨率（比如 1920x960）**恰恰是一个把自己标出来的信号**，而它会在两个入口给出
不同答案时静默产生。

⚠️ 这条测试读的是**迁移文件本身**，不是某个 Python 常量的副本 —— 副本的副本
不构成守卫。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.distribution.account_environment import SESSION_VIEWPORTS

pytestmark = pytest.mark.unit

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "434_account_environments_viewport_backfill.sql"
)

#: `(idx, w, h)` —— 只匹配 VALUES 里那种三元组，注释里的尺寸不会被误抓
#: （注释是 `--` 开头的散文，不含这个形状）。
_TUPLE = re.compile(r"\((\d+),\s*(\d+),\s*(\d+)\)")


def _sql_viewports() -> list[tuple[int, int]]:
    text = MIGRATION.read_text(encoding="utf-8")
    body = text[text.index("FROM (VALUES") :]
    body = body[: body.index(") AS v(idx, w, h)")]
    rows = sorted((int(idx), int(w), int(h)) for idx, w, h in _TUPLE.findall(body))
    return [(w, h) for _idx, w, h in rows]


def test_the_migration_file_is_actually_there():
    """正向对照。少了它，下面每一条都能在文件被改名后靠空列表通过。"""
    assert MIGRATION.exists(), MIGRATION


def test_the_backfill_candidates_match_the_python_source_exactly():
    """**本文件的守卫。** 顺序也算 —— 两处的第 n 个必须是同一个尺寸。"""
    assert _sql_viewports() == [tuple(vp) for vp in SESSION_VIEWPORTS]


def test_the_parser_actually_found_something():
    """再一条正向对照：解析器坏掉时返回空列表，而空列表跟"两边都空"长得一样。"""
    assert len(_sql_viewports()) == 6


def test_every_candidate_fits_the_measured_bounds():
    """下界 1280x720 是 DOM 自动化已知可用的尺寸，上界 1920x1080 是 Xvfb 屏幕。

    这两条也写在 mig 424 的 CHECK 约束里；在这里再断言一次，是因为约束只在
    写入时报错，而这条在**改候选表时**就报错。
    """
    for w, h in _sql_viewports():
        assert 1280 <= w <= 1920, (w, h)
        assert 720 <= h <= 1080, (w, h)


def test_the_backfill_only_touches_rows_that_never_had_a_viewport():
    """回填的是"从来没有过值"，不是"换一个值"。

    一个每次登录指纹都在变的账号，比一个指纹固定的账号更可疑 —— 这正是
    `pin_environment` 拒绝提供 update 入口的理由。所以这条迁移的 WHERE 必须
    只命中 NULL；断言写在这里，因为 SQL 的这个性质没有别的自动检查会看它。
    """
    text = MIGRATION.read_text(encoding="utf-8")
    where = text[text.index("FROM public.account_environments ae") :]
    where = where[: where.index("),")]
    assert "viewport_width IS NULL" in where
    assert "viewport_height IS NULL" in where
    # 且没有任何"无条件更新"的口子
    assert "WHERE TRUE" not in text.upper()
