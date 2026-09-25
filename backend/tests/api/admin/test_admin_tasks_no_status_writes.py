"""Nothing in the admin API writes ``task_tracking``.

``phase / status / progress / started_at / completed_at / error_msg`` are owned
by the ``mirror_dbos_lifecycle_to_tracking`` trigger (CLAUDE.md, 任务系统架构纪律
§2). The admin Task Center used to PATCH them directly: cancel wrote
``cancelled`` without cancelling the workflow, retry wrote ``pending/queued``
without dispatching one. Both paths are gone; this scan keeps a new write from
creeping back into the admin routers or their repositories.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.repositories.admin.tasks_repository import AdminTasksRepository

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[3]
SCANNED = [
    *sorted((BACKEND / "app/api/admin").glob("*.py")),
    *sorted((BACKEND / "app/repositories/admin").glob("*.py")),
]
WRITERS = {"update", "insert", "delete", "sa_update", "sa_insert", "sa_delete"}


def _writes_to_task_tracking(source: str) -> list[int]:
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        target = node.args[0]
        if (
            name in WRITERS
            and isinstance(target, ast.Name)
            and target.id == ("TaskTracking")
        ):
            hits.append(node.lineno)
    return hits


def test_scan_covers_the_admin_surface() -> None:
    names = {p.name for p in SCANNED}
    assert {"tasks_router.py", "tasks_repository.py"} <= names


def test_no_admin_module_writes_task_tracking() -> None:
    hits = [
        f"{p.relative_to(BACKEND)}:{line}"
        for p in SCANNED
        for line in _writes_to_task_tracking(p.read_text())
    ]
    assert hits == [], hits


@pytest.mark.parametrize(
    "source",
    [
        "update(TaskTracking).values(phase='cancelled')",
        "sa_update(TaskTracking).where(x).values(status='pending')",
        "session.execute(insert(TaskTracking).values(**row))",
    ],
)
def test_the_scan_sees_a_write(source) -> None:
    assert _writes_to_task_tracking(source) == [1]


def test_admin_tasks_repository_has_no_write_method() -> None:
    assert not hasattr(AdminTasksRepository, "update")
