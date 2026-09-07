from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _gen_row(**over):
    base = {
        "id": "7",
        "scope_id": "42",
        "creator_id": "u-uuid",
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "teams/42/generations/abc/media.png",
        "prompt": "a cat",
        "model": "m",
        "provider": "p",
        "canvas_id": None,
        "node_id": None,
        "origin_kind": "generated",
        "promoted_resource_id": None,
    }
    base.update(over)
    return base


def _patch_scope_auth(monkeypatch, svc_mod, *, personal_team_id=999):
    fake_conv_repo = AsyncMock()
    fake_conv_repo.is_team_member.return_value = True
    fake_conv_repo.is_member.return_value = True

    async def _personal(_user_id):
        return str(personal_team_id)

    monkeypatch.setattr(svc_mod, "get_conversation_repository", lambda: fake_conv_repo)
    monkeypatch.setattr(svc_mod, "_resolve_personal_team_id", _personal)
    return fake_conv_repo


@pytest.mark.asyncio
async def test_promote_creates_resource_and_marks(monkeypatch, tmp_path):
    import app.services.library.promote_generated_media_service as svc_mod

    # source file on disk
    src = tmp_path / "teams/42/generations/abc/media.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"imgbytes")
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))

    svc = svc_mod.PromoteGeneratedMediaService()

    _patch_scope_auth(monkeypatch, svc_mod)

    async def _get_by_id(gen_id):
        return _gen_row()

    created = {}

    async def _create_resource(data):
        created["resource"] = data
        return {"id": 555}

    async def _create_version(data):
        created["version"] = data
        return {"id": 1}

    async def _create_item(data):
        created["item"] = data
        return {"id": 2}

    updated = {}

    async def _update_resource(resource_id, data):
        updated["args"] = (resource_id, data)
        return {}

    marked = {}

    async def _mark(gen_id, rid):
        marked["args"] = (gen_id, rid)
        return _gen_row(promoted_resource_id=str(rid))

    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)
    monkeypatch.setattr(svc.gen_repo, "mark_promoted", _mark)
    monkeypatch.setattr(svc.res_repo, "create_resource", _create_resource)
    monkeypatch.setattr(svc.res_repo, "create_version", _create_version)
    monkeypatch.setattr(svc.res_repo, "create_resource_item", _create_item)
    monkeypatch.setattr(svc.res_repo, "update_resource", _update_resource)

    out = await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)
    assert out["id"] == 555
    assert created["resource"]["source_type"] == "generated"
    assert created["resource"]["creator_id"] == "u-uuid"
    assert created["resource"]["gen_params"] == {
        "tool": "nous",
        "model": "m",
        "provider": "p",
    }
    assert created["item"]["scope_id"] == 42 and created["item"]["folder_id"] is None
    # file copied into resources layout
    dst = tmp_path / "teams/42/uploads/555/v1"
    assert any(dst.iterdir())
    assert marked["args"] == (7, 555)
    # file_path set on resource row
    assert updated["args"][0] == "555"
    assert "uploads/555/v1" in updated["args"][1]["file_path"]


@pytest.mark.asyncio
async def test_promote_canvas_origin(monkeypatch, tmp_path):
    import app.services.library.promote_generated_media_service as svc_mod

    # source file on disk (same setup as happy-path test)
    src = tmp_path / "teams/42/generations/abc/media.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"imgbytes")
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))

    svc = svc_mod.PromoteGeneratedMediaService()

    _patch_scope_auth(monkeypatch, svc_mod)

    async def _get_by_id(gen_id):
        return _gen_row(canvas_id="99", node_id="n1")

    async def _create_resource(data):
        return {"id": 555}

    async def _create_version(data):
        return {"id": 1}

    async def _create_item(data):
        return {"id": 2}

    async def _mark(gen_id, rid):
        return _gen_row(promoted_resource_id=str(rid), canvas_id="99", node_id="n1")

    async def _update_resource_canvas(resource_id, data):
        return {}

    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)
    monkeypatch.setattr(svc.gen_repo, "mark_promoted", _mark)
    monkeypatch.setattr(svc.res_repo, "create_resource", _create_resource)
    monkeypatch.setattr(svc.res_repo, "create_version", _create_version)
    monkeypatch.setattr(svc.res_repo, "create_resource_item", _create_item)
    monkeypatch.setattr(svc.res_repo, "update_resource", _update_resource_canvas)

    # capture the canvas_resource_refs insert via a fake write_scope() —
    # the ORM statement (Phase B5 Task 1) replaces the old raw
    # db_engine.execute_as_service_role(sql, params) call.
    from contextlib import asynccontextmanager

    from sqlalchemy.dialects import postgresql

    session_calls = []

    class _FakeSession:
        async def execute(self, stmt):
            session_calls.append(stmt)

    @asynccontextmanager
    async def _fake_write_scope():
        yield _FakeSession()

    monkeypatch.setattr(svc_mod, "write_scope", _fake_write_scope)

    out = await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)

    # resource created normally
    assert out["id"] == 555

    # SET LOCAL ROLE service_role, THEN the canvas_resource_refs INSERT —
    # RLS on that table is locked to service_role (mig 290).
    assert len(session_calls) == 2
    role_sql = str(session_calls[0].compile(dialect=postgresql.dialect()))
    assert role_sql == "SET LOCAL ROLE service_role"

    insert_stmt = session_calls[1]
    compiled = insert_stmt.compile(dialect=postgresql.dialect())
    binds = dict(compiled.params)
    assert "INSERT INTO public.canvas_resource_refs" in str(compiled)
    assert binds["canvas_id"] == 99
    assert binds["resource_id"] == 555
    assert binds["node_id"] == "n1"


