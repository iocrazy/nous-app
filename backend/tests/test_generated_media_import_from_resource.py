"""Unit tests for resolve_resource_import — resource → register args, and the
POST /generated-media/import-from-resource endpoint (mirrors
test_generated_media_import.py's function-level, no-TestClient convention).
"""

import pytest

from app.api.generated_media_router import (
    ResourceImportError,
    ResourceImportRequest,
    resolve_resource_import,
)


def test_resolves_image_resource():
    row = {"id": 1, "file_path": "/data/uploads/a.png", "mime_type": "image/png"}
    args = resolve_resource_import(row)
    assert args == {"source_path": "/data/uploads/a.png", "mime": "image/png"}


def test_missing_file_path_raises_404():
    with pytest.raises(ResourceImportError) as e:
        resolve_resource_import({"id": 1, "file_path": None, "mime_type": "image/png"})
    assert e.value.status_code == 404


def test_non_media_mime_raises_400():
    with pytest.raises(ResourceImportError) as e:
        resolve_resource_import(
            {"id": 1, "file_path": "/d/a.pdf", "mime_type": "application/pdf"}
        )
    assert e.value.status_code == 400


def test_video_mime_allowed():
    row = {"id": 1, "file_path": "/d/v.mp4", "mime_type": "video/mp4"}
    assert resolve_resource_import(row)["mime"] == "video/mp4"


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
async def test_import_from_resource_registers_and_returns_cover_url(monkeypatch):
    from app.api.generated_media_router import import_from_resource

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={"id": 1, "file_path": "/data/a.png", "mime_type": "image/png"},
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
    assert captured["source_path"] == "/data/a.png"
    assert captured["mime"] == "image/png"
    assert captured["origin"].kind == "canvas_upload"


@pytest.mark.asyncio
async def test_import_from_resource_video_returns_stream_url(monkeypatch):
    from app.api.generated_media_router import import_from_resource

    captured = {}
    _patch_deps(
        monkeypatch,
        captured,
        resource={"id": 1, "file_path": "/data/v.mp4", "mime_type": "video/mp4"},
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
        resource={"id": 1, "file_path": "/data/a.png", "mime_type": "image/png"},
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
        resource={"id": 1, "file_path": "/data/a.pdf", "mime_type": "application/pdf"},
    )

    with pytest.raises(HTTPException) as exc:
        await import_from_resource(ResourceImportRequest(resource_id="1"), _Auth())
    assert exc.value.status_code == 400
