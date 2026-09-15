"""索引门禁：ORM 声明的每一个索引，库里都必须真有一个同名同列的。

WHY THIS FILE EXISTS
────────────────────
`test_schema_drift.py` 管表 / 列 / 可空 / 类型 / 外键 —— **不管索引**。于是
`__table_args__` 里的 `Index(...)` 是这套模型里唯一**没有任何东西对账**的声明：
写错名字、写漏一列、指着一个从来没建过的索引，两边都不会有人说话。3a Task 1
的账本原话是「新加的 `test_the_orm_mirrors_*` 是拿 ORM 比自己，写错的索引表达式
两处都看不见」——本文件就是那张票（C1）。

**为什么这不是纯洁癖。** ORM 的索引声明是读代码的人判断「这条查询走不走索引」
的依据。声明与库不一致时，它不会让任何东西报错，只会让每一个后来读它的人对
生产的性能形状产生一个错误的、无法证伪的印象 —— 而且正因为没人对账，这个印象
会被后续的查询设计继续引用。

方向是**单向**的，这是刻意的：

* **ORM → 库：零容忍。** 声明了就必须存在，名字与列集合都得对上。
* **库 → ORM：只计数、只打印，不红。** 迁移里手写的索引有几百个本就没有 ORM
  镜像（每张表的 pkey 就是一大批），把它们判成漂移只会让这条守卫在第一天就被
  加一个包罗万象的豁免，然后退化成摆设。

⚠️ 「一个都没扫到」必须与「没有漂移」分开 —— 两个方向各有一条下限断言钉住
扫描本身真的跑起来了（同 CLAUDE.md「探针够不着目标 ≠ 目标是坏的」）。

Setup：指向任何一个按 CI 方式建起来的 Postgres（ci_bootstrap.sql →
schema_baseline.sql → watermark 之上的迁移）：

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_orm_indexes_integration.py -v

INTEGRATION_DATABASE_URL 未设时干净跳过（本地无库）；CI 里由
`.github/workflows/schema-drift.yml` 经 `pytest-no-full-skip.sh` 跑，整文件跳过
即判失败。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, NamedTuple, Tuple

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


# ── 五条轴 ───────────────────────────────────────────────────────────────
#
# 一个名字**一条一条轴**地对账。这几个常量同时是两样东西：报告行的开头词，
# 和豁免名单里 `axes` 的取值 —— **故意是同一个常量**，这样「报告里说它在哪条
# 轴上漂移」与「豁免放行的是哪条轴」不可能各说各话。
AXIS_MISSING = "缺失"
AXIS_TABLE = "表"
AXIS_COLUMNS = "列"
AXIS_UNIQUE = "唯一性"
AXIS_PARTIAL = "部分索引"

#: 全部合法取值。豁免里写了别的词（错别字、想当然的新轴）会被
#: `test_allowlist_axes_come_from_the_vocabulary` 直接拒绝 —— 一个拼错的轴名
#: 永远匹配不上任何漂移，于是那条豁免既不生效也不报错，是最坏的一种。
AXES = frozenset({AXIS_MISSING, AXIS_TABLE, AXIS_COLUMNS, AXIS_UNIQUE, AXIS_PARTIAL})


class Drift(NamedTuple):
    """一条漂移：谁、哪条轴、人话。"""

    name: str
    axis: str
    why: str


# ── 这条门禁到底比什么（契约，读豁免之前先读这个）──────────────────────
#
# **比**（ORM 声明 → 库，逐条零容忍，**每条轴各自判定**）：
#   1. 索引存在（按名字）           → 轴 `缺失`
#   2. 所在的表                     → 轴 `表`
#   3. **有序**的列清单 —— `(a, b)` 与 `(b, a)` 是两个不同的索引 → 轴 `列`
#   4. 唯一性（`indisunique`）      → 轴 `唯一性`
#   5. 是不是部分索引（**只比有无**）→ 轴 `部分索引`
#
# **不比**（每条都有理由，不是懒）：
#   * **谓词文本** —— SQLAlchemy 的 `text("deleted_at IS NULL")` 与
#     `pg_get_indexdef` 吐的 `WHERE (deleted_at IS NULL)` 是同一件事的两种
#     序列化。逐字比会把括号、空格、类型转换判成漂移，噪声淹掉信号。
#   * **opclass / 排序方向 / NULLS FIRST|LAST** —— 同上，两侧不同源。
#     所以 `(user_id, created_at DESC)` 与 `(user_id, created_at)` 在这条
#     门禁下算一致；DESC 的得失要靠 review，不靠这里。
#   * **INCLUDE 列 / 索引方法（btree / gin / …）/ 存储参数** —— 今天 ORM
#     侧基本不声明它们，加进来只会得到一片假红。真要管，先让模型声明。
#   * **库里有而 ORM 没声明的索引** —— 方向性的刻意选择，见文件头。
#
# ── RATCHET ALLOWLIST（已知漂移 —— 只许缩短，不许增长）─────────────────
#
# 同 `test_schema_drift.py` 的 `_ALLOWED_*` 与
# `test_scope_resolver_single_choke_point.py` 的 ALLOWED_* 契约：每一条都是
# **查证过的**既有不一致，带理由。
#
# ⚠️ **豁免按 `(名字, 轴)` 生效，不是按名字。** 早先按名字放行，于是一个索引
# 只要因为任何一条轴进了名单，它在**全部五条轴**上就都不说话了 —— 比如
# `idx_inbox_notifications_user_unread` 本来只该在「部分索引」这条轴上被放行，
# 按名字豁免却让它此后丢一列、掉唯一性都悄无声息。那时「新漂移立刻转红」这句
# 承诺对这九个名字已经不成立了。现在每条只列出它**当下确实在漂移**的那几条轴，
# 同一个名字换一条轴出问题照样转红。
#
# 棘轮也按 `(名字, 轴)` 走：某条轴不再漂移，
# `test_allowlist_entries_are_still_drifting` 会**失败并要求把那条轴删掉**
# （不是把整个名字删掉）—— 豁免不许放馊，也不许比它该有的范围更宽。
#
# ⚠️ 加一条进来不是「修好了」。加之前先回答：是库该补这个索引（写迁移），
# 还是声明写错了（改模型）？只有在两者都需要单独一票时，才把它记在这里。
#
# 下面九条全部是 C1 这条守卫**第一次跑起来就抓到的**，在本票之前就已存在：
# 前六条来自首轮（缺失 + 列），后三条来自 fix round 1 新加的部分索引轴。
# 每条的 `axes` 是从真库的漂移输出里**逐条派生**的，不是照着印象填的。
ALLOWED_INDEX_DRIFT: Dict[str, Dict[str, Any]] = {
    # ── 声明了一个库里根本不存在的索引 ──────────────────────────────
    "idx_hotspot_user_state_user": {
        "axes": frozenset({AXIS_MISSING}),
        "why": (
            "models/topics.py:261 声明 hotspot_user_state(user_id) 上有一个普通索引，"
            "库里没有。库里只有两个**部分**索引 idx_hotspot_user_state_hidden / "
            "_saved（同样是 user_id，但各带 WHERE is_hidden / WHERE is_saved）。"
            "补一个全表索引是迁移决定，不是改模型能解决的。"
        ),
    },
    "idx_topic_groups_user": {
        "axes": frozenset({AXIS_MISSING}),
        "why": (
            "models/topics.py:53 声明 topic_groups(user_id) 上有索引；库里 "
            "topic_groups 只有 pkey，没有任何二级索引。要么补迁移、要么这条声明"
            "从一开始就是空头支票 —— 需要 topics 模块的 owner 裁定。"
        ),
    },
    # ── 索引真的存在，但名字对不上 ──────────────────────────────────
    "idx_hotspots_dedup": {
        "axes": frozenset({AXIS_MISSING}),
        "why": (
            "models/topics.py:104 叫它 idx_hotspots_dedup；库里同样是 "
            "hotspots(dedup_key) 上的 UNIQUE 索引，但名字是 uq_hotspots_dedup。"
            "名字是 ON CONFLICT ON CONSTRAINT 与 IntegrityError 串匹配的那个键，"
            "所以这不是纯改名 —— 改哪边都要先查有没有代码在按名字认它。"
        ),
    },
    "social_accounts_scope_type_scope_id_platform_platform_user_key": {
        "axes": frozenset({AXIS_MISSING}),
        "why": (
            "models/distribution.py:60 手抄的约束名比库里少一个下划线：库里是 "
            "social_accounts_scope_type_scope_id_platform_platform_user__key"
            "（Postgres 自动命名撞上 63 字符上限后的截断结果）。索引本身存在且列"
            "一致，纯粹是名字抄错 —— 但改它要先确认没有代码按名字匹配这个约束。"
        ),
    },
    # ── 索引存在、名字对，但列集合少了一列 ──────────────────────────
    "idx_project_stage_history_project": {
        "axes": frozenset({AXIS_COLUMNS}),
        "why": (
            "models/project_library.py:109 只声明了 (project_id)；库里是 "
            "(project_id, entered_at DESC)。少声明的那一列正是让「按项目取最近一条"
            "阶段记录」走索引的那一列 —— 读模型的人会以为要自己再排序。"
        ),
    },
    "idx_publish_tasks_user": {
        "axes": frozenset({AXIS_COLUMNS}),
        "why": (
            "models/distribution.py:176 只声明了 (user_id)；库里是 "
            "(user_id, created_at DESC)。同上：漏掉的是排序列。"
        ),
    },
    # ── ORM 声明成全表，库里其实是部分索引（fix round 1 新轴抓到）──────
    # 三条同一个形状：读模型的人会以为这个索引服务全表，实际它只服务被
    # WHERE 过滤剩下的那部分行 —— 于是「这条查询走不走索引」的判断是错的，
    # 而且没有任何东西会说出来。补 `postgresql_where=` 是改模型能解决的，
    # 但要逐条确认谓词与迁移里那条一字不差，所以留票不夹带。
    "idx_agent_memory_agent": {
        "axes": frozenset({AXIS_PARTIAL}),
        "why": (
            "models/ai.py:523 声明 agent_memory(agent_id) 全表索引；库里是 "
            "WHERE (agent_id IS NOT NULL) 的部分索引。"
        ),
    },
    "idx_inbox_notifications_user_unread": {
        "axes": frozenset({AXIS_PARTIAL}),
        "why": (
            "models/reviews.py:266 声明 inbox_notifications(user_id) 全表索引；"
            "库里是 WHERE (read_at IS NULL) 的部分索引 —— 名字里的 `unread` 正是"
            "那个谓词，而声明把它丢了。"
        ),
    },
    "idx_inspiration_notes_user_pinned": {
        "axes": frozenset({AXIS_PARTIAL}),
        "why": (
            "models/inspiration.py:49 声明 inspiration_notes(user_id, pinned) "
            "全表索引；库里是 WHERE (deleted_at IS NULL) 的部分索引。"
        ),
    },
}


# ── ORM 侧 ───────────────────────────────────────────────────────────────


class DeclaredIndex(NamedTuple):
    """ORM 元数据里一条具名的索引声明。"""

    name: str
    table: str
    #: 能解析成真实列名的那些列，**按声明顺序**。表达式列（`lower(x)`、
    #: `x DESC NULLS LAST` 里的函数调用）不在其中 —— 见 `expressions`。
    #:
    #: 顺序是有意义的：`(a, b)` 上的复合索引服务「按 a 过滤」与「按 a 过滤再
    #: 按 b 排序」，`(b, a)` 两个都不服务。库侧的 `array_agg(… ORDER BY k.ord)`
    #: 早就是有序的，所以这边用元组而不是集合才对得上。
    columns: Tuple[str, ...]
    #: 解析不出列名的表达式个数。>0 时本条只比名字与表，不比列：
    #: 拿 SQLAlchemy 的表达式对象跟 pg_get_indexdef 的文本比对，比的是两套
    #: 序列化风格而不是索引，会产出大量假红。
    expressions: int
    unique: bool
    #: 这条声明带不带 `postgresql_where`（部分索引）。**只比有无，不比谓词
    #: 文本** —— 理由同上：SQLAlchemy 的 `text("deleted_at IS NULL")` 与
    #: `pg_get_indexdef` 吐的 `WHERE (deleted_at IS NULL)` 是同一件事的两种
    #: 序列化，逐字比会把括号和空格判成漂移。
    partial: bool
    #: "index"（`Index(...)` / `index=True`）或 "unique"（`UniqueConstraint`
    #: / 列上的 `unique=True`）。两者在 Postgres 里都落成 pg_index 一行。
    kind: str


def _declared_indexes() -> List[DeclaredIndex]:
    """`Base.metadata` 里每一条**具名**的索引声明。

    在函数里 import：INTEGRATION_DATABASE_URL 未设时模块只被收集不被执行，
    不该有 import 期副作用（同 `test_schema_drift.py::_orm_tables`）。

    匿名的 `UniqueConstraint`（`name is None`）被跳过：名字是我们跟库对账的
    唯一键，不知道名字就无从比起。它们的个数由
    `test_the_scan_itself_ran` 打印出来，好让「跳过的越来越多」这件事至少
    是可见的。
    """
    from sqlalchemy import UniqueConstraint

    import app.models  # noqa: F401 — 把所有模型注册到 Base.metadata 上
    from app.db.orm_base import Base

    out: List[DeclaredIndex] = []
    for full_name, table in Base.metadata.tables.items():
        bare = full_name.split(".", 1)[-1]
        for ix in table.indexes:
            if not ix.name:
                continue
            columns: List[str] = []
            expressions = 0
            for expr in ix.expressions:
                col_name = getattr(expr, "name", None)
                # 真正的 Column 对象既有 name 也挂在某张表上；`text("a DESC")`
                # 之类的表达式两者至少缺一个。
                if col_name is not None and getattr(expr, "table", None) is not None:
                    columns.append(col_name)
                else:
                    expressions += 1
            out.append(
                DeclaredIndex(
                    name=str(ix.name),
                    table=bare,
                    columns=tuple(columns),
                    expressions=expressions,
                    unique=bool(ix.unique),
                    partial=ix.dialect_kwargs.get("postgresql_where") is not None,
                    kind="index",
                )
            )
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint) or not constraint.name:
                continue
            out.append(
                DeclaredIndex(
                    name=str(constraint.name),
                    table=bare,
                    columns=tuple(col.name for col in constraint.columns),
                    expressions=0,
                    unique=True,
                    # Postgres 的 UNIQUE **约束**不能带谓词（只有部分唯一
                    # **索引**能），所以约束这一侧恒为 False。
                    partial=False,
                    kind="unique",
                )
            )
    return out


def _anonymous_unique_constraints() -> int:
    from sqlalchemy import UniqueConstraint

    import app.models  # noqa: F401
    from app.db.orm_base import Base

    return sum(
        1
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if isinstance(c, UniqueConstraint) and not c.name
    )


# ── 库侧 ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping ORM-index integration tests"
        )
    return _TEST_DSN


@pytest.fixture(scope="module")
async def live_indexes(integration_db_url: str) -> Dict[str, Dict[str, Any]]:
    """public schema 下每一个索引：{ index_name: {table, columns, unique, defn} }。

    `indkey` 里 attnum=0 表示这一位是表达式而不是列，JOIN pg_attribute 会把它
    丢掉 —— 所以同时取 `array_length(indkey, 1)`，两个数不等就说明这个索引里
    含表达式。只读。
    """
    conn = await asyncpg.connect(integration_db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT
                i.relname            AS index_name,
                t.relname            AS table_name,
                ix.indisunique       AS is_unique,
                array_length(ix.indkey, 1) AS key_count,
                pg_get_indexdef(ix.indexrelid) AS defn,
                (
                    SELECT array_agg(a.attname ORDER BY k.ord)
                    FROM unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord)
                    JOIN pg_attribute a
                      ON a.attrelid = t.oid AND a.attnum = k.attnum
                ) AS columns
            FROM pg_index ix
            JOIN pg_class     i  ON i.oid = ix.indexrelid
            JOIN pg_class     t  ON t.oid = ix.indrelid
            JOIN pg_namespace n  ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
            """
        )
    finally:
        await conn.close()

    return {
        r["index_name"]: {
            # ⚠️ 元组，不是集合。SQL 那边 `array_agg(… ORDER BY k.ord)` 辛苦
            # 排好的顺序，在这里换成 frozenset 就白排了 —— `(a, b)` 与
            # `(b, a)` 会被读成同一个索引（L1；这一条是本文件自己的单测
            # `test_column_ORDER_is_compared_not_just_the_set` 在真库上先抓到的）。
            "columns": tuple(r["columns"] or ()),
            "table": r["table_name"],
            "key_count": r["key_count"] or 0,
            "unique": r["is_unique"],
            "defn": r["defn"],
        }
        for r in rows
    }


