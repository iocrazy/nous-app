"""framework-hardening T5: both workforce execution paths stamp the run with
its task (``agent_runs.task_id``, mig 282). The stale-task reaper decides
"did this task already spend money?" from that link — a background sub-agent
never moves its row past ``assigned``, so the phase cannot answer it, and
without the link a crashed child would be REQUEUED and billed twice."""

from __future__ import annotations

import ast
import inspect

import pytest

from app.services.ai.runner import subagent_task_service as sts
from app.services.workforce import agent_worker as aw

pytestmark = pytest.mark.unit


def _recorder_calls(fn) -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "RunRecorder"
    ]


def _kw(call: ast.Call, name: str):
    return next((k for k in call.keywords if k.arg == name), None)


def test_delegate_path_recorder_carries_task_id():
    (call,) = _recorder_calls(aw.run_one_task)
    assert _kw(call, "task_id") is not None


def test_subagent_spawn_recorder_carries_the_workforce_task_id():
    (call,) = _recorder_calls(sts.SubAgentTaskService._spawn)
    kw = _kw(call, "task_id")
    assert kw is not None
    # From the keyword-only parameter — never from model-supplied tool args.
    assert "args" not in ast.unparse(kw.value)


def test_background_task_threads_task_id_into_spawn():
    sig = inspect.signature(sts.SubAgentTaskService.run_background_task)
    assert sig.parameters["task_id"].kind is inspect.Parameter.KEYWORD_ONLY
    sig = inspect.signature(sts.SubAgentTaskService._spawn)
    assert sig.parameters["task_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["task_id"].default is None


def test_worker_passes_its_task_id_to_the_background_child():
    src = inspect.getsource(aw._run_subagent_task)
    assert "run_background_task(payload, task_id=str(task_id))" in src
