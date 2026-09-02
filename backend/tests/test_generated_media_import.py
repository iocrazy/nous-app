"""POST /generated-media/import — the smart canvas Upload node's ingest path.

Function-level tests (no TestClient): call import_generation directly with a
stub UploadFile and monkeypatched register_generated_media, mirroring the
admin-endpoint test convention.
"""

import pytest


class _StubUpload:
    def __init__(self, chunks, content_type="image/png", filename="pic.png"):
        self._chunks = list(chunks)
        self.content_type = content_type
        self.filename = filename

    async def read(self, _size):
        return self._chunks.pop(0) if self._chunks else b""


class _Auth:
    user_id = "u-uuid"


def _router_mod():
    # `app.api.__init__` rebinds the name `generated_media_router` to the
    # APIRouter instance, shadowing the module on `import ... as` — go
    # through sys.modules for the real module object.
    import importlib

    return importlib.import_module("app.api.generated_media_router")


def _patch_scope_and_register(monkeypatch, captured):
    import app.services.library.generated_media_service as gm

    router_mod = _router_mod()

    async def _fake_scope(_auth):
        return 42

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": 999}

    monkeypatch.setattr(router_mod, "_scope", _fake_scope)
    monkeypatch.setattr(gm, "register_generated_media", _fake_register)


@pytest.mark.asyncio
async def test_import_image_registers_and_returns_cover_url(monkeypatch):
    from app.api.generated_media_router import import_generation

    captured = {}
    _patch_scope_and_register(monkeypatch, captured)

    resp = await import_generation(
        _Auth(),
        file=_StubUpload([b"img-bytes"]),
        canvas_id="123",
        node_id="prompt-1",
        role=None,
    )
    data = resp["data"]
    assert data == {
        "id": "999",
        "url": "/api/v1/generated-media/999/cover",
        "media_kind": "image",
        "mime": "image/png",
    }
    assert captured["scope_id"] == 42
    assert captured["mime"] == "image/png"
    assert captured["origin"].kind == "canvas_upload"
    assert captured["origin"].canvas_id == 123
    assert captured["origin"].node_id == "prompt-1"
    # No explicit role → the visible default. A plain upload must never be
    # classified as an intermediate.
    assert captured["origin"].params == {"filename": "pic.png", "role": "user_upload"}
    # The temp file existed at register time — source_path was passed.
    assert captured["source_path"]


@pytest.mark.asyncio
async def test_import_video_returns_stream_url(monkeypatch):
    from app.api.generated_media_router import import_generation

    captured = {}
    _patch_scope_and_register(monkeypatch, captured)

    resp = await import_generation(
        _Auth(),
        file=_StubUpload([b"vid"], content_type="video/mp4", filename="clip.mp4"),
        canvas_id=None,
        node_id=None,
        role=None,
    )
    assert resp["data"]["url"] == "/api/v1/generated-media/999/stream"
    assert resp["data"]["media_kind"] == "video"
    assert captured["origin"].canvas_id is None


@pytest.mark.asyncio
async def test_import_rejects_non_media_mime():
    from fastapi import HTTPException

    from app.api.generated_media_router import import_generation

    with pytest.raises(HTTPException) as exc:
        await import_generation(
            _Auth(), file=_StubUpload([b"x"], content_type="application/pdf")
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_import_rejects_oversize_and_bad_canvas_id(monkeypatch):
    from fastapi import HTTPException

    router_mod = _router_mod()
    import_generation = router_mod.import_generation

    monkeypatch.setattr(router_mod, "IMPORT_MAX_BYTES", 4)
    with pytest.raises(HTTPException) as exc:
        await import_generation(
            _Auth(),
            file=_StubUpload([b"12345"]),
            canvas_id=None,
            node_id=None,
            role=None,
        )
    assert exc.value.status_code == 413

    with pytest.raises(HTTPException) as exc2:
        await import_generation(
            _Auth(),
            file=_StubUpload([b"x"]),
            canvas_id="not-a-number",
            node_id=None,
            role=None,
        )
    assert exc2.value.status_code == 400


@pytest.mark.asyncio
async def test_import_rejects_empty_file(monkeypatch):
    from fastapi import HTTPException

    from app.api.generated_media_router import import_generation

    captured = {}
    _patch_scope_and_register(monkeypatch, captured)
    with pytest.raises(HTTPException) as exc:
        await import_generation(
            _Auth(), file=_StubUpload([]), canvas_id=None, node_id=None, role=None
        )
    assert exc.value.status_code == 400


# ─── Role sub-classification (canvas_upload is three different things) ───────


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["mask", "brush"])
async def test_import_stamps_the_role_the_caller_sent(monkeypatch, role):
    """The canvas editors bake masks / brush composites and upload them
    through this very endpoint. Without the role they land in the inbox
    looking exactly like a file the user chose."""
    from app.api.generated_media_router import import_generation

    captured = {}
    _patch_scope_and_register(monkeypatch, captured)

    await import_generation(
        _Auth(),
        file=_StubUpload([b"png"], filename=f"{role}.png"),
        canvas_id="123",
        node_id="out-1",
        role=role,
    )
    assert captured["origin"].params["role"] == role


@pytest.mark.asyncio
async def test_import_rejects_an_unknown_role(monkeypatch):
    """A typo'd role must be a 400, not a silently visible upload.

    Storing it would produce a row no filter ever matches, behaving exactly
    like the missing classification it is a misspelling of.
    """
    from fastapi import HTTPException

    from app.api.generated_media_router import import_generation

    captured = {}
    _patch_scope_and_register(monkeypatch, captured)

    with pytest.raises(HTTPException) as exc:
        await import_generation(_Auth(), file=_StubUpload([b"png"]), role="maks")
    assert exc.value.status_code == 400
    assert "maks" in str(exc.value.detail)
    assert captured == {}  # nothing was registered
