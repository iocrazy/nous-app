"""Real-shape rows and an HTTP harness for the ``/resources`` wire-parity tests.

Every row is built from the ORM model (:func:`wire_parity.sample_orm`, every
column set, native types) and then passed through the repository's OWN
converter (``_resources_row_to_dict`` …), so what the routes receive here is
what ``ResourcesRepository`` hands them in production — int Snowflake ids,
uuid/datetime already stringified by ``_rest_parity``. See
``tests/api/wire_parity.py`` for why hand-written dicts are not enough.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_resource_write_access, verify_scope_access
from app.main import app
from app.models import (
    Folders,
    ResourceItems,
    Resources,
    ResourceTags,
    ResourceVersions,
    Tags,
)
from app.services.lrc_parser import parse_lrc
from tests.api.wire_parity import sample_orm

repo_mod = sys.modules["app.repositories.resources_repository"]

USER = "00000000-0000-0000-0000-000000000042"
SCOPE = "42"
LRC = "[00:01.00]first line\nuntimed line\n"


def nulled(row: dict, model: type[BaseModel]) -> dict:
    """``row`` with every field ``model`` declares nullable set to None."""
    out = dict(row)
    for name, field in model.model_fields.items():
        if name in out and type(None) in getattr(field.annotation, "__args__", ()):
            out[name] = None
    return out


def resource_row(**over: Any) -> dict:
    """A ``resources`` row as ``get_resource_by_id`` returns it. ``lyrics_json``
    carries what its only writer (``parse_lrc``) stores, not a placeholder."""
    row = repo_mod._resources_row_to_dict(
        sample_orm(Resources, lyrics_json=parse_lrc(LRC))
    )
    row.update(over)
    return row


def item_row(**over: Any) -> dict:
    row = repo_mod._resource_item_row_to_dict(sample_orm(ResourceItems))
    row.update(over)
    return row


def version_row(**over: Any) -> dict:
    row = repo_mod._version_row_to_dict(sample_orm(ResourceVersions))
    row.update(over)
    return row


def folder_row(**over: Any) -> dict:
    row = repo_mod._folder_row_to_dict(sample_orm(Folders))
    row.update(over)
    return row


def resource_tag_row() -> dict:
    return repo_mod._resource_tag_row_to_dict(sample_orm(ResourceTags))


def tag_row() -> dict:
    return repo_mod._tag_row_to_dict(sample_orm(Tags))


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


async def _allow() -> None:
    return None


@pytest.fixture(autouse=True)
def resources_http_overrides(monkeypatch):
    """Auth + the two DB-backed guards, and ``check_media_access`` → allowed."""
    import app.api.media_permissions as mp

    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[verify_scope_access] = _allow
    app.dependency_overrides[verify_resource_write_access] = _allow

    async def _access(*args, **kwargs):
        return True

    monkeypatch.setattr(mp, "check_media_access", _access)
    yield
    for dep in (get_auth, verify_scope_access, verify_resource_write_access):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class Fake:
    """Attribute bag whose async methods are set per test."""

    def __init__(self, **methods: Any) -> None:
        for name, value in methods.items():
            setattr(self, name, _as_async(value))


def _as_async(value: Any):
    if callable(value):

        async def _call(*args, **kwargs):
            return value(*args, **kwargs)

    else:

        async def _call(*args, **kwargs):
            return value

    return _call
