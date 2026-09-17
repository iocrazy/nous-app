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


class _Row:
    def __init__(self, id, status="completed", cost=None, **kw):
        self.id = id
        self.status = status
        self.team_id = kw.get("team_id", 42)
        self.user_id = kw.get("user_id", uuid4())
        self.model = kw.get("model", "doubao-seed-2-0-lite")
        self.prompt_tokens = kw.get("prompt_tokens", 10)
        self.completion_tokens = kw.get("completion_tokens", 20)
        self.metadata_json = {"cost": cost} if cost is not None else {}


def _db(monkeypatch, *, tree_rows, my_root=None, cas_rowcount=1, sink=None):
    """把 ``read_scope`` / ``write_scope`` 换成桩。

    读的第一条语句是「我的 root 是谁」，第二条是全树；写的那一条是 CAS。
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
            return _Res(tree_rows)

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
