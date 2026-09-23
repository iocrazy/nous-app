"""Gallery MIME rename, step 1 of 2: write the new value, accept both.

``resources.mime_type`` is persisted, and migrations and code deploy in no
guaranteed order, so every read / filter must accept BOTH the legacy
``application/x-mediahub-gallery`` and the new ``application/x-nous-gallery``
while every write already uses the new one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from app.api import resources_gallery_router as router_mod
from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import ResourcesRepository
from app.services.library.gallery_mime import (
    GALLERY_MIME,
    GALLERY_MIMES,
    LEGACY_GALLERY_MIME,
    is_gallery_mime,
)

NEW = "application/x-nous-gallery"
OLD = "application/x-mediahub-gallery"


# ─── constants + predicate ────────────────────────────────────────────


def test_write_value_is_the_new_mime() -> None:
    assert GALLERY_MIME == NEW
    assert LEGACY_GALLERY_MIME == OLD


def test_accept_set_holds_both_values() -> None:
    assert set(GALLERY_MIMES) == {NEW, OLD}
    assert GALLERY_MIMES[0] == NEW


@pytest.mark.parametrize("mime", [NEW, OLD])
def test_is_gallery_mime_accepts_both(mime: str) -> None:
    assert is_gallery_mime(mime) is True


@pytest.mark.parametrize(
    "mime", [None, "", "image/png", "application/x-mediahub-tag", "gallery"]
)
def test_is_gallery_mime_rejects_others(mime: Optional[str]) -> None:
    assert is_gallery_mime(mime) is False


# ─── repository listing filter ────────────────────────────────────────


class _Result:
    def mappings(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return []


class _CapSession:
    def __init__(self) -> None:
        self.calls: List[tuple[Any, Dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self.calls.append((stmt, dict(params or {})))
        return _Result()


@asynccontextmanager
async def _fake_scope(session: _CapSession):
    yield session


@pytest.mark.asyncio
async def test_gallery_type_filter_binds_both_mimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession()
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))

    await ResourcesRepository().get_resource_items(
        scope_type="personal", scope_id="1", types=["gallery"]
    )
    stmt, params = session.calls[-1]
    sql = str(stmt)
    assert "r.mime_type = ANY(:gallery_mimes)" in sql
    assert set(params["gallery_mimes"]) == {NEW, OLD}
    # No literal gallery mime is inlined into the SQL text any more.
    assert OLD not in sql and NEW not in sql


@pytest.mark.asyncio
async def test_other_type_filter_excludes_both_mimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession()
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))

    await ResourcesRepository().get_resource_items(
        scope_type="personal", scope_id="1", types=["other"]
    )
    stmt, params = session.calls[-1]
    assert "r.mime_type = ANY(:gallery_mimes)" in str(stmt)
    assert set(params["gallery_mimes"]) == {NEW, OLD}


@pytest.mark.asyncio
async def test_non_gallery_filter_does_not_bind_gallery_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession()
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))

    await ResourcesRepository().get_resource_items(
        scope_type="personal", scope_id="1", types=["video"]
    )
    _, params = session.calls[-1]
    assert "gallery_mimes" not in params


# ─── router: write new, accept both ───────────────────────────────────


class _FakeRepo:
    """Real-shaped ``resources`` rows: BIGINT id as int, mime as persisted."""

    def __init__(self, gallery_mime: str = NEW) -> None:
        self.created: List[Dict[str, Any]] = []
        self.gallery = {"id": 7_300_000_000_000_001, "mime_type": gallery_mime}

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        self.created.append(data)
        return {"id": 7_300_000_000_000_002, **data}

    async def create_resource_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return data

    async def get_resource_by_id(self, _rid: str) -> Dict[str, Any]:
        return self.gallery

    async def get_gallery_items(self, _gid: str) -> List[Dict[str, Any]]:
        return [{"id": "1", "position": 0}]

    async def set_gallery_items(self, _gid: str, _ids: List[str]) -> None:
        return None

    async def update_resource(self, _gid: str, _data: Dict[str, Any]) -> None:
        return None


_AUTH = SimpleNamespace(user_id="00000000-0000-0000-0000-000000000001")


async def _allow(*_a: Any, **_k: Any) -> bool:
    return True


@pytest.mark.asyncio
async def test_create_gallery_writes_new_mime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeRepo()
    monkeypatch.setattr(router_mod, "ResourcesRepository", lambda: repo)

    await router_mod.create_gallery(
        auth=_AUTH, scope_id="1", filename="My Gallery", folder_id=None
    )
    assert repo.created[0]["mime_type"] == NEW


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", [NEW, OLD])
async def test_list_gallery_items_accepts_both_mimes(
    monkeypatch: pytest.MonkeyPatch, mime: str
) -> None:
    repo = _FakeRepo(gallery_mime=mime)
    monkeypatch.setattr(router_mod, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(router_mod, "check_media_access", _allow)

    out = await router_mod.list_gallery_items(gallery_id="1", auth=_AUTH)
    assert out["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", [NEW, OLD])
async def test_set_gallery_items_accepts_both_mimes(
    monkeypatch: pytest.MonkeyPatch, mime: str
) -> None:
    repo = _FakeRepo(gallery_mime=mime)
    monkeypatch.setattr(router_mod, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(router_mod, "check_media_access", _allow)

    body = router_mod.GallerySetItemsRequest(image_ids=[])
    out = await router_mod.set_gallery_items(
        gallery_id="1", body=body, auth=_AUTH, scope_id="1"
    )
    assert out["success"] is True


@pytest.mark.asyncio
async def test_non_gallery_resource_still_404s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeRepo(gallery_mime="image/png")
    monkeypatch.setattr(router_mod, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(router_mod, "check_media_access", _allow)

    with pytest.raises(HTTPException) as exc:
        await router_mod.list_gallery_items(gallery_id="1", auth=_AUTH)
    assert exc.value.status_code == 404
