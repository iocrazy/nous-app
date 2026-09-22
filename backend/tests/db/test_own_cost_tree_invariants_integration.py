"""一棵真的 4 层树上的钱 —— spec §3 不变量 1–4，加三条同族的。

八条用例，各自钉一件事：

===================================================  ==========================
用例                                                 钉什么
===================================================  ==========================
``..._sums_agree``                                   §3-1 不重不漏（树/议题/agent）
``..._equals_bucket_tree``                           §3-2 与积分账同口径
``..._delegate_child_counts_toward_budget``          §3-3 预算看得见委派
``..._crash_writer_leaves_the_mirrored_own_cost``    §3-4 崩溃行不归零
``..._prior_plus_live_without_double_count``         门禁那道加法（T3 评审 Critical）
``..._conversation_only_run_counts_toward_the_...``  议题那组 OR 键的第二条臂
``..._whole_tree_on_the_roots_group``                效率页：钱按 root 归属（裁定 7）
``..._dirty_column_is_not_the_answer``               夹具自证：读错列会得出别的数
===================================================  ==========================

§3-5 / §3-6 是源码扫描守卫，不在这里（见
``tests/services/billing/test_cost_cents_never_aggregated.py``）。

WHY THIS FILE EXISTS
────────────────────
3d 第 0 票把六个读面从 ``agent_runs.cost_cents``（自身 + **已报到的**后代）换成
``own_cost_cents``（只记自身）。同名单测里的 session 全是桩的：它们证明「我们发出
去的语句长什么样」，证明不了**服务器把哪些行交出来、加出什么数**。而这一票修的缺
陷恰恰全在那一层：

* **不重不漏只有在一棵真树上才有意义。** 「按树求和」「按议题求和」「按 agent 求
  和」三条语句各自的 GROUP BY / WHERE 不同，却必须给同一个数。桩 session 按调用顺
  序发牌，三者返回什么都由夹具自己说了算 —— 三条都写错成同一个错数，单测照样绿。
* **``COALESCE(root_run_id, id)`` 这个树键是 Postgres 在算。** root 行那一列是 NULL
  （``_attach_to_parent_run`` 是唯一写方），所以同一个表达式要同时覆盖根与后代；写
  成 ``root_run_id`` 就把 root 自己那份丢了，写成 ``id`` 就把树拆成五棵。两种写法在
  桩里都「成功」。
* **委派子 run 进不进议题预算，是一条 WHERE 的事。** 旧语句带 ``parent_run_id IS
  NULL``，于是 Delegate 出去的花费整个绕过议题预算门禁 —— 这就是本票的起点。证明它
  被捞回来了，只能靠真的删掉那两行再看总额掉了多少。
* **议题那组键是 ``issue_id OR conversation_id``，而 ``OR`` 的两侧会重叠。** root 行
  两个键都有，所以「第二条臂在不在」与「它会不会把同一行数两遍」只能靠一个真的既有
  重叠行、又有只走会话那条路的行的库来分开 —— 桩 session 对这两件事一视同仁。
* **``own_cost_cents`` 是 numeric。** 12.5 / 3.25 这类值经 Decimal 往返后还等不等于
  手算的浮点数，是库 + 驱动 + 仓库那层 ``float()`` 合起来的答案。

**每一条断言都对着手算常数**（下面的 ``OWNS`` / ``DIRTY``），不是对着另一条查询 ——
两条都读错同一列时互相印证只会一起点头。

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_own_cost_tree_invariants_integration.py -v

``INTEGRATION_DATABASE_URL`` 没设就整体 skip —— 这个文件里没有一条在无库时仍然
「通过」的用例。schema-drift 那条 step 走 ``pytest-no-full-skip.sh``，所以在 CI 上
全 skip 是红的，不是绿的。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

asyncpg = pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — the own_cost_cents tree invariants "
        "need a real database."
    ),
)

# ---------------------------------------------------------------------------
# 这棵树
# ---------------------------------------------------------------------------
# A 是 root；B 是 A 的进程内子 run；C 是 A 的后台子 run；D 是 C 的 Delegate 子；
# E 是 D 的 Delegate 子。五行同一个 issue，``root_run_id`` 一律指向 A —— 这正是
# ``_attach_to_parent_run`` 写出来的形状（它把 root 一路传下去，不是只记父）。
#
# agent 故意跨两个：A、B 属 agent X，C、D、E 属 agent Y。「按 agent 分组」与「按树
# 分组」于是落在不同的行集合上，两者求和仍要相等。
#
# ``team_id`` 故意只戳到 C 为止（A / B / C 有，D / E 是 NULL）。子 run 的 scope 列
# 是 best-effort —— ``team_of_run`` 查不到就降级成 None，委派链越深越容易断 —— 所以
# 「一棵树里有的行带 scope、有的不带」是真实形状，而不是夹具偷懒。效率页那条子查询
# 按「属于某棵在 scope 内的树」收行、**不**按每行自己的 scope 列过滤（裁定 7），这
# 个不对称是唯一能把两种写法分开的形状：五行 scope 一致时，错的那种写法照样给出正
# 确答案（实测过）。
A, B, C, D, E = 91001, 91002, 91003, 91004, 91005

#: 这棵树的五行。
_TREE = (A, B, C, D, E)

#: 第六行，**不在这棵树里**：没有父、没有 ``root_run_id``，也**没有 ``issue_id``**。
#: 它挂到议题上的唯一凭据是 ``conversation_id`` —— 这正是 ``issue_scope_keys`` 第二
#: 条臂负责的那种 run（走会话直接找 agent 聊出来的，从没戳过 issue 列）。在此之前
#: 这个夹具一行都没有，那条臂于是从未在真库上跑过。
#:
#: agent 取 X（与 root 同一个），所以「按 agent 开窗求和」会看见它 —— 那是唯一一个
#: 不按议题键收行的读方，它的手算常数因此要含这一笔。
CONV_ONLY = 91006

#: 带 ``conversation_id`` 的两行。root A 也戳着同一个会话 —— 真实形状就是这样
#: （议题的 session 会话上跑出来的 root 两个键都有），而且它让「两条臂 OR 起来会不会
#: 把 A 数两遍」变成一个真的、能被测到的风险。
_CONVERSATION_ROWS = (A, CONV_ONLY)

#: 带 ``team_id`` 的那四行 —— 效率页主查询能看见的就是它们。
_SCOPED = (A, B, C, CONV_ONLY)

#: 每行 ``metadata_json.cost`` 的自身两道（own + media），单位分。写方
#: （``RunRecorder`` / ``RunEventWriter``）把 ``spend_of_run(cost).total`` 落进
#: ``own_cost_cents``，所以这两个数之和就是那一列应有的值。
_LEGS = {
    A: (10.5, 2.0),
    B: (3.0, 0.25),
    C: (5.0, 1.0),
    D: (7.5, 0.5),
    E: (6.0, 0.0),
    CONV_ONLY: (2.0, 0.5),
}

#: 手算的每行自身花费 —— 全文件唯一的真相来源。
#: **只含这棵树的五行** —— ``CONV_ONLY`` 不在里面，它自成一棵树（见 ``OWN_CONV``）。
OWNS = {rid: round(sum(_LEGS[rid]), 4) for rid in _TREE}

#: 议题/树的总额：12.5 + 3.25 + 6.0 + 8.0 + 6.0
TOTAL = 35.75
#: 按 agent 切：X 拿 A + B，Y 拿 C + D + E。
OWN_X = 15.75
OWN_Y = 20.0

#: 只挂会话那行的自身花费：2.0 + 0.5。
OWN_CONV = 2.5
#: 两条臂一起看时的议题总额：35.75 + 2.5。``spent_cents_for_issue`` 传了
#: ``conversation_id`` 才是这个数；不传就是 ``TOTAL``。
TOTAL_WITH_CONV = 38.25

#: 旧列 ``cost_cents`` 上**故意**填的脏值 = 自身 + **已报到的**后代。
#:
#: A 的 ``by_child`` 只有 B（后台派出去的 C 从没往回报 —— workforce 链上 root 的
#: ``by_child`` 恒为空，这就是本票要修的形状），而且记的是 B 汇报当时的数
#: （3.0，不含 B 后来才结算的 media 道）—— 「已报到的」与「最终花了的」本来就会
#: 漂，这也是为什么求和不能读这一列。
#:
#: 这批数与上面那批**两两互不相等**（下面 ``test_the_dirty_column_is_not_the_answer``
#: 把这件事钉成断言），所以任何一个读方误读回 ``cost_cents``，断言必然转红而不是
#: 碰巧通过。
DIRTY = {
    A: 15.5,  # 12.5 + by_child{B: 3.0}
    B: 3.25,  # 叶子：自身即全部
    C: 20.0,  # 6.0 + by_child{D: 14.0}
    D: 14.0,  # 8.0 + by_child{E: 6.0}
    E: 6.0,  # 叶子
}

#: ``CONV_ONLY`` 的旧列：2.5 + by_child{9199006: 6.5}。同样**刻意**不等于自身花费
#: （2.5），所以按 agent 求和那条用例里，读错列会算出别的数而不是碰巧对上。
DIRTY_CONV = 9.0

#: 插入时用的合并视图 —— 常数各自定义在自己该在的地方（树的在 ``OWNS`` / ``DIRTY``，
#: 会话那行的在 ``OWN_CONV`` / ``DIRTY_CONV``），只有写库这一步需要把六行看成一批。
_ALL_OWN = {**OWNS, CONV_ONLY: OWN_CONV}
_ALL_DIRTY = {**DIRTY, CONV_ONLY: DIRTY_CONV}

#: ``metadata_json.cost`` 里除两道之外的噪声，按行。
#:
#: ``by_child`` 必须在场：它是后代的钱，而新列的全部意义就是**不含**它；一棵没有
#: ``by_child`` 的树里，「加总了 by_child」的实现与正确实现给出同一个数。
#: E 上那道 ``own_byok_cents`` 同理，方向相反 —— ``spend_of_run`` 的 ``total``
#: （以及 ``own_cost_cents``）**不**减 BYOK，只有 ``platform`` 减。一行全 BYOK 的
#: run 仍然要按它真烧的钱进树总额。
_NOISE = {
    A: {"by_child": {"9199001": 3.0}},
    B: {},
    C: {"by_child": {"9199002": 14.0}},
    D: {"by_child": {"9199003": 6.0}},
    E: {"own_byok_cents": 6.0},
    CONV_ONLY: {"by_child": {"9199006": 6.5}},
}

#: 窗口锚点。挑一个远离「现在」的固定时刻，让按时间开窗的两个读方
#: （``monthly_usage_by_agent`` 按 ``started_at``、``efficiency_groups`` 按
#: ``created_at``）只可能看到这棵树自己的行 —— 共享的 drift 库上别的用例也在写
#: ``agent_runs``。每次新建的 agent / team 已经把别人隔开了，时间是第二道。
_ANCHOR = datetime(2019, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
_WINDOW = (_ANCHOR - timedelta(days=1), _ANCHOR + timedelta(days=1))


def _cost_view(rid: int) -> dict:
    own, media = _LEGS[rid]
    return {"own_cents": own, "media_cents": media, **_NOISE[rid]}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def orm_dsn():
    """把 ORM 引擎重指到测试 DSN，用完 dispose，别让其他测试继承一个野引擎。

    仓库那几个方法走的是 ``read_scope()`` → 自己的 SQLAlchemy 引擎，**不是**下面
    那条 asyncpg 连接。所以夹具的写入必须是已提交的（见 ``tree``），否则两边看的
    是两个事务，读方一行都拿不到。
    """
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def tree(pg):
    """六行 ``agent_runs`` + 它们要的外键行，**提交**后交出去，用完逐一删干净。

    五行是那棵树（A..E，共用一个 ``issue_id``），第六行 ``CONV_ONLY`` 自成一棵树、
    只挂 ``conversation_id`` —— 议题那组 OR 键的第二条臂。

    为什么不是「开个事务、跑完回滚」（``test_migration_479`` 那个形状）：那边被测
    的是一条 SQL 文件，跑在同一条连接上；这边被测的是仓库方法，它们各自开自己的
    连接，看不见一个未提交的事务。所以只能真写真删。

    id 取 9100x 段（迁移那个文件占 9000x），与任何真实 Snowflake id 都不可能撞。
    删除顺序从叶子往根：``parent_run_id`` 是 ON DELETE CASCADE，但 ``root_run_id``
    那条 FK 没有级联，先删 A 会撞外键。
    """
    suffix = uuid.uuid4().hex[:8]
    agent_x = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"own-cost-tree-x-{suffix}",
    )
    agent_y = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"own-cost-tree-y-{suffix}",
    )
    user_id = uuid.uuid4()
    await pg.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
        user_id,
        f"own-cost-tree-{suffix}@test.dev",
    )
    issue_id = await pg.fetchval(
        "INSERT INTO public.issues "
        "  (issue_number, identifier, title, created_by_agent_id) "
        "VALUES ($1, $2, 'Own cost tree invariants', $3) RETURNING id",
        int(uuid.uuid4().int % 1_000_000),
        f"OWNTREE-{suffix}",
        agent_x,
    )
    team_id = await pg.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"Own Cost Tree {suffix}",
        user_id,
        f"OWNTREE{suffix.upper()}",
    )
    # ``conversations`` 的三个非空无默认列是 type / scope_id / created_by，
    # ``scope_id`` FK 到 ``teams``（照 test_migration_479 的 ``_mk_conversation``，
    # 只是复用本夹具已有的 team 与 user，少造两行）。
    conversation_id = await pg.fetchval(
        "INSERT INTO public.conversations (type, scope_id, created_by) "
        "VALUES ('direct_agent', $1, $2) RETURNING id",
        team_id,
        user_id,
    )

    # (id, agent, parent, root)
    shape = [
        (A, agent_x, None, None),
        (B, agent_x, A, A),
        (C, agent_y, A, A),
        (D, agent_y, C, A),
        (E, agent_y, D, A),
        (CONV_ONLY, agent_x, None, None),
    ]
    for rid, agent, parent, root in shape:
        await pg.execute(
            "INSERT INTO public.agent_runs "
            "  (id, agent_id, user_id, team_id, status, trigger, issue_id, "
            "   conversation_id, parent_run_id, root_run_id, own_cost_cents, "
            "   cost_cents, started_at, ended_at, created_at, metadata_json) "
            "VALUES ($1, $2, $3, $4, 'completed', 'manual', $5, $6, $7, $8, $9, "
            "        $10, $11, $12, $11, $13::jsonb)",
            rid,
            agent,
            user_id,
            team_id if rid in _SCOPED else None,
            None if rid == CONV_ONLY else issue_id,
            conversation_id if rid in _CONVERSATION_ROWS else None,
            parent,
            root,
            _ALL_OWN[rid],
            _ALL_DIRTY[rid],
            _ANCHOR,
            _ANCHOR + timedelta(seconds=30),
            json.dumps({"cost": _cost_view(rid)}),
        )

    try:
        yield {
            "issue_id": issue_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "team_id": team_id,
            "agent_x": agent_x,
            "agent_y": agent_y,
        }
    finally:
        # 崩溃那条用例走的是真的 ``mark_heartbeat_lost_ids``，它在 UPDATE 之后还会
        # best-effort 写一行小时表遥测 + 一条检索投影。两者都按本夹具自己造的
        # agent / run 键控，跟着一起删 —— 这个库是共享的，用例跑完必须一行不留。
        await pg.execute(
            "DELETE FROM public.ai_usage_hourly WHERE agent_id = ANY($1::uuid[])",
            [agent_x, agent_y],
        )
        await pg.execute(
            "DELETE FROM public.search_docs WHERE run_id = ANY($1::bigint[])",
            [*_TREE, CONV_ONLY],
        )
        for rid in (CONV_ONLY, E, D, C, B, A):
            await pg.execute("DELETE FROM public.agent_runs WHERE id = $1", rid)
        await pg.execute("DELETE FROM public.issues WHERE id = $1", issue_id)
        # 会话在 team 之前删：``conversations.scope_id`` 是 ON DELETE CASCADE，
        # 删 team 也会带走它，但靠级联清理等于让「谁负责删」取决于 schema 细节。
        await pg.execute(
            "DELETE FROM public.conversations WHERE id = $1", conversation_id
        )
        await pg.execute("DELETE FROM public.teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)
        await pg.execute(
            "DELETE FROM public.ai_agents WHERE id = ANY($1::uuid[])",
            [agent_x, agent_y],
        )


def _repo():
    from app.repositories.agent_runs_repository import AgentRunsRepository

    return AgentRunsRepository()


# ---------------------------------------------------------------------------
# 不变量 1 —— 三种分组，一个数
# ---------------------------------------------------------------------------


@_skip
async def test_tree_issue_agent_sums_agree(orm_dsn, tree):
    """spec §3 不变量 1：按树 / 按议题 / 按 agent 求和，三条语句一个数。

    三者的 WHERE 与 GROUP BY 毫无共同之处 —— 树按 ``COALESCE(root_run_id, id)``、
    议题按 ``issue_id OR conversation_id``、agent 按 ``agent_id`` 且开时间窗。它们
    仍然相等，唯一的原因是每行只记自身、五行全部在场：**不重不漏**。

    按 agent 那一步还额外拆开看：跨 agent 的树上，父子各记各的（X 只拿 A+B，Y 只拿
    C+D+E）。这正是「按 agent 限额」想要的口径，也是旧列做不到的 —— 旧列里父行的
    钱已经折进了后代，而后代自己那行还在同一批结果里，同一笔钱算两遍。
    """
    repo = _repo()

    by_tree = await repo.tree_cost_cents([A])
    by_issue = await repo.spent_cents_for_issue(issue_id=tree["issue_id"])
    rollup = await repo.own_cost_cents_for_issue_runs(tree["issue_id"])

    assert by_tree[str(A)] == TOTAL
    assert by_issue == TOTAL
    # 驾驶舱 Budget 格与门禁读的是同一条 helper，所以它也必须是同一个数。
    assert rollup == TOTAL

    rows = await repo.monthly_usage_by_agent(
        month_start=_WINDOW[0], month_end=_WINDOW[1]
    )
    mine = {str(tree["agent_x"]): 0.0, str(tree["agent_y"]): 0.0}
    for r in rows:
        key = str(r["agent_id"])
        if key in mine:
            # 这个投影把 numeric 渲染成 str（REST 口径），消费方自己 float()。
            mine[key] += float(r["cost_cents"] or 0)

    # ⚠️ 这个读方**只按时间开窗**，不按议题也不按树收行，所以它看见的是六行：这棵树
    # 加上那条只挂会话的 ``CONV_ONLY``（同属 agent X）。它是唯一一个口径比另外三条宽
    # 的读方 —— 「三种分组一个数」说的是同一批行，不是同一个窗口。
    assert mine[str(tree["agent_x"])] == round(OWN_X + OWN_CONV, 4) == 18.25
    assert mine[str(tree["agent_y"])] == OWN_Y
    assert round(sum(mine.values()), 4) == TOTAL_WITH_CONV
    # 把窗口多出来的那一行减掉，正好还原按树 / 按议题的那个数。
    assert round(sum(mine.values()) - OWN_CONV, 4) == TOTAL


# ---------------------------------------------------------------------------
# 不变量 2 —— 列与收口口径同源
# ---------------------------------------------------------------------------


@_skip
async def test_tree_total_equals_bucket_tree(orm_dsn, tree, pg):
    """spec §3 不变量 2：列求和 == ``tree_charge.bucket_tree`` 的 ``tree_total``。

    两条路径算的是同一笔钱，而来源完全不同：``tree_cost_cents`` 读的是**列**
    （写方在每次镜像时落下的），``bucket_tree`` 读的是**每行的 cost 视图**
    （收口那一刻现折的）。收口扣的钱与界面显示的钱必须是同一个数，否则用户看到
    的花费和账单对不上 —— 而这件事没有任何单测能说，因为两边在桩里都是夹具自己
    喂的。

    行是照 ``settle_tree_if_closed`` 的取法拿的：``id = root OR root_run_id =
    root``，然后 ``(metadata_json or {}).get("cost")``。形状必须一样 —— 喂
    ``metadata_json`` 整个进去，``spend_of_run`` 会在缺 ``own_cents`` 的 mapping 上
    安静地读出 0.0，一棵树于是白白结成免费。
    """
    from app.services.ai.billing.tree_charge import bucket_tree

    rows = await pg.fetch(
        "SELECT metadata_json FROM public.agent_runs "
        " WHERE id = $1 OR root_run_id = $1",
        A,
    )
    assert len(rows) == 5, "取树的谓词漏了行，后面的相等就没有意义"

    views = [(json.loads(r["metadata_json"]) or {}).get("cost") for r in rows]
    buckets = bucket_tree(views)

    assert buckets.tree_total == TOTAL
    assert (await _repo().tree_cost_cents([A]))[str(A)] == TOTAL
    # BYOK 那道只影响 platform：E 整行是用户自己的 key 付的，所以平台侧比真实花费
    # 少掉 own(E)。列里记的是真实花费，两者本就不该相等 —— 断言这一点，免得哪天
    # 有人把 ``own_cost_cents`` 改成写 ``platform``，而上面两条照样绿。
    assert buckets.tree_platform == round(TOTAL - OWNS[E], 4)


# ---------------------------------------------------------------------------
# 不变量 3 —— 委派子 run 进预算
# ---------------------------------------------------------------------------


@_skip
async def test_delegate_child_counts_toward_budget(orm_dsn, tree, pg):
    """spec §3 不变量 3，也是本票的起点：Delegate 出去的钱进议题预算门禁。

    D、E 是两层 Delegate 子 run。旧语句带 ``parent_run_id IS NULL``，它们对门禁
    完全不可见 —— 一个议题可以靠不停委派把预算烧穿而门禁一声不吭。

    证法是删掉那两行再问一次：差值必须**恰好**等于它们自身的两笔。只断言「现在含
    了」不够 —— 一个把整棵树的钱重复计进去的实现同样能让总额变大。
    """
    repo = _repo()
    before = await repo.spent_cents_for_issue(issue_id=tree["issue_id"])
    assert before == TOTAL

    # 从叶子删起：E 是 D 的孩子。
    await pg.execute("DELETE FROM public.agent_runs WHERE id = ANY($1::bigint[])", [E])
    await pg.execute("DELETE FROM public.agent_runs WHERE id = ANY($1::bigint[])", [D])

    after = await repo.spent_cents_for_issue(issue_id=tree["issue_id"])

    assert after == round(TOTAL - OWNS[D] - OWNS[E], 4)
    assert round(before - after, 4) == round(OWNS[D] + OWNS[E], 4) == 14.0


# ---------------------------------------------------------------------------
# 不变量 4 —— 门禁那道加法不双计
# ---------------------------------------------------------------------------


@_skip
async def test_budget_gate_composes_prior_plus_live_without_double_count(orm_dsn, tree):
    """spec §3 不变量 4 / Task 3 评审 Critical：``prior + live`` 恰好是全额。

    预算门禁不能只读库 —— 正在跑的这条 run 的花费每一轮都在长，而它落库是滞后的。
    所以它算的是两段：

        prior = spent_cents_for_issue(issue, exclude_run_id=<本 run>)   # 库
        live  = spend_of_run(recorder.views["cost"]).total              # 内存

    这里唯一的风险是**双计**：如果 ``prior`` 那条语句里还残留 root-only 之外的任何
    别的口径（比如顺手把 root 行的 ``cost_cents`` 也加进来），子 run 的钱就会既在
    ``prior`` 里、又在 root 折叠值里出现两次。所以 ``prior`` 必须**恰好**是 B..E
    的自身之和 —— 一分不多、一分不少 —— 而它加上 A 自己现折的那份正好还原全额。

    live 那一段照门禁的真实算法走同一个函数（``spend_of_run``），喂的是 A 那行的
    cost 视图：门禁读的就是这份视图的内存版。
    """
    from app.services.ai.billing.tree_charge import spend_of_run

    repo = _repo()
    prior = await repo.spent_cents_for_issue(
        issue_id=tree["issue_id"], exclude_run_id=A
    )
    children = round(OWNS[B] + OWNS[C] + OWNS[D] + OWNS[E], 4)

    # 孩子全在 prior 里（这是不变量 3 的另一面），而 A 自己不在。
    assert prior == children == 23.25

    live = spend_of_run(_cost_view(A)).total
    assert live == OWNS[A] == 12.5
    assert round(prior + live, 4) == TOTAL


# ---------------------------------------------------------------------------
# 议题的第二条臂：只挂会话的 run
# ---------------------------------------------------------------------------


@_skip
async def test_a_conversation_only_run_counts_toward_the_issue_when_the_key_is_passed(
    orm_dsn, tree
):
    """``issue_scope_keys`` 的 ``conversation_id`` 那条臂，在真库上跑一遍。

    一条 run 挂到议题上有两条路：直接戳 ``issue_id``，或者经它的 session
    ``conversation_id``。走会话直接找 agent 聊出来的 run 从没戳过 ``issue_id`` ——
    只认第一条臂，这笔钱在预算门禁与 Budget 格上就是不存在的。

    两个方向一起断言，缺一不可：

    * **第二臂在** —— 传了会话键，总额才含 ``CONV_ONLY`` 那 2.5。只断言这一条的话，
      一个「把两条臂写成 AND」或干脆把会话键忽略掉的实现会给出 ``TOTAL``，而
      ``TOTAL`` 看着也像个对的数。
    * **第二臂不多收** —— root A **两个键都有**（它就是这个议题的 session 上跑出来
      的），所以 ``OR`` 起来必须只数它一遍。把 OR 写成两条语句相加、或者 join 出笛卡
      尔积，A 那 12.5 会出现两次，总额变成 50.75。不传会话键时仍是 ``TOTAL``，正是
      「多出来的恰好只有那一行」的另一面。

    ``own_cost_cents_for_issue_runs``（驾驶舱 Budget 格）走同一组键 —— 模块头那条
    「三个读方一个数」的承诺在这条臂上同样要成立，而它此前也没有真库覆盖。
    """
    repo = _repo()

    with_key = await repo.spent_cents_for_issue(
        issue_id=tree["issue_id"], conversation_id=tree["conversation_id"]
    )
    without = await repo.spent_cents_for_issue(issue_id=tree["issue_id"])

    assert without == TOTAL
    assert with_key == TOTAL_WITH_CONV == round(TOTAL + OWN_CONV, 4)

    rollup = await repo.own_cost_cents_for_issue_runs(
        tree["issue_id"], tree["conversation_id"]
    )
    assert rollup == TOTAL_WITH_CONV


# ---------------------------------------------------------------------------
# 效率页：钱按 root 归属，活按 run 归属
# ---------------------------------------------------------------------------


@_skip
async def test_efficiency_groups_puts_the_whole_tree_on_the_roots_group(orm_dsn, tree):
    """跨 agent 的树上，整棵树的钱落在 **root 所在的那一组**（裁定 7 / 终审 I5）。

    两件事一起断言，它们方向相反：

    1. **钱按树滚。** C、D、E 是 agent Y 的 run，但它们的 root A 属 agent X，所以
       35.75 全额进 X 组，Y 组一分没有 —— 而 Y 组的 ``run_count`` 是 1。两种粒度
       混在一张分组表里是刻意的（路由层为此在 ``cost == 0 and delivered > 0`` 时回
       null）。
    2. **收行看树、不看行自己的 scope 列。** 本用例按 ``team_id`` 开窗，而 D、E 那
       两行的 ``team_id`` 是 NULL（见夹具顶部：子 run 的 scope 是 best-effort）。它
       们仍然要计进 X 组的 35.75 —— 那正是「属于某棵在 scope 内的树」这个收行条件
       的全部意义。把子查询改成也带行级 scope 过滤，X 组会静默变成 21.75：join 落
       得下来、树在 scope 里、没有任何报错，只是委派那两笔钱不见了。

    ⚠️ 第 2 点是这个夹具为什么要造 scope 不对称：实测过，五行 ``team_id`` 一致时，
    错的那种写法给出的答案与正确写法**逐字节相同**。

    3. **同一组里两棵树各算各的，然后相加。** ``CONV_ONLY`` 也属 agent X、也带
       ``team_id``、也是 root（没父、没 ``root_run_id``），所以它是 X 组里的第二棵
       树 —— 一棵只有一行的树。X 组于是拿 35.75 + 2.5 = 38.25，``run_count`` 3。

       这一条钉的是那个聚合**真的在求和**：``sum(tree_cost.cents) FILTER (root_only)``
       第一次有两个加数落在同一组里。在只有一棵树的夹具上，把 ``func.sum`` 换成
       ``func.max`` / 取第一行 / ``LIMIT 1`` 全都给出同一个 35.75 —— 实测过：
       ``sum`` → ``max`` 在旧的五行夹具上**照样绿**，加上这一行才转红。

       ⚠️ 它**不是**为了钉树键 ``COALESCE(root_run_id, id)`` —— 那个旧夹具早就拦得住
       （把它换成裸 ``root_run_id``，A 归 NULL 组、B..E 归 A 组，而 join 条件是
       ``tree_cost.root == AgentRuns.id``，NULL 组永远落不下来，X 直接掉到 23.25；
       实测确认）。
    """
    rows, _reasons = await _repo().efficiency_groups(
        frm=_WINDOW[0],
        to=_WINDOW[1],
        team_id=tree["team_id"],
        group_by="agent",
    )
    by_key = {r["key"]: r for r in rows}

    x = by_key[str(tree["agent_x"])]
    y = by_key[str(tree["agent_y"])]

    assert float(x["cost_cents"]) == TOTAL_WITH_CONV
    assert float(y["cost_cents"]) == 0.0
    # 活按行数，且只数 scope 内的行：X 拿 A + B + CONV_ONLY，Y 只拿 C（D、E 没
    # team_id）。
    assert (x["run_count"], y["run_count"]) == (3, 1)


# ---------------------------------------------------------------------------
# 崩溃行不归零
# ---------------------------------------------------------------------------


@_skip
async def test_a_crash_writer_leaves_the_mirrored_own_cost_alone(orm_dsn, tree, pg):
    """spec §3 不变量 4：镜像过的 run 被崩溃写方终结后，列保持最后镜像值。

    崩溃类终态（``mark_heartbeat_lost_ids`` / ``liveness_scanner._mark_dead`` /
    ``reconcile_stranded_runs``）**不经** ``RunRecorder._finish``，所以那条终态
    UPDATE 是这一行最后被写的一次。它只翻 status 那几列、不碰 ``own_cost_cents``，
    于是列停在最后一次事件镜像落下的值 —— 树收口按 ``metadata_json.cost`` 逐行折出
    来的也正是这个数，两者对得上。

    反面是静默的：如果那条 UPDATE 顺手把这一列写成 NULL 或 0（比如有人把它加进
    ``values()`` 的「重置」清单），一条崩溃的 run 在所有聚合里就表现成**没花钱**，
    而它烧掉的 token 是真的。没有任何报错，账少一笔。

    这条断言此前只存在于 ``tests/runner/test_own_cost_column_writers.py`` 的
    **散文**里（「崩溃写方只翻 status、不碰这一列」）—— 一句没有测试的承诺。这里让
    真的那条 UPDATE 跑一遍，由服务器回答。
    """
    repo = _repo()
    # C 已经镜像过（列上是 6.0）。把它放回 running 且心跳过期 —— 这就是清扫器眼里
    # 一条崩掉的 run。``heartbeat_at`` 用绝对时刻，不靠「现在」。
    stale_at = _ANCHOR
    await pg.execute(
        "UPDATE public.agent_runs "
        "   SET status = 'running', ended_at = NULL, heartbeat_at = $2 "
        " WHERE id = $1",
        C,
        stale_at,
    )

    swept = await repo.mark_heartbeat_lost_ids(
        stale_before=stale_at + timedelta(days=1)
    )
    assert C in swept, "清扫器没认领这一行，后面的断言就不是在测崩溃路径"

    row = await pg.fetchrow(
        "SELECT status, own_cost_cents, cost_cents FROM public.agent_runs "
        " WHERE id = $1",
        C,
    )
    assert row["status"] == "heartbeat_lost"  # 正向对照：那条 UPDATE 真的跑了
    assert float(row["own_cost_cents"]) == OWNS[C] == 6.0
    # 旧列同样没被动 —— 两列都是「不归零」，别只盯着新的那个。
    assert float(row["cost_cents"]) == DIRTY[C]

    # 而且它仍然全额计进议题与树：崩溃不等于免费。
    assert await repo.spent_cents_for_issue(issue_id=tree["issue_id"]) == TOTAL
    assert (await repo.tree_cost_cents([A]))[str(A)] == TOTAL


# ---------------------------------------------------------------------------
# 负向对照
# ---------------------------------------------------------------------------


@_skip
async def test_the_dirty_column_is_not_the_answer(orm_dsn, tree, pg):
    """夹具自证：旧列上的数与上面每一个期望值都不相等。

    上面五条用例全部对着手算常数断言，而「手算常数」只有在**读错列会得出不同的
    数**时才拦得住东西。这条把那个前提变成断言：

    * 旧列全表求和 58.75 —— 双计后代（月度用量此前就是这么多算一遍的）；
    * 旧列 root-only 求和 15.5 —— 漏掉没报回来的委派花费，正是门禁此前的盲区。

    两个数都 ≠ 35.75，所以任何一个读方悄悄退回 ``cost_cents``，必有用例转红。
    """
    rows = await pg.fetch(
        "SELECT id, cost_cents, own_cost_cents FROM public.agent_runs "
        " WHERE id = $1 OR root_run_id = $1",
        A,
    )
    assert {r["id"]: float(r["own_cost_cents"]) for r in rows} == OWNS
    assert {r["id"]: float(r["cost_cents"]) for r in rows} == DIRTY

    all_dirty = round(sum(DIRTY.values()), 4)
    assert all_dirty == 58.75 != TOTAL  # 双计
    assert DIRTY[A] == 15.5 != TOTAL  # 低报
