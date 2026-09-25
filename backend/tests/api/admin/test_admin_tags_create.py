"""``POST /admin/tags`` writes an ordinary tag owned by the calling admin.

It used to insert ``type='system', user_id=NULL``. mig 468 retired system
tags and dropped ``'system'`` from ``tags_type_check``, so from then on every
create from the admin console was a CHECK violation → 500. The route now
writes ``type='user'`` under the admin's own ``user_id``, the same shape the
user-facing ``POST /tags`` writes, and a duplicate name is a 409.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import Tags
from tests.api.wire_parity import sample_orm

pytestmark = pytest.mark.unit

ADMIN = "00000000-0000-0000-0000-0000000000ad"
tags_router_module = importlib.import_module("app.api.admin.tags_router")
repo_module = importlib.import_module("app.repositories.admin.tags_repository")


class _FakeRepo:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.inserted: list[dict[str, Any]] = []

    async def create_tag(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.inserted.append(payload)
        if self.fail is not None:
            raise self.fail
        # The repository's real RETURNING shape (every column), so the
        # route's response model sees what production hands it.
        row = repo_module._obj_dict(sample_orm(Tags), repo_module._TAG_N2A)
        return {**row, **payload}


@pytest.fixture
def admin(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=ADMIN, auth_type="jwt")

    class _Row:
        def first(self):
            return ("admin",)

    class _Session:
        async def execute(self, *a, **kw):
            return _Row()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    app.dependency_overrides[get_auth] = _auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _allowed_types() -> str:
    (check,) = [c for c in Tags.__table__.constraints if c.name == "tags_type_check"]
    return str(check.sqltext)


@pytest.mark.asyncio
async def test_create_writes_a_user_tag_owned_by_the_admin(client, admin, monkeypatch):
    repo = _FakeRepo()
    monkeypatch.setattr(tags_router_module, "get_admin_tags_repository", lambda: repo)

    resp = await client.post("/api/v1/admin/tags", json={"name": "Keep"})

    assert resp.status_code == 200, resp.text
    (row,) = repo.inserted
    assert row["type"] == "user"
    assert row["user_id"] == ADMIN
    # The value must be one the live CHECK accepts ('system' is not, since 468).
    assert f"'{row['type']}'" in _allowed_types()
    assert "'system'" not in _allowed_types()


@pytest.mark.asyncio
async def test_duplicate_name_is_409_not_500(client, admin, monkeypatch):
    repo = _FakeRepo(
        fail=IntegrityError("INSERT", {}, Exception("unique_tag_per_scope"))
    )
    monkeypatch.setattr(tags_router_module, "get_admin_tags_repository", lambda: repo)

    resp = await client.post("/api/v1/admin/tags", json={"name": "Keep"})

    assert resp.status_code == 409, resp.text
