"""用户裁定：一个回合的积分 = ceil(整棵树的**平台**花费之和)，一棵树只扣一次；
BYOK 的调用一分不扣。

**由谁来扣**这件事经真栈探针改过一次口径。计划原文写的是「root 定稿时扣」，探针
推翻了它：workforce 的委派是 fire-and-forget，**root 通常先于子 run 结束**（实测
一次回合：root 03:30:34 结束，三个子 run 03:30:48 / 03:30:36 / 03:30:27），而且
``subagent_done`` 只写到**直接父**的 transcript —— root 的 ``by_child`` 对 workforce
链恒为 ``{}``。「root 定稿扣整棵树」在那条链上退化成「每条 run 各 ceil 一次」，正是
要修的那个（真栈 ≈¢0.92 被收成 7 分）。

现在的口径：**谁把树收口谁扣**（树里全部行都终态时，CAS 抢到 root 行戳的那一条）。
金额按**行**聚合每条 run 自身的两道花费，不看 ``by_child``。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.billing import tree_charge
from app.services.ai.billing.tree_charge import bucket_tree, spend_of_run
from app.services.ai.runner import run_recorder as rr

# 只标 unit：``asyncio_mode = "auto"``（pyproject）已经接管 async 用例，模块级再
# 标一次 asyncio 会让本文件的同步分桶用例每条刷一条 PytestWarning。
pytestmark = [pytest.mark.unit]


# ── 分桶：单行 ──────────────────────────────────────────────────────────


def test_a_runs_own_spend_is_its_two_lanes_minus_their_byok_halves():
    s = spend_of_run(
        {
            "own_cents": 10.0,
            "own_byok_cents": 4.0,
            "media_cents": 5.0,
            "media_byok_cents": 5.0,
            # by_child 在这里**刻意被忽略** —— 子树自己有行。
            "by_child": {"c1": 3.0, "c2": 4.0},
        }
    )
    assert s.total == 15.0  # 10 own + 5 media，与 by_child 无关
    assert s.platform == 6.0  # 15 − (4 + 5)


def test_by_child_never_enters_a_rows_own_spend():
    """整棵树的行都在手里，孙子自己那一行就是它 —— 再读 ``by_child`` 就是把同一笔
    钱数两遍。而且 ``by_child`` 只存在于**直接父**的视图里，workforce 链上 root
    那份恒为空，依赖它等于依赖一个对半数链路不成立的东西。"""
    assert spend_of_run({"by_child": {"c1": 99.0}, "by_child_byok": {"c1": 1.0}}) == (
        tree_charge.RunSpend(total=0.0, platform=0.0)
    )


@pytest.mark.parametrize(
    "lane,view,expect_platform",
    [
        # own 那条虚高：media 的 5 分是平台付的，必须照收。
        ("own", {"own_cents": 10.0, "own_byok_cents": 999.0, "media_cents": 5.0}, 5.0),
        # media 那条虚高：own 的 10 分是平台付的，必须照收。
        (
            "media",
            {"own_cents": 10.0, "media_cents": 1.0, "media_byok_cents": 999.0},
            10.0,
        ),
    ],
)
def test_a_byok_lane_can_never_exceed_the_component_it_belongs_to(
    lane, view, expect_platform
):
    """两侧来源不同：``own_cents`` 可能来自 token 费率（``compute_cost_cents``），
    而 ``own_byok_cents`` 来自 step fold。BYOK 道虚高会把真该收的钱抹成 0，所以
    相减前钳位 —— 少收一次的代价远小于「整棵树静默免单」。

    ⚠️ 钳位必须**逐分量**，而且只有在「同一行里还有别的分量是平台付的」时才看得
    出来：单分量的行光靠结果那个 ``max(…, 0)`` 就已经是 0，一条只测单分量的用例
    **证不出钳位存在**（本 Task 实测：去掉 ``min`` 它照样绿）。"""
    assert (
        spend_of_run(view).platform == expect_platform
    ), f"{lane} 那条道抹掉了别的分量"


def test_an_absent_view_is_a_zero_row_not_a_crash():
    assert spend_of_run(None) == tree_charge.RunSpend(0.0, 0.0)


def test_junk_in_the_lanes_reads_as_zero_not_as_a_crash():
    s = spend_of_run({"own_cents": "10", "media_cents": True, "own_byok_cents": None})
    assert (s.total, s.platform) == (0.0, 0.0)


def test_own_cents_can_be_overridden_by_the_token_priced_figure():
    """``_finish`` 在费率已知时用 ``compute_cost_cents()`` 覆盖折叠值。"""
    s = spend_of_run({"own_cents": 1.0, "media_cents": 2.0}, own_cents=9.0)
    assert s.total == 11.0


# ── 分桶：全树 ──────────────────────────────────────────────────────────


def test_the_tree_is_the_sum_of_every_rows_own_spend():
    b = bucket_tree(
        [
            {"own_cents": 10.0, "media_cents": 5.0, "media_byok_cents": 5.0},
            {"own_cents": 3.0},
            {"own_cents": 4.0, "own_byok_cents": 4.0},
        ]
    )
    assert b.tree_total == 22.0  # 15 + 3 + 4
    assert b.tree_platform == 13.0  # 22 − 5 media BYOK − 4 own BYOK


def test_a_three_level_chain_sums_every_generation():
    """孙子自己那一行就在树里（``root_run_id`` 指的是**真** root），所以三层链
    不需要任何子树折叠就是全的。"""
    b = bucket_tree([{"own_cents": 1.0}, {"own_cents": 2.0}, {"own_cents": 4.0}])
    assert (b.tree_total, b.tree_platform) == (7.0, 7.0)


def test_an_empty_row_set_is_zero_but_callers_must_not_read_it_as_free():
    assert bucket_tree([]) == tree_charge.TreeBuckets(0.0, 0.0)


# ── settle_tree_if_closed ───────────────────────────────────────────────


def _after_cutover(**delta) -> datetime:
    """一个**晚于切换点**的时刻。

    刻意从配置里那个切换点推，而不是写 ``now()``：切换点是配置值，用 ``now()`` 就
    等于让这些用例依赖「跑测试的机器此刻已经过了那一刻」，换台钟慢的机器就集体转红
    （而且红在 ``pre_cutover`` 上，读起来像业务缺陷）。
    """
    base = tree_charge.cutover_at()
    assert base is not None, "切换点必须能解析，否则本文件的前提就不成立"
    return base + (timedelta(**delta) if delta else timedelta(seconds=1))


class _Row:
    def __init__(self, id, status="completed", cost=None, **kw):
        self.id = id
        self.status = status
        self.team_id = kw.get("team_id", 42)
        self.user_id = kw.get("user_id", uuid4())
        self.model = kw.get("model", "doubao-seed-2-0-lite")
        self.prompt_tokens = kw.get("prompt_tokens", 10)
        self.completion_tokens = kw.get("completion_tokens", 20)
        # 惰性求值：``_after_cutover()`` 要求切换点可解析，而「切换点坏掉」那条
        # 用例正好造的是解析不了的配置 —— 提前算默认值会让它红在 fixture 上。
        self.started_at = kw["started_at"] if "started_at" in kw else _after_cutover()
        self.ended_at = kw.get("ended_at", datetime.now(timezone.utc))
        md: dict[str, Any] = {}
        if cost is not None:
            md["cost"] = cost
        pending = kw.get("async_pending")
        if pending is not None:
            md["view"] = {"children": {"async_pending": pending}}
        self.metadata_json = md


def _db(
    monkeypatch,
    *,
    tree_rows,
    my_root=None,
    cas_rowcount=1,
    sink=None,
    ever_charged=False,
):
    """把 ``read_scope`` / ``write_scope`` 换成桩。

    读的第一条语句是「我的 root 是谁」，第二条是全树，**第三条两条路径都会走**
    （防回溯的「这棵树扣过钱没有」正查，在 CAS 之前）；写的那一条是 CAS。
    """

    class _Res:
        def __init__(self, rows):
            self._rows = rows

        def first(self):
            return self._rows[0] if self._rows else None

        def all(self):
            return self._rows

    class _ReadSession:
        def __init__(self):
            self._n = 0

        async def execute(self, stmt):
            self._n += 1
            if self._n == 1:
                return _Res([(my_root,)])
            if self._n == 2:
                return _Res(tree_rows)
            return _Res([(1,)] if ever_charged else [])

    class _WriteSession:
        async def execute(self, stmt):
            if sink is not None:
                sink.append(stmt)

            class _R:
                rowcount = cas_rowcount

            return _R()

    @asynccontextmanager
    async def _read():
        yield _ReadSession()

    @asynccontextmanager
    async def _write():
        yield _WriteSession()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", _read)
    monkeypatch.setattr(db_session, "write_scope", _write)


@pytest.fixture
def charged(monkeypatch):
    """只拦 ``reconcile_run`` —— 收口逻辑本身是被测对象，不打桩。"""
    from app.services.ai.billing.token_billing import ReconcileResult

    mock = AsyncMock(
        return_value=ReconcileResult(
            charged=True, charged_points=1.0, byo_key=False, usage_logged=False
        )
    )
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", mock)
    return mock


async def test_the_closer_charges_the_whole_tree_once(charged, monkeypatch):
    _db(
        monkeypatch,
        my_root=None,  # 我就是 root
        tree_rows=[
            _Row(800000000000001, cost={"own_cents": 10.0, "media_cents": 5.0}),
            _Row(900000000000002, cost={"own_cents": 3.0}),
            _Row(900000000000003, cost={"own_cents": 4.0}),
        ],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (True, "charged")
    kwargs = charged.await_args.kwargs
    assert kwargs["cost_points"] == 22.0
    # 一树一行流水：reference_id 是 root，读方 charged_points_for_run_trees 按
    # root_run_id 合计，所以它一行不用改。
    assert kwargs["run_id"] == "800000000000001"
    # 审计行由每条 run 自己的 _finish 写，收口这一次只扣钱。
    assert kwargs["log_usage"] is False
    assert kwargs["byo_key"] is False


async def test_a_tree_with_anyone_still_running_is_not_settled(charged, monkeypatch):
    """这正是「root 先于子 run 结束」时 root 那一次的结局 —— 它不扣，等最后一个。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(800000000000001, cost={"own_cents": 10.0}),
            _Row(900000000000002, status="running", cost={"own_cents": 3.0}),
        ],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "deferred")
    charged.assert_not_awaited()


