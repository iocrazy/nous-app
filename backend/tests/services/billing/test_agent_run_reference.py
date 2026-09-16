"""``point_transactions.reference_type = 'agent_run'`` 只能有一个拼写。

写方（``token_billing`` 把它交给 ``PointsService``）与三个读方（议题 rollup、
议题聊天帧、``/ai-library/runs/costs``）必须逐字一致，否则读方一条都查不到 ——
``token_billing`` 那段注释已经解释过这个陷阱的由来（跟着 ``trigger`` 走会让这张
表按触发方式碎成若干值）。第五处是 mig 474 的 partial 索引谓词：它是 SQL 字符串，
import 不进去，所以反过来断言它逐字包含常量值。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.models.billing import PointTransactions
from app.services.billing.agent_run_reference import AGENT_RUN_REFERENCE_TYPE

pytestmark = pytest.mark.unit

_BACKEND = pathlib.Path(__file__).resolve().parents[3]
#: 允许出现字面量的两处：常量自己的模块，与 ORM 里那条没法 import 的索引谓词。
_ALLOWED = {
    "app/services/billing/agent_run_reference.py",
    "app/models/billing.py",
}


def test_the_value_is_the_one_the_billing_rpc_writes():
    assert AGENT_RUN_REFERENCE_TYPE == "agent_run"


def test_the_partial_index_predicate_spells_it_the_same_way():
    """谓词与查询谓词不一致时 planner 用不上索引，而没有任何东西会说出来。"""
    index = next(
        i
        for i in PointTransactions.__table__.indexes
        if i.name == "idx_point_transactions_agent_run_consume"
    )
    where = str(index.dialect_options["postgresql"]["where"])
    assert f"reference_type = '{AGENT_RUN_REFERENCE_TYPE}'" in where


#: 只盯**积分账那两个关键字**上的字面量。``"agent_run"`` 这个词在别处另有其人
#: （workflow 超时策略、任务种类…），整仓 grep 会把它们全扫成误报，于是这条守卫
#: 很快就会被加进允许名单直到形同虚设。
_BILLING_KEYWORD = re.compile(
    r'\b(?:reference_type|action_type|reference_type_filter)\s*=\s*"([a-z_]+)"'
)


def test_no_module_hardcodes_it_on_a_billing_keyword():
    """新写方 / 新读方必须 import 常量，不许再抄一份字面量。"""
    offenders = [
        f"{path.relative_to(_BACKEND)}:{n}"
        for path in sorted((_BACKEND / "app").rglob("*.py"))
        if str(path.relative_to(_BACKEND)) not in _ALLOWED
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for m in _BILLING_KEYWORD.finditer(line)
        if m.group(1) == AGENT_RUN_REFERENCE_TYPE
    ]
    assert offenders == []


def test_the_guard_itself_can_see_a_hardcoded_one():
    """反向对照：守卫的正则真的认得出它要拦的那个形状。"""
    m = _BILLING_KEYWORD.search('    reference_type="agent_run", reference_ids=[]')
    assert m is not None and m.group(1) == AGENT_RUN_REFERENCE_TYPE