@pytest.mark.asyncio
async def test_promote_idempotent(monkeypatch):
    import app.services.library.promote_generated_media_service as svc_mod

    svc = svc_mod.PromoteGeneratedMediaService()

    _patch_scope_auth(monkeypatch, svc_mod)

    async def _get_by_id(gen_id):
        return _gen_row(promoted_resource_id="900")

    async def _get_res(resource_id):
        return {"id": int(resource_id)}

    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)
    monkeypatch.setattr(svc.res_repo, "get_resource_by_id", _get_res)

    out = await svc.promote(gen_id=7, user_id="u", target_scope_id=42)
    assert str(out["id"]) == "900"  # returns existing, no re-create


@pytest.mark.asyncio
async def test_promote_missing_source_raises(monkeypatch, tmp_path):
    import app.services.library.promote_generated_media_service as svc_mod

    # DOWNLOAD_PATH points to tmp_path but the source file is NOT created
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))

    svc = svc_mod.PromoteGeneratedMediaService()

    _patch_scope_auth(monkeypatch, svc_mod)

    async def _get_by_id(gen_id):
        return _gen_row(file_path="teams/42/generations/missing/media.png")

    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)

    with pytest.raises(ValueError, match="generation file missing"):
        await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)


@pytest.mark.asyncio
async def test_promote_chat_upload_requires_source_membership(monkeypatch):
    import app.services.library.promote_generated_media_service as svc_mod

    svc = svc_mod.PromoteGeneratedMediaService()
    fake_conv_repo = _patch_scope_auth(monkeypatch, svc_mod)
    fake_conv_repo.is_member.return_value = False

    async def _get_by_id(gen_id):
        return _gen_row(origin_kind="chat_upload", conversation_id="123")

    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)

    with pytest.raises(PermissionError, match="source conversation"):
        await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)


@pytest.mark.asyncio
async def test_promote_rejects_target_without_membership(monkeypatch):
    import app.services.library.promote_generated_media_service as svc_mod

    svc = svc_mod.PromoteGeneratedMediaService()
    fake_conv_repo = _patch_scope_auth(monkeypatch, svc_mod, personal_team_id=999)
    fake_conv_repo.is_team_member.side_effect = [True, False]

    async def _get_by_id(gen_id):
        return _gen_row()

    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)

    with pytest.raises(PermissionError, match="target scope"):
        await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)


# ─── Task 2.4 — source read via materialize() + dual-track destination ─────
#
# `promote()` used to hand-roll the fs-vs-object-store split with a direct
# `ObjectStore(loc.bucket).get_bytes(...)` for sb:// sources and always wrote
# the promoted resource straight to the filesystem. Now the source read goes
# through `materialize()` uniformly (fs row: real path; sb:// row: streamed
# temp file) and the destination write mirrors the resources-upload
# dual-track pattern (Task 2.1): `FEATURE_UNIFIED_STORAGE` on + a successful
# `store_local_file()` write lands an `sb://library/...` file_path; off, or
# any storage failure, falls back to the pre-existing filesystem copy2 with
# a `logger.error` (Task 2.4c: fallbacks must surface in the ERROR funnel).


