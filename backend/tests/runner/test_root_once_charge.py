"""用户裁定：一个回合的积分 = ceil(整棵树的**平台**花费之和)，只在 root 定稿时
扣一次；BYOK 的调用一分不扣。

此前是「每条 run 各 ceil 一次自身花费」——一次带委派的回合在 point_transactions
里是 6 行，真栈实测 ≈¢0.92 被收成 7 分。读方 charged_points_for_run_trees 按
root_run_id 全树合计，所以改成 root 一次扣之后它一行不用改：合计从「6 行相加」
变成「1 行」，同一个数。
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.billing.tree_charge import bucket_tree
from app.services.ai.runner import run_recorder as rr

# 只标 unit：``asyncio_mode = "auto"``（pyproject）已经接管 async 用例，模块级再
# 标一次 asyncio 会让本文件的同步分桶用例每条刷一条 PytestWarning。
pytestmark = [pytest.mark.unit]


# ── 纯分桶 ──────────────────────────────────────────────────────────────


def test_the_tree_bucket_subtracts_all_three_byok_lanes():
    b = bucket_tree(
        {
            "own_byok_cents": 4.0,
            "media_cents": 5.0,
            "media_byok_cents": 5.0,
            "by_child": {"c1": 3.0, "c2": 4.0},
            "by_child_byok": {"c1": 3.0},
        },
        10.0,
    )
    assert b.tree_total == 22.0  # 10 own + 7 children + 5 media
    assert b.tree_platform == 10.0  # 22 − (4 + 3 + 5)
    assert b.own_total == 15.0  # 10 own + 5 media
    assert b.own_platform == 6.0  # 15 − (4 + 5)


@pytest.mark.parametrize(
    "lane,view,expect_tree,expect_own",
    [
        # own 那条虚高：media 的 5 分是平台付的，必须照收。
        ("own", {"own_byok_cents": 999.0, "media_cents": 5.0}, 5.0, 5.0),
        # media 那条虚高：own 的 10 分是平台付的，必须照收。
        ("media", {"media_cents": 1.0, "media_byok_cents": 999.0}, 10.0, 10.0),
        # by_child 那条虚高：own + media 都是平台付的，必须照收。
        (
            "by_child",
            {
                "by_child": {"c1": 1.0},
                "by_child_byok": {"c1": 999.0},
                "media_cents": 5.0,
            },
            15.0,
            15.0,
        ),
    ],
)
def test_a_byok_lane_can_never_exceed_the_component_it_belongs_to(
    lane, view, expect_tree, expect_own
):
    """两侧来源不同：``own_cents`` 可能来自 token 费率（``compute_cost_cents``），
    而 ``own_byok_cents`` 来自 step fold。BYOK 道虚高会把真该收的钱抹成 0，
    所以相减前钳位 —— 少收一次的代价远小于「整棵树静默免单」。

    ⚠️ 钳位必须**逐分量**，而且只有在「同一棵树里还有别的分量是平台付的」时才
    看得出来：单分量的树光靠结果那个 ``max(…, 0)`` 就已经是 0，一条只测单分量的
    用例**证不出钳位存在**（本 Task 实测：去掉 ``min`` 它照样绿）。三条道各来一
    组，虚高的那条旁边都留着真该收的钱。"""
    b = bucket_tree(view, 10.0)
    assert b.tree_platform == expect_tree, f"{lane} 那条道虚高把别的分量也抹掉了"
    assert b.own_platform == expect_own


def test_no_folds_at_all_is_a_plain_platform_tree():
    b = bucket_tree(None, 3.0)
    assert (b.tree_total, b.tree_platform, b.own_total, b.own_platform) == (
        3.0,
        3.0,
        3.0,
        3.0,
    )


def test_junk_in_the_lanes_reads_as_zero_not_as_a_crash():
    b = bucket_tree(
        {"own_byok_cents": "4", "by_child": None, "media_byok_cents": True}, 10.0
    )
    assert b.tree_platform == 10.0


# ── _finish 的扣费分支 ──────────────────────────────────────────────────


class _Writer:
    def __init__(self, views):
        self.views = views

    async def refold_external_slices(self):
        return None


def _recorder(*, views, parent_run_id=None, run_id="900000000000001"):
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
    rec.parent_run_id = parent_run_id
    rec.credential_origin = None
    return rec


@pytest.fixture
def billed(monkeypatch):
    """拦下 reconcile_run、agent_runs 的 UPDATE、小时表 upsert 与检索投影。"""
    charged = AsyncMock()

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
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    monkeypatch.setattr(
        "app.services.search.projection.project_run_best_effort", AsyncMock()
    )
    return charged


_TREE = {
    "cost": {
        "own_cents": 10.0,
        "own_byok_cents": 0.0,
        "by_child": {"c1": 3.0, "c2": 4.0},
        "by_child_byok": {},
        "media_cents": 5.0,
        "media_byok_cents": 0.0,
    }
}


async def test_the_root_charges_the_whole_tree_exactly_once(billed):
    await _recorder(views=_TREE)._finish(status="completed")
    assert billed.await_count == 1
    kwargs = billed.await_args.kwargs
    assert kwargs["cost_points"] == 22.0  # 整棵树
    assert kwargs["usage_cost_points"] == 15.0  # 审计行仍记自身真实花费
    assert kwargs["byo_key"] is False


async def test_a_child_does_not_charge_while_its_root_is_still_running(
    billed, monkeypatch
):
    """root 会替它收（它在 root 的 by_child 里）。两边都收就是对同一笔钱收两次。"""
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.root_run_is_settled",
        AsyncMock(return_value=False),
    )
    await _recorder(
        views={"cost": {"own_cents": 3.0, "by_child": {}, "media_cents": 0.0}},
        parent_run_id="800000000000001",
    )._finish(status="completed")
    assert [c.kwargs["cost_points"] for c in billed.await_args_list] == [0.0]
    # 审计行照写：用量表要记真实花费（用户裁定 2）。
    assert billed.await_args.kwargs["usage_cost_points"] == 3.0


async def test_a_late_child_charges_its_own_platform_spend(billed, monkeypatch):
    """root 已经终态 → 它的 refold 快照里没有这个子 run，谁也不会替它收。"""
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.root_run_is_settled",
        AsyncMock(return_value=True),
    )
    await _recorder(
        views={
            "cost": {
                "own_cents": 3.0,
                "own_byok_cents": 1.0,
                "by_child": {},
                "media_cents": 0.0,
            }
        },
        parent_run_id="800000000000001",
    )._finish(status="completed")
    assert billed.await_args.kwargs["cost_points"] == 2.0  # 3 − 1 BYOK


async def test_the_refold_race_bills_the_child_at_most_once(billed, monkeypatch):
    """竞态：子 run 在 root 的 ``refold_external_slices()`` 之后、那条 UPDATE
    之前结束。root 的快照里没有它（所以 root 不收），它看到的 root 还是
    ``running``（所以它也不收）—— 宁少收不重收。

    可证伪点：断言这个子 run 的 3 分**恰好出现 0 次或 1 次，绝不 2 次**。
    把 ``_finish`` 里那个 elif 改成无条件补扣，这条会立刻转红。"""
    monkeypatch.setattr(
        "app.services.ai.billing.tree_charge.root_run_is_settled",
        AsyncMock(return_value=False),
    )
    child_views = {"cost": {"own_cents": 3.0, "by_child": {}, "media_cents": 0.0}}
    await _recorder(views=child_views, parent_run_id="800000000000001")._finish(
        status="completed"
    )
    # root 的快照没赶上这个子 run。
    root_views = {"cost": {"own_cents": 10.0, "by_child": {}, "media_cents": 0.0}}
    await _recorder(views=root_views, run_id="800000000000001")._finish(
        status="completed"
    )
    charged_amounts = [c.kwargs["cost_points"] for c in billed.await_args_list]
    assert charged_amounts == [0.0, 10.0]
    assert sum(1 for a in charged_amounts if a == 3.0) <= 1


async def test_a_pure_byok_tree_is_reported_as_byo_key_not_as_zero_spend(billed):
    """产品含义不同：一个是「你自己付了」，一个是「什么都没烧」。
    ``ReconcileResult.note`` 两者必须分得开（裁定 ⑤）。"""
    await _recorder(
        views={
            "cost": {
                "own_cents": 8.0,
                "own_byok_cents": 8.0,
                "by_child": {},
                "media_cents": 0.0,
            }
        }
    )._finish(status="completed")
    kwargs = billed.await_args.kwargs
    assert kwargs["cost_points"] == 0.0
    assert kwargs["byo_key"] is True


async def test_a_tree_that_spent_nothing_is_not_reported_as_byo_key(billed):
    await _recorder(
        views={"cost": {"own_cents": 0.0, "by_child": {}, "media_cents": 0.0}}
    )._finish(status="completed")
    # 一分没花：没有审计行可写，也没有钱可扣。
    assert billed.await_count == 0


async def test_the_two_zero_charge_reasons_do_not_collapse_into_one_note():
    """裁定 ⑤ 的另一半：两个都「零扣费」的结局在 ``ReconcileResult.note`` 里
    必须分得开。上面两条只证了 ``_finish`` 传对了 ``byo_key``，这条证了
    ``reconcile_run`` 真的把两者说成两件事。"""

    @asynccontextmanager
    async def _ok():
        class _S:
            async def execute(self, stmt):
                return None

        yield _S()

    from app.services.ai.billing import token_billing as tb

    async def _run(**over):
        with patch("app.db.session.write_scope", new=_ok):
            return await tb.reconcile_run(
                run_id="900000000000001",
                user_id=uuid4(),
                team_id=42,
                project_id=None,
                session_id=None,
                agent_id=uuid4(),
                model="qwen-max",
                prompt_tokens=10,
                completion_tokens=20,
                **over,
            )

    byok = await _run(cost_points=0.0, byo_key=True)
    idle = await _run(cost_points=0.0, byo_key=False)
    assert "billed by user's provider" in (byok.note or "")
    assert "zero cost" in (idle.note or "")
    assert byok.note != idle.note


async def test_the_audit_row_carries_the_runs_own_spend_not_the_tree_charge():
    """裁定 ⑬：``cost_points`` 是「该扣多少分」，``usage_cost_points`` 是写进
    ``ai_usage_logs`` 的真实花费。沿用同一个入参会让审计表变成「root 记树 +
    每个子 run 记自己」双计。"""
    seen: list[Any] = []

    @asynccontextmanager
    async def _capture():
        class _S:
            async def execute(self, stmt):
                seen.append(stmt)
                return None

        yield _S()

    from app.services.ai.billing import token_billing as tb

    with patch("app.db.session.write_scope", new=_capture):
        await tb.reconcile_run(
            run_id="900000000000001",
            user_id=uuid4(),
            team_id=None,  # 早退，只留审计行
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=22.0,
            usage_cost_points=15.0,
            byo_key=False,
        )
    values = seen[0].compile().params
    assert float(values["cost_points"]) == 15.0


async def test_omitting_usage_cost_points_keeps_the_old_single_number_behaviour():
    """既有调用方零改动：不传时审计行仍记 ``cost_points``。"""
    seen: list[Any] = []

    @asynccontextmanager
    async def _capture():
        class _S:
            async def execute(self, stmt):
                seen.append(stmt)
                return None

        yield _S()

    from app.services.ai.billing import token_billing as tb

    with patch("app.db.session.write_scope", new=_capture):
        await tb.reconcile_run(
            run_id="900000000000001",
            user_id=uuid4(),
            team_id=None,
            project_id=None,
            session_id=None,
            agent_id=uuid4(),
            model="qwen-max",
            prompt_tokens=10,
            completion_tokens=20,
            cost_points=7.0,
            byo_key=False,
        )
    assert float(seen[0].compile().params["cost_points"]) == 7.0


async def test_the_idempotency_guard_still_covers_the_new_branch(billed, monkeypatch):
    """rowcount 为 0 = 别人已经把这条 run 收工了。root-once 之后单次金额更大，
    这道守卫更不能漏。"""

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
    await _recorder(views=_TREE)._finish(status="completed")
    billed.assert_not_awaited()


# ── root_run_is_settled 的判据 ──────────────────────────────────────────


def _read_scope_yielding(rows, *, raises=False):
    """``read_scope()`` 替身：按顺序把 ``rows`` 喂给每次 ``execute().first()``。"""

    class _Res:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    class _Session:
        def __init__(self):
            self._left = list(rows)

        async def execute(self, *_a, **_kw):
            if raises:
                raise RuntimeError("supabase down")
            return _Res(self._left.pop(0))

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


def _patch_read_scope(monkeypatch, scope):
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", scope)


async def test_a_settled_root_lets_the_late_child_charge(monkeypatch):
    from app.services.ai.billing import tree_charge

    _patch_read_scope(
        monkeypatch, _read_scope_yielding([(800000000000001, None), ("completed",)])
    )
    assert (
        await tree_charge.root_run_is_settled(
            run_id="900000000000001", parent_run_id="800000000000001"
        )
        is True
    )


async def test_a_running_root_means_the_child_must_not_charge(monkeypatch):
    from app.services.ai.billing import tree_charge

    _patch_read_scope(
        monkeypatch, _read_scope_yielding([(800000000000001, None), ("running",)])
    )
    assert (
        await tree_charge.root_run_is_settled(
            run_id="900000000000001", parent_run_id="800000000000001"
        )
        is False
    )


async def test_the_row_falls_back_to_parent_run_id_then_to_the_callers_copy(
    monkeypatch,
):
    """``_attach_to_parent_run`` 失败时行上两列都是 NULL（已记票的既有缺陷）。
    回落到调用方手里那个 —— 但仍然只是**查**，查不到照旧不扣。"""
    from app.services.ai.billing import tree_charge

    _patch_read_scope(monkeypatch, _read_scope_yielding([(None, None), ("completed",)]))
    assert (
        await tree_charge.root_run_is_settled(
            run_id="900000000000001", parent_run_id="800000000000001"
        )
        is True
    )


@pytest.mark.parametrize(
    "case,scope_args,parent",
    [
        # except 分支：一次读失败。
        ("读库整个炸了", {"rows": [], "raises": True}, "800000000000001"),
        # 「root 那行不存在」分支 —— 与上面那条是**不同**的代码路径，各来一条。
        ("root 那行查不到", {"rows": [(800000000000001, None), None]}, None),
        ("root 的 status 是 NULL", {"rows": [(800000000000001, None), (None,)]}, None),
        # 「树上根本没有 root 可指」分支：行上两列都 NULL，调用方手里也没有。
        ("自己这行查不到且调用方也没父", {"rows": [None]}, None),
        ("行上两列都 NULL 且调用方也没父", {"rows": [(None, None)]}, None),
    ],
)
async def test_anything_it_cannot_read_reads_as_not_settled(
    case, scope_args, parent, monkeypatch
):
    """裁定 ④：「不知道」按「root 会替我收」处理 —— 宁少收不重收。读失败回 True
    就是一次凭空的重复扣费，而这条链上没有任何对账会发现它。

    ⚠️ 五条各走**不同**的 return —— 喂的行数必须刚好停在要测的那一步，多喂一步
    就会撞 ``IndexError`` 落进 except，于是五条测的其实是同一个分支。"""
    from app.services.ai.billing import tree_charge

    _patch_read_scope(monkeypatch, _read_scope_yielding(**scope_args))
    assert (
        await tree_charge.root_run_is_settled(
            run_id="900000000000001", parent_run_id=parent
        )
        is False
    ), case


async def test_a_run_without_an_id_is_never_settled():
    """``run_id`` 为空 = 这条 run 根本没落库，没有可查的树。连库都不碰。"""
    from app.services.ai.billing import tree_charge

    assert (
        await tree_charge.root_run_is_settled(run_id=None, parent_run_id="8") is False
    )


# ── parent_run_id 的真实来源 ────────────────────────────────────────────


def test_a_really_constructed_recorder_knows_whether_it_has_a_parent():
    """上面的桩用 ``__new__`` 绕过了 ``__init__``。这条真造两个 recorder：
    派发站点（``subagent_task_service`` / ``agent_worker``）传什么，扣费分支
    就读到什么。默认值必须是简单默认值，否则全仓 ``__new__`` 造的桩会整片
    ``AttributeError``（裁定 ⑦）。"""
    root = rr.RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    child = rr.RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="subagent",
        parent_run_id="800000000000001",
    )
    assert root.parent_run_id is None
    assert child.parent_run_id == "800000000000001"
    # 类属性存在 ⟹ ``__new__`` 造的桩不设也读得到。
    assert rr.RunRecorder.parent_run_id is None
