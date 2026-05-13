"""Regression test for PR #254 cleanup follow-through.

PR #254 dropped the `url` parameter from `download_workflow`, but two
dispatcher sites kept passing `"url": ...` in their kwargs dict:

  * backend/app/api/media_fetch_helpers.py — main parse → download path
  * backend/app/api/task_manager_router.py — Task Center retry button

Every download attempt from those entries TypeError'd inside the DBOS
runner before the workflow body ran. Symptoms on prod (2026-05-13):

  - task_tracking row created with phase=queued, never advanced
  - Front-end progress KV stuck on the last value (the "stuck at 58%"
    bug) because no one updated progress on a workflow that never
    actually started
  - dbos.workflow_status.error = pickled TypeError

These static checks pin the dispatch kwargs against the workflow
signature so the two never drift again. If someone re-adds `url` to a
dispatcher without re-adding the parameter to download_workflow, the
test fails immediately.
"""

from __future__ import annotations

import importlib
import inspect


def _signature_accepts(fn, kwarg: str) -> bool:
    """True iff `fn` accepts `kwarg` either as a named parameter or
    via a **kwargs catch-all."""
    sig = inspect.signature(fn)
    if kwarg in sig.parameters:
        return True
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


def _kwarg_keys_passed_to_download_workflow(source: str) -> set[str]:
    """Pull the literal kwarg names from any
    `dbos_workflow_kwargs={...}` block in source that's paired with
    `dbos_workflow_callable=download_workflow`. Cheap regex on the
    decompiled source string — good enough for a guard rail."""
    import re

    keys: set[str] = set()
    # Find each callable=download_workflow occurrence then walk forward
    # to its kwargs dict.
    for m in re.finditer(
        r"dbos_workflow_callable=download_workflow.*?dbos_workflow_kwargs=\{(.*?)\}",
        source,
        re.DOTALL,
    ):
        block = m.group(1)
        for kv in re.finditer(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:', block):
            keys.add(kv.group(1))
    return keys


def test_media_fetch_helpers_dispatcher_kwargs_match_download_workflow() -> None:
    """Every kwarg media_fetch_helpers passes to download_workflow
    must exist on download_workflow's signature."""
    workflow_mod = importlib.import_module("app.workflows.download")
    helpers_mod = importlib.import_module("app.api.media_fetch_helpers")

    src = inspect.getsource(helpers_mod)
    passed_kwargs = _kwarg_keys_passed_to_download_workflow(src)
    assert passed_kwargs, (
        "Couldn't find a dbos_workflow_kwargs block paired with "
        "download_workflow in media_fetch_helpers — test pattern is stale"
    )

    accepted = inspect.signature(workflow_mod.download_workflow).parameters
    accepts_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in accepted.values()
    )

    unknown = {k for k in passed_kwargs if k not in accepted}
    if accepts_var_kw:
        unknown = set()  # **kwargs absorbs everything

    assert not unknown, (
        f"media_fetch_helpers passes kwargs {unknown!r} that "
        f"download_workflow doesn't accept. download_workflow params: "
        f"{sorted(accepted)!r}. This is the PR #254 drift pattern — "
        f"either re-add the params to download_workflow or drop them "
        f"from the dispatcher."
    )


def test_task_manager_router_dispatcher_kwargs_match_download_workflow() -> None:
    """Same guard for the Task Center retry path."""
    workflow_mod = importlib.import_module("app.workflows.download")
    router_mod = importlib.import_module("app.api.task_manager_router")

    src = inspect.getsource(router_mod)
    passed_kwargs = _kwarg_keys_passed_to_download_workflow(src)
    assert passed_kwargs, (
        "Couldn't find a dbos_workflow_kwargs block paired with "
        "download_workflow in task_manager_router — test pattern is stale"
    )

    accepted = inspect.signature(workflow_mod.download_workflow).parameters
    accepts_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in accepted.values()
    )

    unknown = {k for k in passed_kwargs if k not in accepted}
    if accepts_var_kw:
        unknown = set()

    assert not unknown, (
        f"task_manager_router passes kwargs {unknown!r} that "
        f"download_workflow doesn't accept. download_workflow params: "
        f"{sorted(accepted)!r}. This is the PR #254 drift pattern."
    )


def test_download_workflow_does_not_take_url_kwarg() -> None:
    """Pins the post-PR-#254 contract: `url` is gone for good. If
    someone wants to re-add it, this test should be deleted in the same
    commit so the intent is explicit."""
    workflow_mod = importlib.import_module("app.workflows.download")
    sig = inspect.signature(workflow_mod.download_workflow)
    assert "url" not in sig.parameters, (
        "download_workflow re-introduced a `url` parameter. If this is "
        "intentional, also delete this regression test in the same commit "
        "and update the dispatcher to pass `url=`. PR #254's intent was "
        "to remove url plumbing entirely."
    )
