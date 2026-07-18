"""Structural test: every /projects/{project_id} route must declare a guard.

test_scope_guards.py tests the guard FUNCTIONS; this pins the WIRING —
a future endpoint added without Depends(verify_project_read|write_access)
fails here instead of shipping an IDOR (the 2026-07-04 audit found 37 of
42 endpoints unguarded)."""

from __future__ import annotations

import pytest

from app.api.projects_router import router
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)

pytestmark = pytest.mark.unit

# Routes with no {project_id} target: nothing to guard.
EXEMPT = {
    ("GET", "/projects"),
    ("POST", "/projects"),
    ("GET", "/projects/stages/catalog"),
    # PR-8 Task A: batch queue data source scoped by user_id (same
    # visible-projects call the GET /projects list route uses), not a
    # single project_id — no per-object guard applies.
    ("GET", "/projects/suggestions"),
    # Recent view: cross-project feed scoped by owner_id == user_id at the
    # repo join (same visibility as the GET /projects list), not a single
    # project_id — no per-object guard applies.
    ("GET", "/projects/recent-items"),
}


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


@pytest.mark.parametrize(
    "route",
    [r for r in router.routes if hasattr(r, "dependant")],
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_project_route_declares_guard(route):
    key = (next(iter(route.methods)), route.path)
    if key in EXEMPT:
        pytest.skip("no project_id target")
    calls = set(_flat_dependency_calls(route.dependant))
    assert (
        verify_project_read_access in calls or verify_project_write_access in calls
    ), f"{key} has no project access guard"


def _find_route(router_, path: str, method: str):
    """Locate a route by its path (relative to the router's prefix) + method."""
    full_path = f"{router_.prefix}{path}"
    for route in router_.routes:
        if not hasattr(route, "dependant"):
            continue
        if route.path == full_path and method in route.methods:
            return route
    return None


def _has_dependency(route, guard_name: str) -> bool:
    calls = _flat_dependency_calls(route.dependant)
    return any(getattr(call, "__name__", "") == guard_name for call in calls)


def _has_write_guard(route) -> bool:
    return _has_dependency(route, "verify_project_write_access")


def test_stage_suggestion_has_read_guard():
    # NB: `from app.api import projects_router` would resolve to the APIRouter
    # instance, not the module — app/api/__init__.py does
    # `from app.api.projects_router import router as projects_router`, which
    # shadows the submodule name in the `app.api` package namespace. Use the
    # `router` object already imported at module scope instead.
    route = _find_route(router, "/{project_id}/stage-suggestion", "GET")
    assert route is not None
    assert _has_dependency(route, "verify_project_read_access")


def test_generate_missing_has_write_guard():
    route = _find_route(router, "/{project_id}/storyboard/generate-missing", "POST")
    assert route is not None
    # reuse whatever write guard the other mutating project routes use
    assert _has_write_guard(route)