# ── 门禁 ─────────────────────────────────────────────────────────────────


def _missing(live: Dict[str, Dict[str, Any]]) -> List[Drift]:
    """声明了、库里却没有同名索引的。"""
    return [
        Drift(d.name, AXIS_MISSING, f"{d.table}({', '.join(d.columns) or '<expr>'})")
        for d in _declared_indexes()
        if d.name not in live
    ]


def _mismatched(
    live: Dict[str, Dict[str, Any]],
    declared: List[DeclaredIndex] | None = None,
) -> List[Drift]:
    """同名索引存在，但定义对不上的。

    四条独立的轴，**每条各自上报**（一次不一致可以同时是好几件事 ——
    CLAUDE.md「正交的结果各自独立上报」）：表、有序列、唯一性、部分索引有无。

    ``declared`` 只为单测注入合成声明用；生产路径留空走真元数据。
    """
    out: List[Drift] = []
    for d in declared if declared is not None else _declared_indexes():
        row = live.get(d.name)
        if row is None:
            continue

        # ── 表 ───────────────────────────────────────────────────────
        # 表不同**只让「列」这一轴失去意义**（列名属于某张表）。唯一性与
        # 部分索引是表无关的属性，照比不误 —— 在这里 `continue` 会把两条
        # 正交的结果吞进「表不一致」这一个分支里（CLAUDE.md：绝不能把一个
        # 标志的上报嵌进另一个标志的分支）。
        same_table = row["table"] == d.table
        if not same_table:
            out.append(
                Drift(
                    d.name,
                    AXIS_TABLE,
                    f"{AXIS_TABLE} orm={d.table} live={row['table']}",
                )
            )

        # ── 唯一性（M1）──────────────────────────────────────────────
        # `indisunique` 一直取着却从没比过。一个声明成 unique 而库里不是的
        # 索引，是「这一列不会重复」这句承诺的凭空消失 —— 依赖它的
        # `ON CONFLICT` 与并发去重全都建立在一个不存在的保证上。
        if row["unique"] != d.unique:
            out.append(
                Drift(
                    d.name,
                    AXIS_UNIQUE,
                    f"{AXIS_UNIQUE} orm={d.unique} live={row['unique']} :: {row['defn']}",
                )
            )

        # ── 部分索引的有无（M2）─────────────────────────────────────
        # 只比**有无**，不比谓词文本。ORM 声明成全表而库里带 WHERE，意味着
        # 那个索引只服务被过滤剩下的那部分行，而读模型的人会以为它服务全表。
        live_partial = " WHERE " in row["defn"]
        if live_partial != d.partial:
            out.append(
                Drift(
                    d.name,
                    AXIS_PARTIAL,
                    f"{AXIS_PARTIAL} orm={d.partial} live={live_partial} "
                    f":: {row['defn']}",
                )
            )

        # ── 有序列（L1）─────────────────────────────────────────────
        if not same_table:
            # 列名属于某张表，两张不同的表之间比列没有意义。
            continue
        if d.expressions:
            # 含表达式的声明的**列**这一轴比不了 —— 见 DeclaredIndex.expressions。
            continue
        if row["key_count"] != len(row["columns"]):
            # 库里这个索引含表达式，ORM 侧却全是普通列：列没法直接比，
            # 但「一边有表达式一边没有」本身就是要报的不一致。
            out.append(
                Drift(
                    d.name,
                    AXIS_COLUMNS,
                    f"{AXIS_COLUMNS} 库侧含表达式，ORM 侧没有 :: {row['defn']}",
                )
            )
            continue
        if tuple(row["columns"]) != d.columns:
            out.append(
                Drift(
                    d.name,
                    AXIS_COLUMNS,
                    f"{AXIS_COLUMNS} orm={list(d.columns)} "
                    f"live={list(row['columns'])} :: {row['defn']}",
                )
            )
    return out


