"""`manager.start()` must always carry user_id — bare calls break self-heal.

`UnifiedTaskManager.start()` self-heals a missing task_tracking row, and its
docstring is explicit:

    ``user_id`` MUST be threaded from the call site for the self-heal to
    succeed — ``task_tracking.user_id`` is ``UUID NOT NULL`` (mig 064), so a
    blank/None user_id makes the recovery INSERT fail.

It falls back to `user_id or ""`, and an empty string is not a UUID, so a bare
`start(workflow_id)` guarantees the recovery INSERT dies with:

    invalid input for query argument $1: '' (invalid UUID '': length must be
    between 32..36 characters, got 0)

Observed 2026-07-26: a retried download had its pre-create rejected (Snowflake
int into a text column — see test_task_tracking_id_coercion), then the
self-heal ALSO failed for this reason. Both layers logged a warning and
continued, so the download ran to completion while the Task Center stayed
empty. The pre-create fix removes the usual trigger, but this is the fallback
that is supposed to catch the unusual ones, so it has to actually work.

All three call sites had the user_id in scope one frame up — the workflow
signatures take it as a positional argument — it simply was not passed down.
"""

from __future__ import annotations

import importlib
import inspect
import re

import pytest

_WORKFLOWS = [
    "app.workflows.download",
    "app.workflows.parse",
    "app.workflows.publish_distribution",
]

# `.start(workflow_id)` / `.start(DBOS.workflow_id)` with no further arguments.
_BARE_START = re.compile(r"\.start\(\s*(?:DBOS\.)?workflow_id\s*\)")


@pytest.mark.parametrize("dotted", _WORKFLOWS)
def test_no_bare_manager_start(dotted: str) -> None:
    source = inspect.getsource(importlib.import_module(dotted))
    assert not _BARE_START.search(source), (
        f"{dotted} calls manager.start(workflow_id) without user_id. "
        "task_tracking.user_id is UUID NOT NULL, so the self-heal INSERT "
        "fails on '' and a task whose pre-create was swallowed never reaches "
        "the Task Center."
    )


@pytest.mark.parametrize("dotted", _WORKFLOWS)
def test_processing_step_accepts_user_id(dotted: str) -> None:
    """The mark_*_processing_step wrappers must expose a user_id parameter."""
    module = importlib.import_module(dotted)
    steps = [
        obj
        for name, obj in vars(module).items()
        if name.startswith("mark_") and name.endswith("_processing_step")
    ]
    assert steps, f"{dotted} has no mark_*_processing_step to check"

    for step in steps:
        target = inspect.unwrap(step)
        params = inspect.signature(target).parameters
        assert "user_id" in params, (
            f"{target.__name__} in {dotted} takes no user_id, so it cannot "
            "thread it into manager.start()."
        )
