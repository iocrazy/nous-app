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


def test_slide_endpoint_pins_the_same_wf_id_contract() -> None:
    source = _source("generate_slide_prompt")
    assert "dbos_workflow_id=wf_id" in source and "workflow_id=wf_id" in source, (
        "the per-slide endpoint dispatches its own workflow (not via "
        "_dispatch_asset_ai, which has no slide_name), so it has to honour "
        "the wf_id contract itself."
    )
    assert "caption_slide_workflow" in source


def test_slide_endpoint_gates_before_dispatching() -> None:
    source = _source("generate_slide_prompt")
    assert "check_media_access" in source
    # A bad slide name must 4xx at the endpoint, not become a failed Task
    # Center row minutes later. resolve_slide_SOURCE, not resolve_slide_file:
    # a migrated album has nothing on disk, so the filesystem-only resolver
    # would 404 every slide of it.
    assert "resolve_slide_source" in source
    assert "is_image_slide" in source
    # Albums are the ONLY resources with per-slide prompts, and media_id is
    # what identifies one (file_type holds raw platform codes for downloads,
    # so it can't be the discriminator).
    assert "if not media_id:" in source
    assert "status_code=422" in source


def test_training_set_export_guards_every_resource() -> None:
    source = _source("export_training_set")
    assert "check_media_access" in source, (
        "export must access-check every resource individually — a zip of "
        "someone else's files would be a data leak"
    )
    assert "_image_gate_reason" in source
    # LoRA convention: caption .txt shares the image's arcname stem.
    assert ".txt" in source and "arcname" in source


def test_translate_endpoint_delegates_to_the_shared_ops_module() -> None:
    """P2-3 moved ``build_translate_plan`` / the TranslateService loop into
    ``app/services/library/resource_ai_ops.py`` so the asset library can drive
    the SAME translation agent without importing this router. The move is only
    worth anything while this endpoint keeps calling the shared copy — a
    re-inlined loop here would resolve providers its own way and drift.
    """
    import app.services.library.resource_ai_ops as ops
    from app.api import resources_ai_router as mod

    assert mod.build_translate_plan is ops.build_translate_plan
    assert mod.translate_fields is ops.translate_fields

    source = _source("translate_gen_prompt")
    assert "translate_fields(" in source
    assert (
        "TranslateService(" not in source
    ), "the provider wiring belongs to resource_ai_ops now, not to this router"
    assert "AllModelsFailed" in source and "LLMCallError" in source, (
        "the ops layer propagates provider failures RAW; this endpoint still "
        "has to let them past its catch-all to the typed provider surface."
    )
