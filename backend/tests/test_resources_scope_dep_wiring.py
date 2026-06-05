"""Pin the ``ScopedRequestDep`` WIRING on the resources HTTP routers (A2 pass 1).

A2 pass 1 added the ambient-tenant-``Scope`` dependency (``scoped_request`` /
``ScopedRequestDep``) to every router path that (directly or via a service)
touches the ``resources`` repo, so that flipping ``SCOPE_ENFORCE_RESOURCES`` later
needs no router edits. It is INERT today — with the flag off the choke point
ignores ``_scope`` for ``resources`` — so a plain response-shape test cannot tell
"wired" from "not wired". These tests assert the AMBIENT SCOPE IS ESTABLISHED
during the request instead: they patch a repo method the endpoint calls and read
``current_scope()`` from inside it.

A1 (``tests/db/test_resources_scope_activation.py::
test_scoped_request_dependency_sets_and_resets_scope``) already proved the
``scoped_request`` generator sets/resets the ContextVar end-to-end. This file
proves a REAL wired router endpoint, run through the full FastAPI request
pipeline (dependency resolution included), establishes that scope — and that a
representative set of wired endpoints carry the dependency at all.

Pure-process: ``get_auth`` is overridden and the repo method is patched, so no
real Supabase / JWT / DB is needed. NOT marked ``integration`` so it runs in the
unit suite (the wiring it guards is inert, so a regression would otherwise pass
silently until the flag flip).
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.db.scope import Scope, current_scope

_USER_A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client(monkeypatch):
    """TestClient whose auth always resolves to user-A (a UUID, as
    ``resources.creator_id`` is a uuid column)."""
    from app.main import app

    def _auth_user_a() -> AuthContext:
        return AuthContext(user_id=_USER_A, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth_user_a
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_auth, None)


def test_search_endpoint_establishes_user_scope(client, monkeypatch):
    """``GET /api/v1/resources/search`` (router-level ``scoped_request``) sets the
    ambient ``Scope`` to the authed user for the duration of the request.

    We patch the repo method the handler calls and capture ``current_scope()``
    from inside it — the only place that proves the dependency ran BEFORE the
    handler body and the ambient scope is live while the repo executes.
    """
    captured: dict[str, object] = {}

    async def _fake_list_accessible_for_user(
        self,
        *,
        user_id: str,
        q: str = "",
        kinds: Optional[list] = None,
        limit: int = 20,
        scope_team_id: Optional[str] = None,
    ) -> list:
        captured["scope"] = current_scope()
        return []

    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository."
        "list_accessible_for_user",
        _fake_list_accessible_for_user,
    )

    resp = client.get("/api/v1/resources/search", params={"q": "x"})
    assert resp.status_code == 200, resp.text

    scope = captured.get("scope")
    assert isinstance(scope, Scope), (
        f"ambient scope not set during the request: {scope!r}. "
        "ScopedRequestDep wiring regression on resources_search_router."
    )
    assert scope.user_id == _USER_A, f"scope user_id mismatch: {scope.user_id!r}"
    # Single-axis user scope (resources is UserScoped only) — no team/project sets.
    assert scope.team_ids == frozenset()
    assert scope.project_ids == frozenset()


def test_ambient_scope_resets_after_request(client, monkeypatch):
    """The ambient scope must not leak past the request boundary (the
    dependency resets the ContextVar in ``finally``)."""

    async def _fake(self, **_kw) -> list:
        return []

    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository."
        "list_accessible_for_user",
        _fake,
    )

    assert current_scope() is None, "precondition: no ambient scope before request"
    resp = client.get("/api/v1/resources/search", params={"q": "x"})
    assert resp.status_code == 200, resp.text
    assert current_scope() is None, "ambient scope leaked after the request returned"


# ─── Dependency-presence assertions across the wired endpoint set ─────────
#
# Belt-and-braces: even where we don't drive a full ASGI request, assert the
# scoped_request dependency is attached to a representative set of wired routes
# (per-endpoint AND router-level wiring), so a refactor that drops it fails here.


def _route_has_scoped_request(app, path: str, method: str) -> bool:
    from app.core.scope_dep import scoped_request

    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", set()
        ):
            for dep in route.dependant.dependencies:
                if dep.call is scoped_request:
                    return True
    return False


@pytest.mark.parametrize(
    "path,method",
    [
        # router-level wiring (resources-dedicated routers)
        ("/api/v1/resources/search", "GET"),
        ("/api/v1/resources/upload", "POST"),
        ("/api/v1/resources/folders", "POST"),
        # per-endpoint wiring (crud + mixed routers)
        ("/api/v1/resources", "GET"),
        ("/api/v1/resources/{resource_id}", "GET"),
        ("/api/v1/resources/{resource_id}", "PATCH"),
        ("/api/v1/resources/{resource_id}/tags", "POST"),
        ("/api/v1/resources/{resource_id}/versions", "GET"),
        ("/api/v1/media/{platform_id}", "GET"),
        ("/api/v1/media/fetch", "POST"),
    ],
)
def test_scoped_request_present_on_wired_routes(path, method):
    from app.main import app

    assert _route_has_scoped_request(
        app, path, method
    ), f"ScopedRequestDep missing on {method} {path} — A2 wiring regression."
