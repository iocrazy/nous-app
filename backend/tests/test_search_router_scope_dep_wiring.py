"""Pin the ``scoped_request`` WIRING on the search router.

2026-09-24 production incident: Settings → AI → Vectors showed "无法加载向量状态".
``GET /api/v1/search/vectors/status`` read the ``resources`` table (via
``ResourceEmbeddingsRepository.coverage``) with NO ambient scope, and production
runs ``SCOPE_ENFORCE_RESOURCES=true`` → the ORM choke point raised
``UnscopedQueryError`` → 500. Local and CI have the flag off, so every unit test
and the schema-drift gate were green (same family as CLAUDE.md "DBOS 步骤写
resources 必须包 system_request_scope": the flag is only on in prod).

The four ``resources_*`` routers declare ``dependencies=[Depends(scoped_request)]``
at router level; ``search_router`` never did, although ``/hybrid``, ``/quick``,
``/similar`` and ``/vectors/status`` all hydrate through ``resources``. This file
mirrors ``tests/test_resources_scope_dep_wiring.py``: it proves the ambient
``Scope`` is live inside a repo method the handler calls.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.db.scope import Scope, current_scope

_USER_A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client():
    from app.main import app

    def _auth_user_a() -> AuthContext:
        return AuthContext(user_id=_USER_A, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth_user_a
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_auth, None)


def test_search_router_declares_scoped_request_dependency():
    """Structural: the dependency sits on the ROUTER so every present and future
    search endpoint gets it, exactly like the resources routers."""
    from fastapi import params

    from app.api import search_router as sr
    from app.core.scope_dep import scoped_request

    calls = [d.dependency for d in sr.dependencies if isinstance(d, params.Depends)]
    assert scoped_request in calls


def test_vectors_status_runs_repo_under_user_scope(client, monkeypatch):
    """``GET /search/vectors/status`` must reach the embeddings repo with the
    authed user's ``Scope`` live — that is what makes the ``resources`` read
    legal under ``SCOPE_ENFORCE_RESOURCES=true``."""
    import importlib

    # ``app.api`` rebinds the name ``search_router`` to the APIRouter object,
    # shadowing the submodule attribute — go through importlib.
    search_router_mod = importlib.import_module("app.api.search_router")
    from app.repositories import resource_embeddings_repository as rer

    seen: list[object] = []

    async def _fake_space_spec(self):
        return None  # "unconfigured" branch → coverage() is still called

    async def _fake_coverage(self, *, user_id, space_id, layer):
        seen.append(current_scope())
        return 0, 0

    monkeypatch.setattr(
        search_router_mod.EmbeddingService, "space_spec", _fake_space_spec
    )
    monkeypatch.setattr(rer.ResourceEmbeddingsRepository, "coverage", _fake_coverage)

    resp = client.get("/api/v1/search/vectors/status")

    assert resp.status_code == 200, resp.text
    assert seen == [Scope(user_id=_USER_A)]
    assert current_scope() is None  # reset after the request
