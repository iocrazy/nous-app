"""No two routes may register the same (method, path) pair.

FastAPI resolves collisions by registration order: whichever router was
included first silently wins and the later route becomes unreachable —
no error, no log line. That is exactly how the workflow template list
(`GET /api/v1/workflows` on workflow_templates_router) was shadowed by
the DBOS runs list (bare ``@router.get("")`` on workflows_router) in
prod: templates never listed, seeding never ran, and the frontend's
``response.data ?? []`` fallback hid the wrong-shape payload entirely
(2026-07-20 walkthrough).

This guard sweeps every route on the aggregated ``api_router`` and fails
on any duplicate, so the next collision dies in CI instead of in prod.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute


def _collect_route_keys(app: FastAPI) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            if method == "HEAD":
                continue
            keys.append((method, route.path))
    return keys


@pytest.mark.unit
def test_no_duplicate_method_path_pairs() -> None:
    from app.api import api_router

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    keys = _collect_route_keys(app)
    seen: dict[tuple[str, str], int] = {}
    for key in keys:
        seen[key] = seen.get(key, 0) + 1
    duplicates = sorted(key for key, count in seen.items() if count > 1)

    assert not duplicates, (
        "Duplicate (method, path) registrations — the later route is "
        f"silently unreachable: {duplicates}"
    )
