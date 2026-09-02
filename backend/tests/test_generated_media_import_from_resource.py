"""Unit tests for resolve_resource_import — resource → validated (raw) args,
and the POST /generated-media/import-from-resource endpoint (mirrors
test_generated_media_import.py's function-level, no-TestClient convention).

resources.file_path is NOT an absolute host path (storage unification): it's
either legacy filesystem-relative-to-DOWNLOAD_PATH, or an `sb://bucket/key`
object-store URI. The endpoint resolves either shape via the shared
`materialize()` helper (media_storage.py) before calling
register_generated_media. These tests cover both shapes end to end.
"""

import os

import pytest

from app.api.generated_media_router import (
    ResourceImportError,
    ResourceImportRequest,
    resolve_resource_import,
)


def test_resolves_image_resource():
    row = {
        "id": 1,
        "file_path": "teams/42/uploads/1/v1/a.png",
        "mime_type": "image/png",
    }
    args = resolve_resource_import(row, row["file_path"])
    assert args == {"file_path": "teams/42/uploads/1/v1/a.png", "mime": "image/png"}


def test_resolves_object_store_resource():
    """sb:// rows pass through resolve_resource_import unresolved — the
    endpoint (via materialize()) is what tells the two shapes apart."""
    row = {
        "id": 1,
        "file_path": "sb://library/t42/ab/cd/deadbeef.png",
        "mime_type": "image/png",
    }
    args = resolve_resource_import(row, row["file_path"])
    assert args == {
        "file_path": "sb://library/t42/ab/cd/deadbeef.png",
        "mime": "image/png",
    }


def test_missing_file_path_raises_404():
    with pytest.raises(ResourceImportError) as e:
        resolve_resource_import({"id": 1, "mime_type": "image/png"}, None)
    assert e.value.status_code == 404


def test_non_media_mime_raises_400():
    with pytest.raises(ResourceImportError) as e:
        resolve_resource_import({"id": 1, "mime_type": "application/pdf"}, "/d/a.pdf")
    assert e.value.status_code == 400


def test_video_mime_allowed():
    row = {"id": 1, "file_path": "d/v.mp4", "mime_type": "video/mp4"}
    assert resolve_resource_import(row, row["file_path"])["mime"] == "video/mp4"


class _Auth:
    user_id = "u-uuid"


def _router_mod():
    import importlib

    return importlib.import_module("app.api.generated_media_router")


def _patch_deps(
    monkeypatch,
    captured,
    *,
    resource=None,
    access=True,
):
    import app.services.library.generated_media_service as gm
    from app.repositories import resources_repository as resources_repo_mod

    router_mod = _router_mod()

    async def _fake_scope(_auth):
        return 42

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": 999}

    async def _fake_get_resource_by_id(self, resource_id):
        captured["resource_id_looked_up"] = resource_id
        return resource

    async def _fake_check_media_access(resource_id, user_id, share_token):
        captured["access_checked_for"] = resource_id
        return access

    monkeypatch.setattr(router_mod, "_scope", _fake_scope)
    monkeypatch.setattr(gm, "register_generated_media", _fake_register)
    monkeypatch.setattr(
        resources_repo_mod.ResourcesRepository,
        "get_resource_by_id",
        _fake_get_resource_by_id,
    )
    monkeypatch.setattr(
        "app.api.media_permissions.check_media_access", _fake_check_media_access
    )


@pytest.mark.asyncio
async def test_import_from_resource_registers_and_returns_cover_url(
    monkeypatch, tmp_path
):
    """Filesystem-relative file_path: register_generated_media must receive
    the real DOWNLOAD_PATH-joined absolute path, not the raw relative one."""
    from app.api.generated_media_router import import_from_resource
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/42/uploads/1/v1/a.png"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"fake-png-bytes")

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={"id": 1, "file_path": rel, "mime_type": "image/png"},
    )

    resp = await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert resp == {
        "data": {
            "id": "999",
            "url": "/api/v1/generated-media/999/cover",
            "media_kind": "image",
            "mime": "image/png",
        }
    }
    assert captured["scope_id"] == 42
    assert captured["source_path"] == os.path.realpath(str(abs_path))
    assert captured["mime"] == "image/png"
    assert captured["origin"].kind == "canvas_upload"
    # A library asset minted purely so the i2i bridge can fetch it — the user
    # already owns it in My Uploads, so it is an INPUT, not something to triage.
    assert captured["origin"].params["role"] == "reference"


@pytest.mark.asyncio
async def test_import_from_resource_object_store_row(monkeypatch, tmp_path):
    """sb:// file_path: materialize() must stream the object to a temp file
    and hand register_generated_media that real local path — and clean the
    temp file up afterward (not leave it behind)."""
    from app.api.generated_media_router import import_from_resource
    from app.services.library import media_storage as ms

    async def _fake_get_stream(self, key, *, start=None, end=None, chunk_size=None):
        yield b"object-store-bytes"

    monkeypatch.setattr(ms.ObjectStore, "get_stream", _fake_get_stream)

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={
            "id": 1,
            "file_path": "sb://library/t42/ab/cd/deadbeef.png",
            "mime_type": "image/png",
        },
    )

    resp = await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert resp["data"]["id"] == "999"
    source_path = captured["source_path"]
    # Materialized from the object-store stream into a real temp file whose
    # content matches what get_stream yielded.
    assert source_path.endswith(".png")
    # Cleaned up by materialize()'s finally block once register_generated_media
    # (mocked here) returned.
    assert not os.path.exists(source_path)


