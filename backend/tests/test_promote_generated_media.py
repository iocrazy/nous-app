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
        "promoted_resource_id": None,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_promote_creates_resource_and_marks(monkeypatch, tmp_path):
    import app.services.library.promote_generated_media_service as svc_mod

    # source file on disk
    src = tmp_path / "teams/42/generations/abc/media.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"imgbytes")
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))

    svc = svc_mod.PromoteGeneratedMediaService()

    async def _get(gen_id, scope_id):
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

    monkeypatch.setattr(svc.gen_repo, "get", _get)
    monkeypatch.setattr(svc.gen_repo, "mark_promoted", _mark)
    monkeypatch.setattr(svc.res_repo, "create_resource", _create_resource)
    monkeypatch.setattr(svc.res_repo, "create_version", _create_version)
    monkeypatch.setattr(svc.res_repo, "create_resource_item", _create_item)
    monkeypatch.setattr(svc.res_repo, "update_resource", _update_resource)

    out = await svc.promote(gen_id=7, user_id="u-uuid", scope_id=42)
    assert out["id"] == 555
    assert created["resource"]["source_type"] == "generated"
    assert created["resource"]["creator_id"] == "u-uuid"
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
    import sys
    import types

    import app.services.library.promote_generated_media_service as svc_mod

    # source file on disk (same setup as happy-path test)
    src = tmp_path / "teams/42/generations/abc/media.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"imgbytes")
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))

    svc = svc_mod.PromoteGeneratedMediaService()

    async def _get(gen_id, scope_id):
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

    monkeypatch.setattr(svc.gen_repo, "get", _get)
    monkeypatch.setattr(svc.gen_repo, "mark_promoted", _mark)
    monkeypatch.setattr(svc.res_repo, "create_resource", _create_resource)
    monkeypatch.setattr(svc.res_repo, "create_version", _create_version)
    monkeypatch.setattr(svc.res_repo, "create_resource_item", _create_item)
    monkeypatch.setattr(svc.res_repo, "update_resource", _update_resource_canvas)

    # capture canvas_resource_refs insert via a fake app.db.engine
    db_calls = []

    async def _execute_as_service_role(sql, params):
        db_calls.append(params)

    fake_engine = types.SimpleNamespace(
        execute_as_service_role=_execute_as_service_role
    )
    fake_db_mod = types.ModuleType("app.db")
    fake_db_mod.engine = fake_engine
    monkeypatch.setitem(sys.modules, "app.db", fake_db_mod)

    out = await svc.promote(gen_id=7, user_id="u-uuid", scope_id=42)

    # resource created normally
    assert out["id"] == 555

    # canvas_resource_refs INSERT called exactly once with expected params
    assert len(db_calls) == 1
    assert db_calls[0]["cid"] == 99
    assert db_calls[0]["rid"] == 555
    assert db_calls[0]["nid"] == "n1"


@pytest.mark.asyncio
async def test_promote_idempotent(monkeypatch):
    import app.services.library.promote_generated_media_service as svc_mod

    svc = svc_mod.PromoteGeneratedMediaService()

    async def _get(gen_id, scope_id):
        return _gen_row(promoted_resource_id="900")

    async def _get_res(resource_id):
        return {"id": int(resource_id)}

    monkeypatch.setattr(svc.gen_repo, "get", _get)
    monkeypatch.setattr(svc.res_repo, "get_resource_by_id", _get_res)

    out = await svc.promote(gen_id=7, user_id="u", scope_id=42)
    assert str(out["id"]) == "900"  # returns existing, no re-create


@pytest.mark.asyncio
async def test_promote_missing_source_raises(monkeypatch, tmp_path):
    import app.services.library.promote_generated_media_service as svc_mod

    # DOWNLOAD_PATH points to tmp_path but the source file is NOT created
    monkeypatch.setattr(svc_mod.settings, "DOWNLOAD_PATH", str(tmp_path))

    svc = svc_mod.PromoteGeneratedMediaService()

    async def _get(gen_id, scope_id):
        # file_path references a file that does not exist on disk
        return _gen_row(file_path="teams/42/generations/missing/media.png")

    monkeypatch.setattr(svc.gen_repo, "get", _get)

    with pytest.raises(ValueError, match="generation file missing"):
        await svc.promote(gen_id=7, user_id="u-uuid", scope_id=42)
