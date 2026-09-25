"""No route writes the lifecycle columns of ``task_tracking``.

``phase / status / progress / started_at / completed_at / error_msg`` belong
to the ``mirror_dbos_lifecycle_to_tracking`` trigger (CLAUDE.md, 任务系统架构纪律
§2). A route that PATCHes them reports a cancel that never reached the
workflow: it keeps running, and the trigger (or the worker) writes the row
back. ``tests/api/admin/test_admin_tasks_no_status_writes.py`` pins the admin
surface; this scan covers every module under ``app/api``.

What counts as a write:

* an ORM ``update / insert`` of ``TaskTracking`` whose ``.values(...)`` names a
  lifecycle column — decoration columns (``error_code``, ``metadata``,
  ``max_duration_minutes`` …) are business-owned and allowed;
* ``.values(**something)`` on such a statement — the scan cannot see the
  keys, so the call site must be reviewed and listed in ``REVIEWED``;
* a call to a repository method that writes lifecycle columns
  (``LIFECYCLE_WRITERS``), also only through ``REVIEWED``.

``REVIEWED`` entries must still hit, so a stale allowance fails loudly rather
than quietly covering a future write.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2]
SCANNED = sorted((BACKEND / "app/api").rglob("*.py"))

LIFECYCLE = {"phase", "status", "progress", "started_at", "completed_at", "error_msg"}
WRITERS = {"update", "insert", "sa_update", "sa_insert", "pg_insert"}
# Repository methods that write the lifecycle columns of task_tracking rows.
LIFECYCLE_WRITERS = {"update_task_status", "requeue_task", "claim_task"}

REVIEWED: dict[tuple[str, str], str] = {
    (
        "app/api/task_manager_router.py",
        "patch_health_override",
    ): "``.values(**fields)`` where fields is HealthOverridePayload: only "
    "max_duration_minutes / expected_duration_minutes / do_not_auto_cancel",
    (
        "app/api/workforce_router.py",
        "cancel_task",
    ): "agent_task rows are not DBOS-mirrored (dbos_workflow_id is the task id, "
    "the workflow is workforce-<task>-<attempt>); the workforce repository owns "
    "their lifecycle, and this is its compare-and-set queued → cancelled",
}


def _functions(tree: ast.AST) -> Iterator[tuple[str, ast.AST]]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
    yield "<module>", tree


def _name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    return getattr(func, "attr", "")


def _targets_task_tracking(expr: ast.AST) -> bool:
    """Walk ``x(...).where(...).values`` down to its root call."""
    while isinstance(expr, (ast.Attribute, ast.Call)):
        if isinstance(expr, ast.Call):
            if (
                _name(expr.func) in WRITERS
                and expr.args
                and isinstance(expr.args[0], ast.Name)
                and expr.args[0].id == "TaskTracking"
            ):
                return True
            expr = expr.func
        else:
            expr = expr.value
    return False


def _hits(source: str) -> list[tuple[str, int, str]]:
    """``(function, line, why)`` for every lifecycle write in ``source``."""
    tree = ast.parse(source)
    found: dict[tuple[str, int], str] = {}
    for fn, scope in _functions(tree):
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            name = _name(node.func)
            if name in LIFECYCLE_WRITERS:
                found.setdefault((fn, node.lineno), f"calls {name}")
            if name != "values" or not isinstance(node.func, ast.Attribute):
                continue
            if not _targets_task_tracking(node.func.value):
                continue
            keys = {kw.arg for kw in node.keywords}
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys |= {k.value for k in arg.keys if isinstance(k, ast.Constant)}
                    if any(k is None for k in arg.keys):
                        keys.add(None)
                else:
                    keys.add(None)
            if None in keys:
                found.setdefault((fn, node.lineno), "opaque .values(**...)")
            elif keys & LIFECYCLE:
                found.setdefault(
                    (fn, node.lineno), f"writes {sorted(keys & LIFECYCLE)}"
                )
    # The innermost function wins: ast.walk visits a nested def from its
    # parent too, so keep one entry per line, named by the deepest scope.
    by_line: dict[int, tuple[str, str]] = {}
    for (fn, line), why in found.items():
        if line not in by_line or fn != "<module>":
            by_line[line] = (fn, why)
    return sorted((fn, line, why) for line, (fn, why) in by_line.items())


def _scan() -> list[tuple[str, str, int, str]]:
    return [
        (str(p.relative_to(BACKEND)), fn, line, why)
        for p in SCANNED
        for fn, line, why in _hits(p.read_text())
    ]


def test_the_scan_covers_every_router() -> None:
    names = {p.name for p in SCANNED}
    assert {
        "flows_router.py",
        "workforce_router.py",
        "task_manager_router.py",
        "tasks_router.py",
    } <= names


def test_no_route_writes_task_tracking_lifecycle() -> None:
    violations = [
        f"{path}:{line} {fn}: {why}"
        for path, fn, line, why in _scan()
        if (path, fn) not in REVIEWED
    ]
    assert violations == [], violations


def test_every_reviewed_allowance_still_hits() -> None:
    hit = {(path, fn) for path, fn, _, _ in _scan()}
    assert set(REVIEWED) <= hit, set(REVIEWED) - hit


@pytest.mark.parametrize(
    "source",
    [
        "update(TaskTracking).values(phase='cancelled')",
        "sa_update(TaskTracking).where(x).values(status='pending')",
        "sa_update(TaskTracking).where(a).where(b).values(error_msg='x', "
        "error_code='y')",
        "update(TaskTracking).values({'progress': 5})",
        "update(TaskTracking).values(**fields)",
        "repo.update_task_status(task_id=t, lifecycle_status='cancelled')",
    ],
)
def test_the_scan_sees_a_write(source) -> None:
    assert len(_hits(source)) == 1, _hits(source)


@pytest.mark.parametrize(
    "source",
    [
        "sa_update(TaskTracking).values(error_code='flow_cascade_cancel')",
        "update(TaskTracking).values(max_duration_minutes=5)",
        "sa_update(TaskFlows).values(state='cancelled')",
        "select(TaskTracking.phase).where(TaskTracking.phase == 'queued')",
    ],
)
def test_the_scan_lets_decoration_and_other_tables_through(source) -> None:
    assert _hits(source) == []
