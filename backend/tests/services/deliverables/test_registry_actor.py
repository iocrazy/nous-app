"""3b：登记口收人手作者。三条不变量：普通人手改动仍 no-op（3a 核心不变量，已由
test_registry.py 钉住）；人手版不落 transcript 事件（没有 run 可挂，硬挂到别的 run
上等于伪造一次运行）；reverted_from_version 只有和 actor 一起才成立——单独出现是
接线 bug，要在 CI 里炸而不是在生产里留一行说不清归属的记录。"""

import pytest

from app.services.deliverables.registry import register_deliverable

ME = "11111111-1111-1111-1111-111111111111"


async def test_an_actor_makes_a_runless_registration_land(repo_spy, emit_spy):
    repo_spy.latest_version_returns = 2
    out = await register_deliverable(
        run_id=None,
        kind="script_shot",
        ref_id="9",
        title="S1 · Shot 3",
        actor_user_id=ME,
        reverted_from_version=1,
        ledger_ref="9001",
    )
    assert (out.version, out.parent_version, out.run_id) == (3, 2, None)
    assert out.actor_user_id == ME and out.reverted_from_version == 1
    inserted = repo_spy.inserts[-1]
    assert inserted["run_id"] is None and inserted["ledger_ref"] == "9001"
    assert inserted["reverted_from_version"] == 1


async def test_a_human_version_lands_no_transcript_event(repo_spy, emit_spy):
    await register_deliverable(
        run_id=None, kind="script_scene", ref_id="7", actor_user_id=ME
    )
    assert emit_spy.events == [] and repo_spy.seq_calls == []


async def test_reverted_from_without_an_actor_is_a_typed_failure(repo_spy):
    with pytest.raises(ValueError, match="reverted_from_version"):
        await register_deliverable(
            run_id=777, kind="script_shot", ref_id="9", reverted_from_version=1
        )
    assert repo_spy.inserts == []


async def test_an_agent_registration_carries_its_ledger_ref(repo_spy, emit_spy):
    await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", ledger_ref="4242"
    )
    assert repo_spy.inserts[-1]["ledger_ref"] == "4242"
    # 账本位置是服务端定位字段，不进模型看得见的事件载荷。
    assert "ledger_ref" not in emit_spy.events[-1].payload


async def test_a_caller_session_is_handed_to_every_repo_call(repo_spy, emit_spy):
    sentinel = object()
    await register_deliverable(
        run_id=None,
        kind="script_shot",
        ref_id="9",
        actor_user_id=ME,
        session=sentinel,
    )
    assert repo_spy.sessions == [sentinel, sentinel]  # latest_version + insert


@pytest.mark.parametrize("run_id", ["r1", None, 777])
async def test_a_wrong_kind_is_a_typed_failure_on_every_path(repo_spy, run_id):
    """kind 写错在**每条**路上都是接线 bug，与这次登记占不占号无关。

    ``"r1"`` 是画布道真实传过的非 bigint run id。3b 合并 no-op 分支时它一度排在
    kind 校验之前，于是「run 传错 + kind 写错」从 ValueError 退化成 None + 一行
    WARNING —— 一个真缺陷被降级成日志。
    """
    with pytest.raises(ValueError, match="unknown deliverable kind"):
        await register_deliverable(run_id=run_id, kind="script_beat", ref_id="9")
    assert repo_spy.inserts == []


async def test_a_caller_session_reaches_the_seq_back_fill_too(repo_spy, emit_spy):
    """agent 那条路多一次写：事件落下之后把 seq 补回行上。它也必须待在调用方的
    事务里——三次写分属两个事务就等于「行记了、指针没记」可以各自成功。"""
    sentinel = object()
    await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", session=sentinel
    )
    assert repo_spy.seq_calls  # 事件确实落了，否则下面这条断言是空的
    assert repo_spy.set_seq_sessions == [sentinel]
    assert repo_spy.sessions == [sentinel, sentinel]  # latest_version + insert
