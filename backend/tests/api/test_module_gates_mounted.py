"""Asserts the module gate is mounted on every gated router (spec §1 table).

Structural check: FastAPI keeps router-level dependencies in
``router.dependencies``; we match by the dependency's closure cell holding
the right ModuleDef. This fails when someone adds/reworks a router and
forgets the gate wiring.
"""

from __future__ import annotations

import pytest


def _ids_from_dependencies(dependencies) -> set[str]:
    ids: set[str] = set()
    for dep in dependencies:
        fn = dep.dependency
        for cell in getattr(fn, "__closure__", None) or ():
            obj = cell.cell_contents
            if hasattr(obj, "id") and hasattr(obj, "enabled_default"):
                ids.add(obj.id)
    return ids


def _gated_module_ids(router) -> set[str]:
    return _ids_from_dependencies(router.dependencies)


def _route_gated_module_ids(router, path: str, method: str) -> set[str]:
    """Same closure match, but for a single route's own ``dependencies``.

    Used where the gate must NOT sit at router level — those files also host
    query/progress endpoints that stay reachable while the module is off.
    """
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return _ids_from_dependencies(route.dependencies)
    raise AssertionError(f"route {method} {path} not found on router")


@pytest.mark.parametrize(
    "module_path,router_name,expected_id",
    [
        ("app.api.media_fetch_router", "router", "media-parser"),
        ("app.api.media_batch_router", "router", "media-parser"),
        ("app.api.projects_router", "router", "projects"),
        ("app.api.project_assets_router", "router", "projects"),
        ("app.api.canvases_router", "router", "projects"),
        ("app.api.shares_router", "router", "shares"),
        ("app.api.issues_router", "router", "todolist"),
        ("app.api.conversation_router", "router", "ai-library"),
        ("app.api.ai_library_router", "router", "ai-library"),
        ("app.api.ideation_router", "router", "ideation"),
    ],
)
def test_router_has_module_gate(module_path, router_name, expected_id):
    import importlib

    router = getattr(importlib.import_module(module_path), router_name)
    assert expected_id in _gated_module_ids(router), (
        f"{module_path}.{router_name} is missing require_module('{expected_id}')"
    )


@pytest.mark.parametrize(
    "module_path,path,method",
    [
        # Manual "retry this download" — a new download dispatch.
        ("app.api.media_download_router", "/retry/{platform_id}", "POST"),
        # Batch playlist download — dispatches one parse_workflow per track.
        ("app.api.media_soda_router", "/soda/playlist/download", "POST"),
    ],
)
def test_dispatch_endpoint_has_module_gate(module_path, path, method):
    """These two live in files whose other endpoints are read-only, so the gate
    is endpoint-level rather than router-level."""
    import importlib

    router = importlib.import_module(module_path).router
    assert "media-parser" in _route_gated_module_ids(router, path, method), (
        f"{module_path} {method} {path} is missing require_module('media-parser')"
    )


# The retry tick's gate cannot be exercised through the workflow itself: the
# DBOS decorators raise "invoked before DBOS initialized" and ``__wrapped__``
# only unwraps to the next decorator layer, not to the bare coroutine. So the
# switch lives in the module-level ``_media_parser_enabled`` helper, tested
# directly here, plus a source-level assertion that the workflow body still
# consults it (brief Step 4 fallback).


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"enabled": False, "visible": False}, False),
        ({"enabled": True, "visible": True}, True),
        # Fail-open: unreadable config keeps downloads retrying.
        (None, True),
    ],
)
async def test_media_parser_enabled_reads_module_switch(raw, expected):
    from unittest.mock import AsyncMock, patch

    from app.workflows import scheduled_recovery as sr

    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value=raw),
    ):
        assert await sr._media_parser_enabled() is expected


def test_retry_workflow_body_consults_media_parser_switch():
    import inspect

    from app.workflows import scheduled_recovery as sr

    src = inspect.getsource(sr)
    body = src.split("async def retry_failed_downloads_workflow(", 1)[1]
    body = body.split("\n@DBOS.scheduled", 1)[0]
    guard_at = body.find("_media_parser_enabled()")
    collect_at = body.find("collect_retryable_downloads_step()")
    assert guard_at != -1, "retry_failed_downloads_workflow lost its module gate"
    assert guard_at < collect_at, "module gate must run before any DB work"
