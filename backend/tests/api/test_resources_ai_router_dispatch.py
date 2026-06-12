# backend/tests/api/test_resources_ai_router_dispatch.py

"""Regression tests for the resources AI dispatch endpoints.

Pinned contract (reference_dbos_dispatch_endpoint_wf_id): a DBOS-dispatch
endpoint must pre-create its task_tracking row with
``dbos_workflow_id=wf_id`` (the NOT NULL PK — create() without it fails
23502 and is swallowed, so Task Center silently shows nothing) AND pass
the SAME ``workflow_id=wf_id`` to ``start_workflow_routed`` (else the
lifecycle trigger can't associate them and the task never completes).

Since P2-1 the contract lives in the shared ``_dispatch_asset_ai`` helper;
the endpoint tests pin that the endpoints actually route through it.
"""

from __future__ import annotations

import importlib
import inspect


def _source(symbol: str) -> str:
    mod = importlib.import_module("app.api.resources_ai_router")
    return inspect.getsource(getattr(mod, symbol))


def test_dispatch_helper_creates_row_with_workflow_id() -> None:
    source = _source("_dispatch_asset_ai")
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


def test_single_endpoints_route_through_dispatch_helper() -> None:
    assert "_single_asset_ai" in _source("generate_gen_prompt")
    assert "_single_asset_ai" in _source("classify_resource")
    assert "_dispatch_asset_ai" in _source("_single_asset_ai")


def test_operation_registry_maps_both_workflows() -> None:
    source = _source("_resolve_ai_operation")
    assert "caption_asset_workflow" in source
    assert "classify_asset_workflow" in source
    assert "prompt_caption" in source and "asset_classify" in source


def test_batch_endpoint_dispatches_per_resource_with_skip_reasons() -> None:
    source = _source("batch_asset_ai")
    assert (
        "_dispatch_asset_ai" in source
    ), "batch must reuse the wf_id-pinned dispatch helper per resource"
    assert (
        "check_media_access" in source
    ), "batch must access-check every resource individually"
    assert "skipped" in source and "dispatched" in source