async def test_the_late_child_closes_the_tree_the_root_could_not(charged, monkeypatch):
    """root 先结束（上一条）、子 run 后结束 —— 收口落在子 run 这一次，金额仍是
    整棵树。子 run 报的 root 是它行上的 ``root_run_id``。"""
    _db(
        monkeypatch,
        my_root=800000000000001,
        tree_rows=[
            _Row(800000000000001, status="failed", cost={"own_cents": 10.0}),
            _Row(900000000000002, cost={"own_cents": 3.0}),
        ],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="900000000000002")
    assert out.reason == "charged"
    assert charged.await_args.kwargs["cost_points"] == 13.0
    assert charged.await_args.kwargs["run_id"] == "800000000000001"


async def test_two_runs_closing_at_once_produce_exactly_one_charge(
    charged, monkeypatch
):
    """并发收口：CAS 只有一条能把 ``charged_at`` 戳上去，另一条 rowcount 为 0。

    可证伪点：把那道 ``rowcount != 1`` 判断删掉，这条立刻变成两次扣费。"""
    rows = [
        _Row(800000000000001, cost={"own_cents": 10.0}),
        _Row(900000000000002, cost={"own_cents": 3.0}),
    ]
    _db(monkeypatch, my_root=None, tree_rows=rows, cas_rowcount=1)
    first = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    _db(monkeypatch, my_root=800000000000001, tree_rows=rows, cas_rowcount=0)
    second = await tree_charge.settle_tree_if_closed(run_id="900000000000002")
    assert (first.reason, second.reason) == ("charged", "already")
    assert charged.await_count == 1


