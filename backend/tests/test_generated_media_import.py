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

    async def _fake_canvas_scope(_auth, canvas_id):
        captured["canvas_scope_for"] = canvas_id
        return 77

    async def _fake_register(**kwargs):
        captured.update(kwargs)
        return {"id": 999}

    monkeypatch.setattr(router_mod, "_scope", _fake_scope)
    monkeypatch.setattr(router_mod, "_canvas_import_scope", _fake_canvas_scope)
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
    assert captured["scope_id"] == 77
    assert captured["mime"] == "image/png"
    assert captured["origin"].kind == "canvas_upload"
    assert captured["origin"].canvas_id == 123
    assert captured["origin"].node_id == "prompt-1"
    # No explicit role → the visible default. A plain upload must never be
    # classified as an intermediate.
    assert captured["origin"].params == {"filename": "pic.png", "role": "user_upload"}
    # The temp file existed at register time — source_path was passed.
    assert captured["source_path"]
    assert captured["canvas_scope_for"] == 123


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
    assert "canvas_scope_for" not in captured
    assert captured["scope_id"] == 42


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


@pytest.mark.asyncio
async def test_canvas_import_scope_gates_the_canvas_then_resolves_its_scope(
    monkeypatch,
):
    import sys

    import app.workflows.canvas_generation as cg

    router_mod = _router_mod()
    calls = []

    async def _fake_gate(canvas_id, auth):
        calls.append(("gate", canvas_id))
        return "777"

    async def _fake_registration_scope(canvas_id, user_id):
        calls.append(("scope", canvas_id, user_id))
        return 77

    monkeypatch.setattr(
        sys.modules["app.api.canvases_router"], "_gate_canvas_write", _fake_gate
    )
    monkeypatch.setattr(cg, "_registration_scope_id", _fake_registration_scope)

    assert await router_mod._canvas_import_scope(_Auth(), 123) == 77
    assert calls == [("gate", "123"), ("scope", 123, "u-uuid")]


@pytest.mark.asyncio
async def test_canvas_import_scope_unresolved_is_500(monkeypatch):
    import sys

    from fastapi import HTTPException

    import app.workflows.canvas_generation as cg

    router_mod = _router_mod()

    async def _fake_gate(canvas_id, auth):
        return "777"

    async def _unresolved(canvas_id, user_id):
        raise RuntimeError("scope_unresolved")

    monkeypatch.setattr(
        sys.modules["app.api.canvases_router"], "_gate_canvas_write", _fake_gate
    )
    monkeypatch.setattr(cg, "_registration_scope_id", _unresolved)

    with pytest.raises(HTTPException) as caught:
        await router_mod._canvas_import_scope(_Auth(), 123)
    assert caught.value.status_code == 500