def _fake_materialize(seen: dict, fixture: Path):
    @asynccontextmanager
    async def _materialize(file_path: str):
        seen["file_path"] = file_path
        yield fixture

    return _materialize


@pytest.mark.asyncio
async def test_promote_source_read_via_materialize_not_hand_rolled_object_store(
    monkeypatch, tmp_path
):
    """A generation stored as sb:// (chat-media) is read through
    materialize() — not a hand-rolled ObjectStore.get_bytes call — proving
    the orchestration change, independent of the destination flag."""
    import app.services.library.promote_generated_media_service as svc_mod

    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(svc_mod.settings, "FEATURE_UNIFIED_STORAGE", False)

    svc = svc_mod.PromoteGeneratedMediaService()
    _patch_scope_auth(monkeypatch, svc_mod)

    materialized_src = tmp_path.parent / "materialize-tmp" / "media.png"
    materialized_src.parent.mkdir(parents=True, exist_ok=True)
    materialized_src.write_bytes(b"sb-source-bytes")

    seen: dict = {}
    monkeypatch.setattr(
        svc_mod, "materialize", _fake_materialize(seen, materialized_src)
    )

    async def _get_by_id(gen_id):
        return _gen_row(file_path="sb://chat-media/t42/ab/cd/deadbeef.png")

    async def _create_resource(data):
        return {"id": 777}

    async def _create_version(data):
        return {"id": 1}

    async def _create_item(data):
        return {"id": 2}

    updated = {}

    async def _update_resource(resource_id, data):
        updated["args"] = (resource_id, data)
        return {}

    async def _mark(gen_id, rid):
        return _gen_row(promoted_resource_id=str(rid))

    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)
    monkeypatch.setattr(svc.gen_repo, "mark_promoted", _mark)
    monkeypatch.setattr(svc.res_repo, "create_resource", _create_resource)
    monkeypatch.setattr(svc.res_repo, "create_version", _create_version)
    monkeypatch.setattr(svc.res_repo, "create_resource_item", _create_item)
    monkeypatch.setattr(svc.res_repo, "update_resource", _update_resource)

    out = await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)

    assert seen["file_path"] == "sb://chat-media/t42/ab/cd/deadbeef.png"
    assert out["id"] == 777
    # flag off -> legacy fs copy landed under teams/42/uploads/777/v1/
    dst_dir = tmp_path / "teams/42/uploads/777/v1"
    files = list(dst_dir.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == b"sb-source-bytes"
    assert updated["args"] == (
        "777",
        {"file_path": f"teams/42/uploads/777/v1/{files[0].name}"},
    )


@pytest.mark.asyncio
async def test_promote_flag_on_writes_object_store_destination(monkeypatch, tmp_path):
    """FEATURE_UNIFIED_STORAGE on + store_local_file succeeds -> resource
    file_path is an sb://library/... value, no fs copy lands."""
    import app.services.library.promote_generated_media_service as svc_mod
    from app.services.library.media_storage import StoredObject

    src = tmp_path / "teams/42/generations/abc/media.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"imgbytes")
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(svc_mod.settings, "FEATURE_UNIFIED_STORAGE", True)

    svc = svc_mod.PromoteGeneratedMediaService()
    _patch_scope_auth(monkeypatch, svc_mod)

    async def _get_by_id(gen_id):
        return _gen_row()

    async def _create_resource(data):
        return {"id": 555}

    async def _create_version(data):
        return {"id": 1}

    async def _create_item(data):
        return {"id": 2}

    updated = {}

    async def _update_resource(resource_id, data):
        updated["args"] = (resource_id, data)
        return {}

    async def _mark(gen_id, rid):
        return _gen_row(promoted_resource_id=str(rid))

    captured = {}

    async def fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
        assert Path(source_path).exists()
        captured["scope_id"] = scope_id
        captured["source_path"] = source_path
        return StoredObject(
            file_path=f"sb://library/t{scope_id}/ab/cd/{sha256}.png",
            size_bytes=Path(source_path).stat().st_size,
            sha256=sha256,
        )

    monkeypatch.setattr(svc_mod, "store_local_file", fake_store_local_file)
    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)
    monkeypatch.setattr(svc.gen_repo, "mark_promoted", _mark)
    monkeypatch.setattr(svc.res_repo, "create_resource", _create_resource)
    monkeypatch.setattr(svc.res_repo, "create_version", _create_version)
    monkeypatch.setattr(svc.res_repo, "create_resource_item", _create_item)
    monkeypatch.setattr(svc.res_repo, "update_resource", _update_resource)

    out = await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)

    assert out["id"] == 555
    assert captured["scope_id"] == 42
    assert updated["args"][1]["file_path"].startswith("sb://library/t42/")
    # No filesystem destination directory was ever created.
    assert not (tmp_path / "teams/42/uploads/555").exists()