async def test_an_unreadable_rowcount_never_charges(charged, monkeypatch):
    """⚠️ 与 ``closed_by_us`` 的 ``!= 0`` **方向相反**：那边「不知道」继续走下去
    只是可能多写一行遥测，这边继续走下去是**可能重复扣一次真钱**。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[_Row(800000000000001, cost={"own_cents": 10.0})],
        cas_rowcount=None,
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "already")
    charged.assert_not_awaited()


async def test_a_tree_still_owing_async_children_is_not_settled(charged, monkeypatch):
    """评审 Critical 2：workforce 异步派发**不建子 run 行** —— 只插一条 workforce
    task 并发 ``subagent_spawned{child_run_id: None, mode: async}``，``agent_runs``
    的行是 worker 事后才建的。

    所以「树里的行全终态」**不蕴含**「这棵树跑完了」：root 派完活立刻结束时树上只有
    它自己，盖了戳，子 run 后来收口全撞 ``already``，委派的钱一分不进账 —— 正是本
    计划要修的那一类，只是从「by_child 恒空」搬到了「树成员恒少」。

    可用信号已经在库里：``view.children.async_pending``。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(800000000000001, cost={"own_cents": 10.0}, async_pending=2),
        ],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "pending_children")
    charged.assert_not_awaited()


