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