@pytest.mark.asyncio
async def test_promote_flag_on_store_failure_raises_and_discards_row(
    monkeypatch, tmp_path
):
    """FEATURE_UNIFIED_STORAGE on and store_local_file raises -> the typed
    ObjectStoreWriteFailed escapes; no filesystem copy, no file_path
    update, no mark_promoted, and the resource row created ahead of the
    bytes is discarded again (2026-09-07 hard-fail)."""
    import app.services.library.promote_generated_media_service as svc_mod
    from app.services.library.storage_errors import ObjectStoreWriteFailed

    src = tmp_path / "teams/42/generations/abc/media.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"imgbytes")
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(svc_mod.settings, "FEATURE_UNIFIED_STORAGE", True)

    svc = svc_mod.PromoteGeneratedMediaService()
    _patch_scope_auth(monkeypatch, svc_mod)

    async def _get_by_id(gen_id):
        return _gen_row()

    async def _create_resource(data):
        return {"id": 555}

    calls = {"marked": False, "updated": False, "deleted": []}

    async def _mark(gen_id, rid):
        calls["marked"] = True
        return _gen_row(promoted_resource_id=str(rid))

    async def _update_resource(resource_id, data):
        calls["updated"] = True
        return {}

    async def _delete_resource(resource_id):
        calls["deleted"].append(str(resource_id))
        return True

    async def failing_store_local_file(**kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(svc_mod, "store_local_file", failing_store_local_file)
    monkeypatch.setattr(svc.gen_repo, "get_by_id", _get_by_id)
    monkeypatch.setattr(svc.gen_repo, "mark_promoted", _mark)
    monkeypatch.setattr(svc.res_repo, "create_resource", _create_resource)
    monkeypatch.setattr(svc.res_repo, "update_resource", _update_resource)
    monkeypatch.setattr(svc.res_repo, "delete_resource", _delete_resource)

    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await svc.promote(gen_id=7, user_id="u-uuid", target_scope_id=42)

    assert excinfo.value.details["where"] == "promote_generated_media"
    assert excinfo.value.details["resource_id"] == "555"
    assert calls == {"marked": False, "updated": False, "deleted": ["555"]}
    assert not (tmp_path / "teams/42/uploads").exists()


class TestGenerationParamsFromGeneratedMedia:
    def test_model_provider_and_whitelisted_params(self):
        from app.services.library.promote_generated_media_service import (
            generation_params_from_generated_media,
        )

        out = generation_params_from_generated_media(
            {
                "model": "seedream-4",
                "provider": "doubao",
                # cover-frame provenance is NOT a generation parameter
                "params": {
                    "seed": 7,
                    "size": "2K",
                    "filename": "x.png",
                    "cover_frame_timestamp_seconds": 1.5,
                },
            }
        )
        assert out == {
            "tool": "nous",
            "model": "seedream-4",
            "provider": "doubao",
            "seed": 7,
            "size": "2K",
        }

    def test_nothing_known_returns_none(self):
        from app.services.library.promote_generated_media_service import (
            generation_params_from_generated_media,
        )

        assert (
            generation_params_from_generated_media(
                {"model": "", "params": {"filename": "a"}}
            )
            is None
        )


def test_generation_params_ratio_aliases_to_aspect_ratio():
    """codex writes ``ratio``; the detail panel renders ``aspect_ratio``."""
    from app.services.library.promote_generated_media_service import (
        generation_params_from_generated_media,
    )

    out = generation_params_from_generated_media(
        {"model": "gpt-5.4", "provider": "codex", "params": {"ratio": "1:1"}}
    )
    assert out == {
        "tool": "nous",
        "model": "gpt-5.4",
        "provider": "codex",
        "aspect_ratio": "1:1",
    }
    # explicit aspect_ratio wins over the alias
    out = generation_params_from_generated_media(
        {"model": "m", "params": {"ratio": "1:1", "aspect_ratio": "16:9"}}
    )
    assert out["aspect_ratio"] == "16:9"