async def test_a_child_that_has_materialised_no_longer_blocks(charged, monkeypatch):
    """``subagent_done`` 把 ``async_pending`` 减回去 —— 归零之后收口才成立。
    证明上一条不是「永远收不了口」。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(800000000000001, cost={"own_cents": 10.0}, async_pending=0),
            _Row(900000000000002, cost={"own_cents": 3.0}),
        ],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert out.reason == "charged"
    assert charged.await_args.kwargs["cost_points"] == 13.0


async def test_the_pending_gate_holds_until_the_grace_period_has_passed(
    charged, monkeypatch
):
    """清扫器带着 ``force_stale_pending`` 来，但树才刚结束 —— 还不到强制的时候。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[_Row(800000000000001, cost={"own_cents": 10.0}, async_pending=1)],
    )
    out = await tree_charge.settle_tree_if_closed(
        run_id="800000000000001", force_stale_pending=True
    )
    assert out.reason == "pending_children"
    charged.assert_not_awaited()


async def test_a_long_stale_pending_tree_is_force_settled(charged, monkeypatch):
    """兜底：派出去的任务永远不被 worker 取走时，那棵树没有任何一条 run 会再触发
    收口 —— 钱永久不进账，而且没有任何探针会说。超过宽限期就强制收口并记 WARNING。"""
    long_ago = datetime.now(timezone.utc) - tree_charge.PENDING_CHILDREN_GRACE * 2
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(
                800000000000001,
                cost={"own_cents": 10.0},
                async_pending=1,
                ended_at=long_ago,
            )
        ],
    )
    out = await tree_charge.settle_tree_if_closed(
        run_id="800000000000001", force_stale_pending=True
    )
    assert (out.settled, out.reason) == (True, "forced")
    assert charged.await_args.kwargs["cost_points"] == 10.0


async def test_a_tree_that_was_already_charged_is_never_force_settled(
    charged, monkeypatch
):
    """防回溯：本机制上线前的历史树在**旧口径**下已经逐 run 扣过钱，
    ``point_transactions`` 里必然留着行。强制收口把它们扫进来就是二次扣费 ——
    而这类扣费不会报错、只会让用户莫名其妙少一笔余额。"""
    long_ago = datetime.now(timezone.utc) - tree_charge.PENDING_CHILDREN_GRACE * 2
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(
                800000000000001,
                cost={"own_cents": 10.0},
                async_pending=1,
                ended_at=long_ago,
            )
        ],
        ever_charged=True,
    )
    out = await tree_charge.settle_tree_if_closed(
        run_id="800000000000001", force_stale_pending=True
    )
    assert (out.settled, out.reason) == (False, "legacy_charged")
    charged.assert_not_awaited()


async def test_an_unknown_end_time_is_never_treated_as_stale(charged, monkeypatch):
    """``ended_at`` 读不出来 = 「不知道到没到点」。按「还没到」处理 —— 宁可让清扫器
    下一轮再看一眼，也不要凭一个空值提前强制收口。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(
                800000000000001,
                cost={"own_cents": 10.0},
                async_pending=1,
                ended_at=None,
            )
        ],
    )
    out = await tree_charge.settle_tree_if_closed(
        run_id="800000000000001", force_stale_pending=True
    )
    assert out.reason == "pending_children"


async def test_a_charge_that_raises_releases_the_stamp(charged, monkeypatch):
    """戳先盖、钱后扣，所以扣费**抛异常**时必须把戳撤回去 —— 否则这棵树永久收不到
    钱，除了一行日志之外没有任何痕迹。

    ⚠️ 只撤 raise 这一种：``charged=False`` 的拒绝（余额不足、无 team、急停）是
    「扣过了、被拒了」，撤戳会让它每来一条 run 就重试一次。"""
    writes: list[Any] = []
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[_Row(800000000000001, cost={"own_cents": 10.0})],
        sink=writes,
    )
    charged.side_effect = RuntimeError("points service exploded")
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "error")
    # 两条写：盖戳 + 撤戳。
    assert len(writes) == 2
    params = writes[-1].compile().params
    assert "charged_at" in str(params.values()), "撤戳那条没有指向 charged_at"


async def test_the_real_async_timeline_settles_once_when_the_event_lands(
    charged, monkeypatch
):
    """**按真实时序走一遍**（评审修复轮 3 的 Critical）。

    ``async_pending`` 只在 ``subagent_done`` 折进**父**的视图时才减一，而那条事件是
    ``agent_worker`` 在子 run 跑完**之后**才写的。所以异步链上的三拍是：

    1. root 结束 —— ``pending=1`` → 不收口；
    2. 子 run 结束 —— 全树终态，但 root 的 ``pending`` **仍是 1** → 还是不收口；
    3. ``subagent_done`` 落地、``pending`` 归零 —— 这一刻必须有人再叫一次收口，
       否则整棵树停在 ``pending_children``，只能等 2 小时兜底（而那条路径还会打一条
       「async child never materialised」的 WARNING，与事实正相反）。

    第三拍的调用方是 ``agent_worker`` 写完事件之后那一次。"""
    root_pending = _Row(800000000000001, cost={"own_cents": 10.0}, async_pending=1)
    child = _Row(900000000000002, cost={"own_cents": 3.0})

    # 第一拍与第二拍：都不收口。
    for caller in ("800000000000001", "900000000000002"):
        _db(
            monkeypatch,
            my_root=None if caller == "800000000000001" else 800000000000001,
            tree_rows=[root_pending, child],
        )
        out = await tree_charge.settle_tree_if_closed(run_id=caller)
        assert out.reason == "pending_children", caller
    charged.assert_not_awaited()

    # 第三拍：事件落地，父视图归零，worker 补调一次。
    root_done = _Row(800000000000001, cost={"own_cents": 10.0}, async_pending=0)
    _db(monkeypatch, my_root=800000000000001, tree_rows=[root_done, child])
    out = await tree_charge.settle_tree_if_closed(run_id="900000000000002")
    assert (out.settled, out.reason) == (True, "charged")
    assert charged.await_count == 1
    assert charged.await_args.kwargs["cost_points"] == 13.0


async def test_a_tree_already_charged_the_old_way_is_never_charged_again(
    charged, monkeypatch
):
    """防回溯必须挂在**正常**路径上，不能只挂强制路径（评审修复轮 3 的 Important 1）。

    上线前的老树在旧口径下已经**逐 run** 扣过钱，而它的 ``billing.charged_at`` 当然
    是空的。部署会重启 worker、在飞的 run 拿 heartbeat_lost 是常态 —— 那些树随后被
    标终态，走的正是这条**正常**路径。少了这道正查，整棵树会按新口径再扣满一次，
    叠在旧的逐 run 扣费之上。这不是边角情形，是上线首轮最可能发生的那一种。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[_Row(800000000000001, cost={"own_cents": 10.0})],
        ever_charged=True,
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "legacy_charged")
    charged.assert_not_awaited()


