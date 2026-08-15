"""``schedule_state`` —— 一批定时**现在**还能不能按原样重投。

为什么需要这个函数（生产实证 2026-08-15）
=========================================
一批定时发布失败后，用户在记录页连点了三次 Retry。三次都返回 200，三次都真的
派发了 workflow，三次都在 ``validate_publish_intent`` 那一步被拒：

    publish intent rejected: scheduled_at must be at least ... from now

因为重投沿用批次里存着的 ``scheduled_at``，而那个时间早就过去了。界面上看起来
是"点了没反应"，实际是"每点一次都新失败一次"。

结构上不可能成功的按钮不该存在，而判断它可不可能成功的规则（下限是
``SCHEDULE_MIN_LEAD``）属于后端。这个模块把那条规则收成一个纯函数，让 router
的门与 API 回显的 ``schedule_state`` 字段共用同一份判据 —— 前端不再拿
``scheduled_at`` 自己减一个它猜的数。

边界的取舍
==========
**只看下限，不看上限**：超过 14 天（``SCHEDULE_TOO_FAR``）是等一等就会自己好
的，而下限只会越过越远。把两者都算成 ``unreachable`` 会让"太远了"被当成"没救
了"，于是给用户一个本不该出现的"立即发布"选项。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.distribution.publish_options import (
    SCHEDULE_MIN_LEAD,
    SCHEDULE_STATE_NONE,
    SCHEDULE_STATE_PENDING,
    SCHEDULE_STATE_UNREACHABLE,
    schedule_state,
)

NOW = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "delta,expected",
    [
        # 生产那一批的形状：定时到 07:20，重投时早已过去。
        (timedelta(hours=-20), SCHEDULE_STATE_UNREACHABLE),
        (timedelta(hours=-1), SCHEDULE_STATE_UNREACHABLE),
        (timedelta(0), SCHEDULE_STATE_UNREACHABLE),
        # 还没到、但已经近到传不完了 —— 同样必然被拒，所以同样算 unreachable。
        # 只判"时间是否已过去"会漏掉这一段，而这一段的失败长得一模一样。
        (SCHEDULE_MIN_LEAD - timedelta(minutes=1), SCHEDULE_STATE_UNREACHABLE),
        (SCHEDULE_MIN_LEAD, SCHEDULE_STATE_PENDING),  # 闭区间下界，与提交时同口径
        (SCHEDULE_MIN_LEAD + timedelta(minutes=1), SCHEDULE_STATE_PENDING),
        (timedelta(days=3), SCHEDULE_STATE_PENDING),
        # 超过 14 天：提交时会被 TOO_FAR 拒，但它**不是** unreachable ——
        # 时间只会让它变近。判成 unreachable 会把"等等就好"说成"没救了"。
        (timedelta(days=30), SCHEDULE_STATE_PENDING),
    ],
)
def test_lower_bound_is_the_only_edge_that_makes_a_schedule_unreachable(
    delta, expected
):
    assert schedule_state(NOW + delta, now=NOW) == expected


def test_no_schedule_is_not_a_schedule_problem():
    """立即发布的批次重投时**不该**被这道门碰到 —— 它本来就没有定时时间。"""
    assert schedule_state(None, now=NOW) == SCHEDULE_STATE_NONE


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-08-14T07:20:00+00:00", SCHEDULE_STATE_UNREACHABLE),
        ("2026-08-16T07:20:00Z", SCHEDULE_STATE_PENDING),  # PostgREST 那条路径的 Z 后缀
    ],
)
def test_iso_strings_are_read_too(raw, expected):
    """同一列在不同读路径上是 datetime 或 ISO 字符串。

    只认 datetime 的话，走 JSON 那条路径的调用方会静默拿到 ``none`` ——
    门在那条路径上等于不存在，而没有任何东西会报错。
    """
    assert schedule_state(raw, now=NOW) == expected


def test_unparseable_value_is_not_a_verdict():
    """认不出来的值 → ``none``，不抛。

    这个函数在**序列化列表接口**的路径上，抛异常会把整页记录打成 500。
    真正的格式校验在写入侧（``validate_scheduled_at`` 拒 naive datetime），
    不在这里重做一遍。
    """
    assert schedule_state("garbage", now=NOW) == SCHEDULE_STATE_NONE
    assert schedule_state("", now=NOW) == SCHEDULE_STATE_NONE


def test_naive_datetime_is_read_as_utc_rather_than_ignored():
    """落库的是 timestamptz，naive 值只可能来自某条降级路径。

    此时"当没定时"最糟 —— 那正好让门对这一行失效，而门存在的理由就是拦住
    这一行。当 UTC 读至少是个确定的、可解释的行为。
    """
    naive_past = (NOW - timedelta(hours=1)).replace(tzinfo=None)
    assert schedule_state(naive_past, now=NOW) == SCHEDULE_STATE_UNREACHABLE