def _unexcused(drifts: List[Drift]) -> List[Tuple[str, str]]:
    """去掉**按轴**豁免过的，剩下的才是要报的。返回 (name, 人话)。"""
    return [
        (dr.name, dr.why)
        for dr in drifts
        if dr.axis not in ALLOWED_INDEX_DRIFT.get(dr.name, {}).get("axes", frozenset())
    ]


async def test_every_declared_index_exists_in_the_database(live_indexes) -> None:
    """ORM → 库，零容忍：声明了就必须真有。

    这条抓的是最贵的一类错 —— 一个从来没被建出来的索引。它在代码里看起来跟
    真索引一模一样，而每一条依赖它的查询在生产上都是全表扫。
    """
    offenders = _unexcused(_missing(live_indexes))
    assert offenders == [], (
        "这些索引 ORM 声明了但库里没有（要么补迁移，要么这条声明是错的；"
        f"两者都要单独一票时才进 ALLOWED_INDEX_DRIFT）：{offenders}"
    )


async def test_every_declared_index_matches_its_columns(live_indexes) -> None:
    """名字对上了还不够：列集合也要一致。

    少声明一列不会让任何东西报错，只会让读模型的人以为那条查询还要自己排序
    ——或者反过来，以为一个复合索引能服务它其实服务不了的查询。
    """
    offenders = _unexcused(_mismatched(live_indexes))
    assert offenders == [], f"这些索引的声明与库里的定义对不上：{offenders}"