def test_the_legacy_charge_probe_can_use_the_partial_index():
    """``type = 'consume'`` 不能省：mig 474 的
    ``idx_point_transactions_agent_run_consume`` 谓词是
    ``type = 'consume' AND reference_type = 'agent_run'``。少一个条件谓词就不被蕴含，
    planner 悄悄改走顺扫 —— 不会报错，也不会有任何东西说出来。

    断的是**编译出来的 WHERE**，不是源码字面量：后者钉的是写法，等价改写一次就红，
    而真正要钉的是那两个谓词的**值**都在查询里。"""
    from sqlalchemy.dialects.postgresql import dialect

    from app.services.billing.agent_run_reference import AGENT_RUN_REFERENCE_TYPE

    compiled = tree_charge.legacy_charge_probe_stmt([1, 2]).compile(dialect=dialect())
    # ``IN`` 那个绑定是 list（不可哈希），所以按字符串收集而不是塞进 set。
    bound = [v for v in compiled.params.values() if isinstance(v, str)]
    assert "consume" in bound, bound
    assert AGENT_RUN_REFERENCE_TYPE in bound, bound
    sql = str(compiled)
    assert "point_transactions" in sql and "WHERE" in sql


async def test_a_pure_byok_tree_is_reported_as_byo_key(charged, monkeypatch):
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(
                800000000000001,
                cost={"own_cents": 8.0, "own_byok_cents": 8.0},
            )
        ],
    )
    await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    kwargs = charged.await_args.kwargs
    assert kwargs["cost_points"] == 0.0
    assert kwargs["byo_key"] is True


