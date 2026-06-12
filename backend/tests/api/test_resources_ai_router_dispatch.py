# backend/tests/api/test_resources_ai_router_dispatch.py

"""Regression tests for POST /resources/{id}/gen-prompt/generate dispatch.

Pinned contract (reference_dbos_dispatch_endpoint_wf_id): a DBOS-dispatch
endpoint must pre-create its task_tracking row with
``dbos_workflow_id=wf_id`` (the NOT NULL PK — create() without it fails
23502 and is swallowed, so Task Center silently shows nothing) AND pass
the SAME ``workflow_id=wf_id`` to ``start_workflow_routed`` (else the
lifecycle trigger can't associate them and the task never completes).
"""

from __future__ import annotations

import importlib
import inspect


def _endpoint_source() -> str:
    mod = importlib.import_module("app.api.resources_ai_router")
    return inspect.getsource(mod.generate_gen_prompt)


def test_generate_gen_prompt_creates_row_with_workflow_id() -> None:
    source = _endpoint_source()
    assert "dbos_workflow_id=wf_id" in source, (
        "task_tracking.dbos_workflow_id is the NOT NULL PK — create() "
        "without it fails 23502 and the task silently never appears in "
        "Task Center."
    )
    assert "workflow_id=wf_id" in source, (
        "the dispatched workflow_id must match the pre-created row's "
        "dbos_workflow_id, or the lifecycle trigger can't associate them "
        "and the task never completes."
    )


def test_generate_gen_prompt_dispatches_caption_workflow() -> None:
    source = _endpoint_source()
    assert (
        "caption_asset_workflow" in source and "start_workflow_routed" in source
    ), "the endpoint must dispatch caption_asset_workflow via the router"


def _classify_endpoint_source() -> str:
    mod = importlib.import_module("app.api.resources_ai_router")
    return inspect.getsource(mod.classify_resource)


def test_classify_creates_row_with_workflow_id() -> None:
    source = _classify_endpoint_source()
    assert "dbos_workflow_id=wf_id" in source
    assert "workflow_id=wf_id" in source


def test_classify_dispatches_classify_workflow() -> None:
    source = _classify_endpoint_source()
    assert "classify_asset_workflow" in source and "start_workflow_routed" in source