@pytest.mark.asyncio
async def test_import_from_resource_missing_file_maps_to_404(monkeypatch, tmp_path):
    """A resources row whose file_path no longer exists on disk must 404, not
    bubble a raw FileNotFoundError/500."""
    from fastapi import HTTPException

    from app.api.generated_media_router import import_from_resource
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={
            "id": 1,
            "file_path": "teams/42/uploads/1/v1/missing.png",
            "mime_type": "image/png",
        },
    )

    with pytest.raises(HTTPException) as exc:
        await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_import_from_resource_exercises_real_register_path(monkeypatch, tmp_path):
    """Does NOT mock register_generated_media wholesale — only its DB-insert
    layer. Exercises the real file-path-resolution seam: materialize() joins
    DOWNLOAD_PATH, then register_generated_media's own _copy_local_to
    actually copies bytes to the generation's destination path."""
    import app.services.library.generated_media_service as gm
    from app.api.generated_media_router import import_from_resource
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)

    rel = "teams/42/uploads/1/v1/a.png"
    src_abs = tmp_path / rel
    src_abs.parent.mkdir(parents=True, exist_ok=True)
    src_abs.write_bytes(b"real-bytes-through-the-real-copy-path")

    inserted = {}

    from contextlib import asynccontextmanager

    from sqlalchemy.dialects import postgresql

    @asynccontextmanager
    async def _fake_write_scope():
        class _Result:
            def __init__(self, row):
                self._row = row

            def mappings(self):
                return self

            def first(self):
                return self._row

        class _Session:
            async def execute(self, stmt):
                params = dict(stmt.compile(dialect=postgresql.dialect()).params)
                inserted.update(params)
                return _Result({**params, "id": 999})

        yield _Session()

    monkeypatch.setattr(gm, "write_scope", _fake_write_scope)

    # _patch_deps wholesale-mocks gm.register_generated_media; keep a
    # reference to the real one so it can be restored after — this test's
    # whole point is to exercise it for real, faking only the DB insert.
    real_register = gm.register_generated_media

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={"id": 1, "file_path": rel, "mime_type": "image/png"},
    )
    monkeypatch.setattr(gm, "register_generated_media", real_register)

    resp = await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert resp["data"]["id"] == "999"
    assert inserted["file_size_bytes"] == len(b"real-bytes-through-the-real-copy-path")
    dest = tmp_path / inserted["file_path"]
    assert dest.exists()
    assert dest.read_bytes() == b"real-bytes-through-the-real-copy-path"


@pytest.mark.asyncio
async def test_import_from_resource_video_returns_stream_url(monkeypatch, tmp_path):
    from app.api.generated_media_router import import_from_resource
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/42/uploads/1/v1/v.mp4"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"fake-mp4-bytes")

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={"id": 1, "file_path": rel, "mime_type": "video/mp4"},
    )

    resp = await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert resp["data"]["url"] == "/api/v1/generated-media/999/stream"
    assert resp["data"]["media_kind"] == "video"


@pytest.mark.asyncio
async def test_import_from_resource_missing_resource_404(monkeypatch):
    from fastapi import HTTPException

    from app.api.generated_media_router import import_from_resource

    captured = {}
    _patch_deps(monkeypatch, captured, resource=None)

    with pytest.raises(HTTPException) as exc:
        await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_import_from_resource_no_access_403(monkeypatch):
    from fastapi import HTTPException

    from app.api.generated_media_router import import_from_resource

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={"id": 1, "file_path": "d/a.png", "mime_type": "image/png"},
        access=False,
    )

    with pytest.raises(HTTPException) as exc:
        await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_import_from_resource_bad_mime_400(monkeypatch):
    from fastapi import HTTPException

    from app.api.generated_media_router import import_from_resource

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={"id": 1, "file_path": "d/a.pdf", "mime_type": "application/pdf"},
    )

    with pytest.raises(HTTPException) as exc:
        await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_import_from_resource_reads_the_resource_INSIDE_a_tenant_scope(
    monkeypatch, tmp_path
):
    """2026-08-27 生产：resources 是受 scope 保护的表，端点在 scope 外查它 →
    UnscopedQueryError，封面工作室里每一张模板都"没能准备成参考图"。钉住：
    仓库调用发生时必须已经开了 scope。"""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.db.scope import current_scope
    from app.repositories import resources_repository as resources_repo_mod

    seen: dict = {}

    async def _fake_get_resource_by_id(self, resource_id):
        seen["scope"] = current_scope()
        return None  # → 404, we only care about the scope at call time

    monkeypatch.setattr(
        resources_repo_mod.ResourcesRepository,
        "get_resource_by_id",
        _fake_get_resource_by_id,
    )
    router_mod = _router_mod()
    auth = SimpleNamespace(user_id="8e1584e3-9c29-4a5b-90fe-125b74259f7f")
    with pytest.raises(HTTPException) as ei:
        await router_mod.import_from_resource(
            ResourceImportRequest(resource_id="342622652031665"), auth
        )
    assert ei.value.status_code == 404
    assert seen["scope"] is not None, "resources 必须在 request_scope 内读"