async def test_a_tree_that_spent_nothing_is_not_reported_as_byo_key(
    charged, monkeypatch
):
    """产品含义不同：一个是「你自己付了」，一个是「什么都没烧」。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[_Row(800000000000001, cost={"own_cents": 0.0})],
    )
    await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert charged.await_args.kwargs["byo_key"] is False


@pytest.mark.parametrize(
    "case,kw",
    [
        ("读不到我自己那一行", {"my_root": None, "tree_rows": [], "no_self": True}),
        ("树上一行都没有", {"my_root": None, "tree_rows": []}),
    ],
)
async def test_nothing_readable_is_never_a_charge(case, kw, charged, monkeypatch):
    """读到零行 ≠ 这棵树没花钱。什么都不做，等下一次。"""
    if kw.pop("no_self", False):

        @asynccontextmanager
        async def _read():
            class _S:
                async def execute(self, stmt):
                    class _R:
                        def first(self):
                            return None

                        def all(self):
                            return []

                    return _R()

            yield _S()

        import app.db.session as db_session

        monkeypatch.setattr(db_session, "read_scope", _read)
    else:
        _db(monkeypatch, **kw)
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "unknown"), case
    charged.assert_not_awaited()


async def test_a_read_failure_is_never_a_charge(charged, monkeypatch):
    @asynccontextmanager
    async def _boom():
        class _S:
            async def execute(self, stmt):
                raise RuntimeError("supabase down")

        yield _S()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", _boom)
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "error")
    charged.assert_not_awaited()


async def test_a_run_without_an_id_has_no_tree_to_settle(charged):
    out = await tree_charge.settle_tree_if_closed(run_id=None)
    assert (out.settled, out.reason) == (False, "no_run_id")
    charged.assert_not_awaited()


# ── _finish 这一侧 ──────────────────────────────────────────────────────


class _Writer:
    def __init__(self, views):
        self.views = views
        self.persisted = 0

    async def refold_external_slices(self):
        return None

    async def persist_views(self):
        self.persisted += 1


def _recorder(*, views, run_id="900000000000001"):
    rec = rr.RunRecorder.__new__(rr.RunRecorder)
    rec.run_id = run_id
    rec.user_id, rec.agent_id = uuid4(), uuid4()
    rec.team_id, rec.project_id, rec.session_id = 42, None, None
    rec.model, rec.trigger, rec.attribution = (
        "doubao-seed-2-0-lite",
        "chat",
        "direct_human",
    )
    rec._prompt_tokens, rec._completion_tokens, rec._cached_input_tokens = 10, 20, 0
    rec._skill_slugs_used, rec._output_summary = [], None
    rec._prompt_rate = rec._completion_rate = None
    rec._event_writer = _Writer(views)
    rec.parent_run_id = None
    rec.credential_origin = None
    return rec


@pytest.fixture
def audited(monkeypatch):
    """拦下审计用的 reconcile_run、树收口、UPDATE、小时表与检索投影。"""
    audit = AsyncMock()
    settle = AsyncMock(return_value=tree_charge.SettleOutcome(True, "charged", 3.0))

    @asynccontextmanager
    async def _scope():
        class _S:
            async def execute(self, stmt):
                class _R:
                    rowcount = 1

                return _R()

        yield _S()

    import app.db.session as db_session
    import app.services.ai_usage as ai_usage

    monkeypatch.setattr(db_session, "write_scope", _scope)
    monkeypatch.setattr(ai_usage, "write_scope", _scope)
    monkeypatch.setattr(ai_usage, "record_usage", AsyncMock())
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", audit)
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.settle_tree_if_closed", settle
    )
    monkeypatch.setattr(
        "app.services.search.projection.project_run_best_effort", AsyncMock()
    )
    return audit, settle


_VIEWS = {
    "cost": {
        "own_cents": 10.0,
        "own_byok_cents": 0.0,
        "by_child": {"c1": 3.0, "c2": 4.0},
        "media_cents": 5.0,
        "media_byok_cents": 0.0,
    }
}


async def test_finish_writes_its_own_audit_row_and_charges_nothing(audited):
    audit, _ = audited
    await _recorder(views=_VIEWS)._finish(status="completed")
    kwargs = audit.await_args.kwargs
    # 审计行记**自身**真实花费（own + media），与 by_child 无关。
    assert kwargs["usage_cost_points"] == 15.0
    # 扣费是整棵树的事，不在这一步。
    assert kwargs["cost_points"] == 0.0
    assert kwargs["charge_deferred"] is True
    assert kwargs["byo_key"] is False


async def test_finish_asks_for_a_tree_settle_on_every_terminal_status(audited):
    """任一终态都算收口：平台的钱已经烧掉了，与这个回合成没成功无关。"""
    _, settle = audited
    for status in ("completed", "failed", "cancelled"):
        await _recorder(views=_VIEWS)._finish(status=status)
    assert settle.await_count == 3
    assert settle.await_args.kwargs["run_id"] == "900000000000001"


async def test_a_run_that_spent_nothing_itself_writes_no_audit_row_but_still_settles(
    audited,
):
    """审计行的条件是「**这条 run 自己**真的烧了钱」——
    ``summarize_user_usage`` 的 ``overall_run_count`` 直接数行，多写就是上抬。
    但收口照叫：这条 run 可能正是树里最后一个结束的。"""
    audit, settle = audited
    await _recorder(
        views={"cost": {"own_cents": 0.0, "by_child": {"c1": 3.0}}}
    )._finish(status="completed")
    audit.assert_not_awaited()
    settle.assert_awaited_once()


async def test_the_final_cost_view_is_persisted_before_the_run_goes_terminal(
    monkeypatch,
):
    """收口按行读 ``agent_runs.metadata_json.cost``，而 ``own_cents`` 的最终值是在
    内存里定稿的。**一旦状态不再是 running，树里任何一条 run 都可能立刻收口并读走
    这里的值** —— 所以镜像必须发生在那条终态 UPDATE **之前**，顺序本身就是防线。

    可证伪点：断言的是**次序**而不是「调过了」。把 ``persist_views()`` 挪到
    UPDATE 之后，这条立刻转红。"""
    order: list[str] = []

    @asynccontextmanager
    async def _scope():
        class _S:
            async def execute(self, stmt):
                if getattr(getattr(stmt, "table", None), "name", None) == "agent_runs":
                    order.append("terminal-update")

                class _R:
                    rowcount = 1

                return _R()

        yield _S()

    import app.db.session as db_session
    import app.services.ai_usage as ai_usage

    monkeypatch.setattr(db_session, "write_scope", _scope)
    monkeypatch.setattr(ai_usage, "write_scope", _scope)
    monkeypatch.setattr(ai_usage, "record_usage", AsyncMock())
    monkeypatch.setattr(
        "app.services.ai.billing.token_billing.reconcile_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.settle_tree_if_closed",
        AsyncMock(return_value=tree_charge.SettleOutcome(False, "deferred")),
    )
    monkeypatch.setattr(
        "app.services.search.projection.project_run_best_effort", AsyncMock()
    )

    rec = _recorder(views=_VIEWS)

    async def _persist():
        order.append("persist-views")

    rec._event_writer.persist_views = _persist
    await rec._finish(status="completed")

    assert "persist-views" in order and "terminal-update" in order
    assert order.index("persist-views") < order.index("terminal-update")


async def test_a_run_that_lost_the_idempotency_guard_neither_audits_nor_settles(
    audited, monkeypatch
):
    """rowcount 为 0 = 别人已经把这条 run 收工了。"""
    audit, settle = audited

    @asynccontextmanager
    async def _lost():
        class _S:
            async def execute(self, stmt):
                class _R:
                    rowcount = 0

                return _R()

        yield _S()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "write_scope", _lost)
    await _recorder(views=_VIEWS)._finish(status="completed")
    audit.assert_not_awaited()
    settle.assert_not_awaited()


# ── token_billing 的两个新旗标 ──────────────────────────────────────────


@asynccontextmanager
async def _capture_into(sink):
    class _S:
        async def execute(self, stmt):
            sink.append(stmt)
            return None

    yield _S()


async def _reconcile(**over):
    from app.services.ai.billing import token_billing as tb

    return await tb.reconcile_run(
        run_id="900000000000001",
        user_id=uuid4(),
        team_id=over.pop("team_id", None),
        project_id=None,
        session_id=None,
        agent_id=uuid4(),
        model="qwen-max",
        prompt_tokens=10,
        completion_tokens=20,
        **over,
    )


async def test_the_audit_row_carries_the_runs_own_spend_not_the_tree_charge():
    """``cost_points`` 是「该扣多少分」，``usage_cost_points`` 是写进
    ``ai_usage_logs`` 的真实花费。沿用同一个入参会让审计表双计。"""
    seen: list[Any] = []
    with patch("app.db.session.write_scope", new=lambda: _capture_into(seen)):
        await _reconcile(cost_points=22.0, usage_cost_points=15.0, byo_key=False)
    assert float(seen[0].compile().params["cost_points"]) == 15.0


async def test_omitting_usage_cost_points_keeps_the_old_single_number_behaviour():
    seen: list[Any] = []
    with patch("app.db.session.write_scope", new=lambda: _capture_into(seen)):
        await _reconcile(cost_points=7.0, byo_key=False)
    assert float(seen[0].compile().params["cost_points"]) == 7.0


async def test_log_usage_false_really_skips_the_insert():
    """收口那一次用它 —— 每条 run 自身的用量已经由它自己的 ``_finish`` 记过了。"""
    seen: list[Any] = []
    with patch("app.db.session.write_scope", new=lambda: _capture_into(seen)):
        result = await _reconcile(cost_points=3.0, log_usage=False, byo_key=False)
    assert seen == []
    assert result.usage_logged is False


async def test_the_three_zero_charge_reasons_do_not_collapse_into_one_note():
    """裁定 ⑤：「你自己付了」/「等收口那一次扣」/「什么都没烧」是三件事。"""
    seen: list[Any] = []
    with patch("app.db.session.write_scope", new=lambda: _capture_into(seen)):
        byok = await _reconcile(cost_points=0.0, byo_key=True, team_id=42)
        deferred = await _reconcile(
            cost_points=0.0, byo_key=False, charge_deferred=True, team_id=42
        )
        idle = await _reconcile(cost_points=0.0, byo_key=False, team_id=42)
    assert "billed by user's provider" in (byok.note or "")
    assert "deferred" in (deferred.note or "")
    assert "zero cost" in (idle.note or "")
    assert len({byok.note, deferred.note, idle.note}) == 3


# ── 切换点：只向前不追扣（2026-09-17 事故） ─────────────────────────────


async def test_a_tree_that_started_before_the_cutover_is_never_charged(
    charged, monkeypatch
):
    """上线前开始的回合一律不扣、不盖戳 —— 用户裁定「只向前不追扣」。

    这是 2026-09-17 那次退款事故的直接判据。当时唯一的防回溯是「这棵树在
    ``point_transactions`` 里扣过钱没有」，而 09-10~09-15 那批树结束于积分链修好
    之前，一分钱都没扣过 —— 正查照单放行，43 棵树被整棵扣了 87 分。
    **「有没有扣过钱」回答不了「该不该扣」，时间才是判据。**
    """
    sink: list = []
    _db(
        monkeypatch,
        my_root=None,
        sink=sink,
        tree_rows=[
            _Row(
                800000000000001,
                started_at=_after_cutover() - timedelta(days=5),
                cost={"own_cents": 40.0},
            ),
            _Row(900000000000002, cost={"own_cents": 3.0}),
        ],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "pre_cutover")
    charged.assert_not_awaited()
    # 戳也不能盖：盖了就等于宣称「这棵树本机制收过了」，而它一分没收。
    assert sink == []


async def test_a_tree_that_started_after_the_cutover_still_charges(
    charged, monkeypatch
):
    """正对照。少了它，上一条用「永远不扣」也能通过 —— 那是把计费整个关掉。"""
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[_Row(800000000000001, cost={"own_cents": 7.0})],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (True, "charged")
    assert charged.await_args.kwargs["cost_points"] == 7.0


async def test_an_unreadable_cutover_refuses_to_charge_anything(charged, monkeypatch):
    """配置坏掉 = 不知道边界在哪。往不扣那侧倒，并且与 ``pre_cutover`` 分开上报 ——
    两者都是「没扣」，但一个是正常的老树、一个是需要人去修的配置。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_POINTS_TREE_CUTOVER", "not-a-timestamp")
    sink: list = []
    _db(
        monkeypatch,
        my_root=None,
        sink=sink,
        tree_rows=[_Row(800000000000001, started_at=datetime.now(timezone.utc))],
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "cutover_unreadable")
    charged.assert_not_awaited()
    assert sink == []


