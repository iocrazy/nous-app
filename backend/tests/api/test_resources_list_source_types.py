"""Router-level tests for the ``source_types`` filter on ``GET /resources``.

``source_types`` restricts by ``resources.source_type`` provenance. The router
validates values against the CHECK enum (web / upload / generated / derived),
422s on anything else, drops empty strings, and forwards the normalised list to
``ResourcesRepository.get_resource_items``. The Distribution Publish picker uses
it to list own content (upload/generated/derived) and exclude ``web`` downloads.

Driven through ``TestClient`` with the auth + scope dependencies overridden so
the request reaches the endpoint body (where the validation + forwarding live).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.core.scope_dep import scoped_request
from app.core.scope_guards import verify_scope_access
from app.main import app

client = TestClient(app)


def _override_auth() -> AuthContext:
    return AuthContext(user_id="u", auth_type="jwt")


@pytest.fixture(autouse=True)
def _bypass_scope_deps():
    """Let requests reach the endpoint body regardless of scope membership."""
    app.dependency_overrides[get_auth] = _override_auth
    app.dependency_overrides[scoped_request] = lambda: None
    app.dependency_overrides[verify_scope_access] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_auth, None)
        app.dependency_overrides.pop(scoped_request, None)
        app.dependency_overrides.pop(verify_scope_access, None)


def test_invalid_source_type_returns_422():
    r = client.get("/api/v1/resources?scope_id=u&source_types=bogus")
    assert r.status_code == 422
    assert "Invalid source_type" in r.json()["error"]


def test_mixed_valid_and_invalid_source_type_returns_422():
    r = client.get(
        "/api/v1/resources?scope_id=u"
        "&source_types=upload&source_types=web&source_types=bogus"
    )
    assert r.status_code == 422


def test_valid_source_types_forwarded_to_repo():
    with patch(
        "app.api.resources_crud_router.ResourcesRepository.get_resource_items",
        new=AsyncMock(return_value=[]),
    ) as mock_get:
        r = client.get(
            "/api/v1/resources?scope_id=u"
            "&source_types=upload&source_types=generated&source_types=derived"
        )
    assert r.status_code == 200
    assert mock_get.await_args.kwargs["source_types"] == [
        "upload",
        "generated",
        "derived",
    ]


def test_empty_source_types_forwarded_as_none():
    """``?source_types=&source_types=`` → all-empty → normalised to None so the
    repo applies no provenance clause."""
    with patch(
        "app.api.resources_crud_router.ResourcesRepository.get_resource_items",
        new=AsyncMock(return_value=[]),
    ) as mock_get:
        r = client.get("/api/v1/resources?scope_id=u&source_types=&source_types=")
    assert r.status_code == 200
    assert mock_get.await_args.kwargs["source_types"] is None


def test_absent_source_types_forwarded_as_none():
    with patch(
        "app.api.resources_crud_router.ResourcesRepository.get_resource_items",
        new=AsyncMock(return_value=[]),
    ) as mock_get:
        r = client.get("/api/v1/resources?scope_id=u")
    assert r.status_code == 200
    assert mock_get.await_args.kwargs["source_types"] is None
