"""A6：派发链的 run 必须带上 team_id / project_id。

NULL 的行落在 idx_agent_runs_billing（WHERE team_id IS NOT NULL）之外 ——
对任何按团队的效率账，它们等于不存在。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

BACKEND = pathlib.Path(__file__).resolve().parents[1]
CHAT = BACKEND / "app/services/ai/chat/ai_library_chat_service.py"
CONV = BACKEND / "app/services/chat/conversation_agent_turn.py"


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


def test_the_chat_dispatch_site_takes_both_columns_from_the_fallback():
    kwargs = _recorder_kwargs(CHAT)
    assert kwargs["team_id"] == "_team_id"
    assert kwargs["project_id"] == "_project_id"
    assert kwargs["episode_id"] == "_dispatch_scope.episode_id"


def test_the_chat_fallback_reads_both_columns_off_the_issue_row():
    src = CHAT.read_text(encoding="utf-8")
    assert "_issue_row = await get_issue_repository().get_by_id(int(issue_id))" in src
    assert (
        '_team_id = session.get("team_id") or (_issue_row or {}).get("team_id")' in src
    )
    assert (
        "_project_id = _dispatch_scope.project_id or "
        '(_issue_row or {}).get("project_id")'
    ) in src


def test_the_conversation_summon_site_still_binds_the_team():
    """这一侧没有议题（模块里零个 issue 引用），team_id 来自 conversation 的
    scope_id，而 Gate 0 已保证它非空。这条守的是「未来有人把它拿掉」—— 不是
    兜底，是回归钉子。"""
    kwargs = _recorder_kwargs(CONV)
    assert kwargs["team_id"] == "int(scope_id) if scope_id is not None else None"
    assert (
        "issue" not in CONV.read_text(encoding="utf-8").lower()
    ), "这个模块一旦引入议题概念，本 Task 的裁定（那一侧不加兜底）要重做"


def test_the_scope_columns_are_still_insert_only():
    """兜底在构造时（INSERT），不是 UPDATE —— agent_run_scope.py 的不可变承诺
    不受影响。直接跑那条既有守卫，而不是复述它。"""
    from tests.test_agent_run_scope import (
        test_no_write_path_anywhere_touches_scope_columns,
    )

    test_no_write_path_anywhere_touches_scope_columns()