def test_the_cutover_is_read_as_an_absolute_instant():
    """裸时间当 UTC；带时区的按它自己的时区。切换点漂到部署机的本地时区上，
    边界就会随机器走 —— 而它必须是同一个绝对时刻。"""
    from app.core.config import settings

    for raw, expected in (
        ("2026-09-17T06:42:56Z", datetime(2026, 9, 17, 6, 42, 56, tzinfo=timezone.utc)),
        ("2026-09-17T06:42:56", datetime(2026, 9, 17, 6, 42, 56, tzinfo=timezone.utc)),
        (
            "2026-09-17T14:42:56+08:00",
            datetime(2026, 9, 17, 6, 42, 56, tzinfo=timezone.utc),
        ),
    ):
        with patch.object(settings, "AGENT_POINTS_TREE_CUTOVER", raw):
            assert tree_charge.cutover_at() == expected

    for bad in ("", "   ", "yesterday"):
        with patch.object(settings, "AGENT_POINTS_TREE_CUTOVER", bad):
            assert tree_charge.cutover_at() is None


def test_a_missing_cutover_key_reads_as_unknown_not_as_zero(monkeypatch):
    """键根本不在（旧镜像 / 半截配置）时不能当成「纪元 0」—— 那等于整个边界失效。

    ⚠️ 这里换掉整个 ``settings`` 对象而不是 ``monkeypatch.setattr`` 一个值：
    Settings 开了 ``validate_assignment``，把 None 赋给一个 ``str`` 字段会直接
    抛 ValidationError，测不到「读不出来」那条分支。
    """
    from types import SimpleNamespace

    import app.core.config as config_mod

    monkeypatch.setattr(config_mod, "settings", SimpleNamespace())
    assert tree_charge.cutover_at() is None
