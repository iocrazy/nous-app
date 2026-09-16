"""A6：派发链的 run 必须带上 team_id / project_id。

NULL 的行落在 idx_agent_runs_billing（WHERE team_id IS NOT NULL）之外 ——
对任何按团队的效率账，它们等于不存在。

兜底的两条都是**行为断言**：值被跟到真正消费它的对象（RunRecorder 的构造
关键字）。源码文本断言写过一版、评审时拆掉了 —— 把转发行注释掉它们照样
绿，而任何 black 重排都会在代码正确时把它们弄红（同
tests/services/ai/test_run_session_turn_issue_id.py 顶部的裁定）。
"""

from __future__ import annotations

import ast
import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.chat import ai_library_chat_service as svc_mod
from tests.test_parity_gap_coverage import (
    _chat_env,
    _FakeStore,
    _RunRecorderCM,
    _session_row,
)

pytestmark = pytest.mark.unit

BACKEND = pathlib.Path(__file__).resolve().parents[1]
CHAT = BACKEND / "app/services/ai/chat/ai_library_chat_service.py"
CONV = BACKEND / "app/services/chat/conversation_agent_turn.py"
# 3c A3 修复轮 1：两条派发链此前硬编码 team_id=None，落在
# idx_agent_runs_billing(WHERE team_id IS NOT NULL) 之外 —— 正是本文件顶部
# 那条不变量要防的退化，而守卫当时只扫上面两个文件，一次都没拦到。
SUB = BACKEND / "app/services/ai/runner/subagent_task_service.py"
WORKER = BACKEND / "app/services/workforce/agent_worker.py"


def _recorder_kwargs(path: pathlib.Path) -> dict[str, str]:
    """取出文件里唯一一处 RunRecorder(...) 的关键字（源码文本形式）。比 grep 稳：
    参数换行、夹注释都不影响。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "RunRecorder"
    ]
    assert len(calls) == 1, f"{path.name} 里 RunRecorder 调用点不是一个"
    return {kw.arg: ast.unparse(kw.value) for kw in calls[0].keywords if kw.arg}


async def _turn(
    *,
    issue_repo: MagicMock,
    session_overrides: dict | None = None,
    **turn_kwargs,
) -> dict:
    """跑一次真的 run_session_turn（LLM 栈已桩），返回 RunRecorder 收到的关键字。

    形状照抄 tests/services/ai/test_run_session_turn_issue_id.py 的 _turn()，
    只多两件事：可以覆盖 session 行的字段、可以桩掉议题仓库。
    ⚠️ get_issue_repository 是在函数体内**惰性 import** 的，所以必须在它的
    源模块上 patch，不能挂到 service 的命名空间上。"""
    user_id, agent_id = uuid4(), uuid4()
    row = _session_row(user_id, agent_id)
    row.update(session_overrides or {})
    store = _FakeStore(row)
    seen: dict = {}
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as env_recorder:

        def _rr(**kw):
            seen.update(kw)
            return _RunRecorderCM(env_recorder)

        with (
            patch.object(svc_mod, "RunRecorder", side_effect=_rr),
            patch(
                "app.repositories.issue_repository.get_issue_repository",
                return_value=issue_repo,
            ),
        ):
            await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(),
                user_id=user_id,
                content="go",
                trigger="issue_dispatch",
                **turn_kwargs,
            )
    return seen


async def test_a_team_less_session_takes_both_columns_off_the_issue():
    """派发链的真缺陷形状：session 没有 team_id，议题有。两个值都必须落到
    RunRecorder 的构造关键字上 —— 这是 agent_runs 那两列的唯一写入时机。"""
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value={"team_id": 777, "project_id": 888})

    seen = await _turn(issue_repo=repo, issue_id=9)

    assert seen["team_id"] == 777
    assert seen["project_id"] == 888
    repo.get_by_id.assert_awaited_once_with(9)


async def test_a_session_that_already_has_a_team_does_not_query_the_issue():
    """兜底只在真缺时付那次往返的代价。session 自带 team_id 时议题一次都不查，
    而那个值原样进 RunRecorder。"""
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value={"team_id": 777, "project_id": 888})

    seen = await _turn(issue_repo=repo, session_overrides={"team_id": 555}, issue_id=9)

    assert seen["team_id"] == 555
    repo.get_by_id.assert_not_awaited()


def test_the_chat_dispatch_site_takes_both_columns_from_the_fallback():
    """splat 必须保持拆开：显式 project_id 与 **as_recorder_kwargs() 并存是
    重复关键字、调用时 TypeError（同 CLAUDE.md safe_popen_kwargs 那条）。"""
    kwargs = _recorder_kwargs(CHAT)
    assert kwargs["team_id"] == "_team_id"
    assert kwargs["project_id"] == "_project_id"
    assert kwargs["episode_id"] == "_dispatch_scope.episode_id"


def test_the_conversation_summon_site_still_binds_the_team():
    """这一侧的 team_id 来自 conversation 的 scope_id，而 Gate 0 已保证它非空。
    这条守的是「未来有人把它拿掉」—— 不是兜底，是回归钉子。"""
    kwargs = _recorder_kwargs(CONV)
    assert kwargs["team_id"] == "int(scope_id) if scope_id is not None else None"


def test_the_conversation_module_still_has_no_notion_of_an_issue():
    """本 Task 裁定「那一侧不加兜底」的**前提**：该模块没有议题概念，所以
    兜底会是永不执行的死分支。词边界匹配 —— get_issue_repository 里的 issue
    前面是下划线（词内），不该算数；真引入 `issue` / `issues` / `issue_id`
    才算。前提一旦不成立，裁定要重做。"""
    src = CONV.read_text(encoding="utf-8")
    assert not re.search(r"\bissue", src, re.IGNORECASE)


def test_no_dispatch_site_hardcodes_a_null_team():
    """字面 None 是这条不变量唯一栽过的形状，所以钉的就是「不是 None」——
    不钉具体变量名：那样改个名字就假阳性，而重命名从来不是这条不变量的
    失效方式。值本身走哪条路（父 recorder 快路径 / team_of_run 读库）由
    tests/test_child_run_inherits_team.py 的行为断言负责。

    四个站点一起扫。新加派发站点时这条会漏 —— 它守的是既有四处不退化，
    不是「所有站点都对」，后者只有 grep 全仓 RunRecorder 才做得到。"""
    for path in (CHAT, CONV, SUB, WORKER):
        assert _recorder_kwargs(path)["team_id"] != "None", path.name


def test_the_scope_columns_are_still_insert_only():
    """兜底在构造时（INSERT），不是 UPDATE —— agent_run_scope.py 的不可变承诺
    不受影响。直接跑那条既有守卫，而不是复述它。"""
    from tests.test_agent_run_scope import (
        test_no_write_path_anywhere_touches_scope_columns,
    )

    test_no_write_path_anywhere_touches_scope_columns()
