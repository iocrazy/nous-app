"""索引比较器自己的单测 —— 不碰数据库。

为什么**单独一个文件**而不是塞进 `test_orm_indexes_integration.py`：那个文件
在 CI 里由 `pytest-no-full-skip.sh` 包着跑，靠「整文件跳过 = 失败」来保证
Postgres 真的起来了。往里面加几条**永远通过**的单测，就等于给那个包装器喂了
一个 `passed`，于是「库没起来」会被读成一次干净的绿 —— 实测过：加进去之后
无 DSN 跑出来是 `6 passed, 5 skipped` + exit 0（修之前是 5 skipped + exit 1）。
守卫的守卫不能被这样悄悄拆掉，所以两边分开住。

这里比的是**比较器本身**：喂合成的库内行给 `_mismatched`，证明它真的在看
每一条轴。fix round 1 之前唯一性与部分索引两条轴就是这么漏掉的 —— 数据一直
取着（`indisunique`、`pg_get_indexdef` 的正文），只是没人比，而没有库的环境里
这种缺陷完全看不见。

每条用例都先给一个「四条轴全一致」的对照，再只动一个字段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from tests.db.test_orm_indexes_integration import DeclaredIndex, _mismatched

pytestmark = pytest.mark.unit


def _synthetic(**over: Any) -> Tuple[List[DeclaredIndex], Dict[str, Dict[str, Any]]]:
    """一条声明 + 一行与它**完全一致**的库内索引；`over` 只改库侧。"""
    declared = [
        DeclaredIndex(
            name="ix_probe",
            table="probes",
            columns=("a", "b"),
            expressions=0,
            unique=True,
            partial=True,
            kind="index",
        )
    ]
    row: Dict[str, Any] = {
        "table": "probes",
        "columns": ("a", "b"),
        "key_count": 2,
        "unique": True,
        "defn": "CREATE UNIQUE INDEX ix_probe ON public.probes "
        "USING btree (a, b) WHERE (deleted_at IS NULL)",
    }
    row.update(over)
    return declared, {"ix_probe": row}


def test_an_identical_pair_is_not_reported() -> None:
    """对照组：四条轴全一致时一个字都不报。没有这条，下面每条红都可能是
    比较器对**任何**输入都报红。"""
    declared, live = _synthetic()
    assert _mismatched(live, declared) == []


def test_uniqueness_is_compared() -> None:
    """M1：库侧 `indisunique` 翻成 false 必须转红。

    修之前 `DeclaredIndex.unique` 与 `row["unique"]` 两个值都在手里，却从来
    没有被放在一起比过 —— 把 `uq_loadout_default` 的 `indisunique` 改掉，
    整条门禁照样绿。
    """
    declared, live = _synthetic(
        unique=False,
        defn="CREATE INDEX ix_probe ON public.probes USING btree (a, b) "
        "WHERE (deleted_at IS NULL)",
    )
    found = _mismatched(live, declared)
    assert [why for _n, why in found if why.startswith("唯一性")] == [
        "唯一性 orm=True live=False :: CREATE INDEX ix_probe ON public.probes "
        "USING btree (a, b) WHERE (deleted_at IS NULL)"
    ], found


def test_partial_predicate_presence_is_compared() -> None:
    """M2：声明成部分索引而库里是全表（或反过来）必须转红。

    比的是**有无**不是谓词文本 —— 见文件上方的契约。`idx_assets_scope_type`
    与 `idx_assets_scope_library` 就是同表同列、只差谓词的一对：不比这条轴，
    把其中一个的 WHERE 弄丢没有任何东西会说话。
    """
    declared, live = _synthetic(
        defn="CREATE UNIQUE INDEX ix_probe ON public.probes USING btree (a, b)"
    )
    found = _mismatched(live, declared)
    assert [why for _n, why in found if why.startswith("部分索引")] == [
        "部分索引 orm=True live=False :: CREATE UNIQUE INDEX ix_probe "
        "ON public.probes USING btree (a, b)"
    ], found


def test_the_predicate_TEXT_is_deliberately_not_compared() -> None:
    """契约的另一半：谓词**内容**不同不转红。

    这不是疏忽，是上方契约写明的取舍。写成断言，是为了让将来想「顺手把谓词
    也比一下」的人先看见这条测试、再决定是不是真要承担那份噪声。
    """
    declared, live = _synthetic(
        defn="CREATE UNIQUE INDEX ix_probe ON public.probes USING btree (a, b) "
        "WHERE (something_else IS NOT NULL)"
    )
    assert _mismatched(live, declared) == []


def test_column_ORDER_is_compared_not_just_the_set() -> None:
    """L1：同一组列换个顺序是**另一个索引**。

    `(a, b)` 服务「按 a 过滤」与「按 a 过滤再按 b 排序」，`(b, a)` 两个都
    不服务。修之前两侧都过 `frozenset`，顺序在比较之前就被扔掉了 —— 而库侧
    的 `array_agg(… ORDER BY k.ord)` 一直是有序的，扔掉纯属浪费。
    """
    declared, live = _synthetic(
        columns=("b", "a"),
        defn="CREATE UNIQUE INDEX ix_probe ON public.probes USING btree (b, a) "
        "WHERE (deleted_at IS NULL)",
    )
    found = _mismatched(live, declared)
    assert [why for _n, why in found if why.startswith("列")] == [
        "列 orm=['a', 'b'] live=['b', 'a'] :: CREATE UNIQUE INDEX ix_probe "
        "ON public.probes USING btree (b, a) WHERE (deleted_at IS NULL)"
    ], found


def test_orthogonal_differences_are_each_reported() -> None:
    """一次不一致可以同时是好几件事，每件都要独立说出来。

    CLAUDE.md「正交的结果各自独立上报」—— 把一条轴的上报嵌进另一条的分支里，
    调用方会把「唯一性没了**而且**索引缩了半张表」读成一件小事。
    """
    declared, live = _synthetic(
        unique=False,
        columns=("b", "a"),
        defn="CREATE INDEX ix_probe ON public.probes USING btree (b, a)",
    )
    kinds = {why.split(" ")[0] for _n, why in _mismatched(live, declared)}
    assert kinds == {"唯一性", "部分索引", "列"}