async def test_allowlist_entries_are_still_drifting(live_indexes) -> None:
    """棘轮：豁免不许放馊，也不许比它该有的范围更宽。

    按 `(名字, 轴)` 判定。一条轴不再漂移，那条轴就必须从 `axes` 里删掉 ——
    留着它等于给这个名字在那条轴上开了一张永久通行证，而下一次真在那条轴上
    出问题时没有任何东西会说话。

    整个名字都不漂移了，`axes` 会被清空 —— 那就是「把这条豁免整条删掉」。
    """
    drifting = {(dr.name, dr.axis) for dr in _missing(live_indexes)} | {
        (dr.name, dr.axis) for dr in _mismatched(live_indexes)
    }
    stale = sorted(
        f"{name} / {axis}"
        for name, entry in ALLOWED_INDEX_DRIFT.items()
        for axis in entry["axes"]
        if (name, axis) not in drifting
    )
    assert stale == [], (
        "这些 (索引, 轴) 已经不漂移了，请把对应的轴从 ALLOWED_INDEX_DRIFT "
        f"的 axes 里删掉（轴清空即整条删掉）：{stale}"
    )


async def test_database_only_indexes_are_counted_not_failed(live_indexes) -> None:
    """库 → ORM：只报数，不判红。

    迁移里手写的索引（每张表的 pkey、各种部分索引）本就没有 ORM 镜像。把它们
    判成漂移，这条守卫会在落地当天被加一条包罗万象的豁免，然后就再也拦不住
    任何东西了。所以这一侧只把清单打出来 —— 它是给人读的，不是门禁。
    """
    declared = {d.name for d in _declared_indexes()}
    live_only = sorted(set(live_indexes) - declared)
    print(f"\n[inventory] 库里有而 ORM 未声明的索引：{len(live_only)} 个")
    for name in live_only:
        print(f"  - {name} on {live_indexes[name]['table']}")
    # 唯一的断言：扫描本身跑起来了。空清单在这里既可能是「全都声明了」，也
    # 可能是「查询坏了」，而后者绝不能读成前者。
    assert live_indexes, "pg_index 一行都没查到 —— 是检查坏了，不是没有索引"


async def test_the_scan_itself_ran(live_indexes) -> None:
    """守卫自己坏掉时必须说话，而不是报平安（CLAUDE.md 退出码三态那一节）。

    `Base.metadata` 没被填满（漏 import、模型模块改名）时，上面两条门禁会
    「零漂移」通过 —— 那是这条守卫最糟糕的失败模式。下限刻意松（真实值
    2026-09-15 是 352 条声明 / 599 个库内索引），它要拦的是「掉到个位数」
    而不是「少了一条」。
    """
    declared = _declared_indexes()
    print(
        f"\n[inventory] ORM 具名索引声明 {len(declared)} 条"
        f"（其中 UniqueConstraint {sum(1 for d in declared if d.kind == 'unique')} 条）；"
        f"匿名 UniqueConstraint {_anonymous_unique_constraints()} 条未参与对账；"
        f"库内 public 索引 {len(live_indexes)} 个"
    )
    assert len(declared) > 100, (
        f"只扫到 {len(declared)} 条索引声明 —— Base.metadata 没被填满，"
        "这一轮的「零漂移」是假的"
    )
    assert len(live_indexes) > 100, (
        f"只查到 {len(live_indexes)} 个库内索引 —— schema 没建起来，"
        "这一轮的对账不作数"
    )
