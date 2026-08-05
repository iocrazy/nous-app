from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql


def _bind_params(stmt) -> dict:
    """Compile an insert(...).returning(...) statement (postgresql dialect)
    and return its literal bind values keyed by column name — the ORM
    equivalent of the old ``_fake_execute_returning_one(sql, params)``'s
    ``params`` dict."""
    return dict(stmt.compile(dialect=postgresql.dialect()).params)


class _FakeResult:
    def __init__(self, row: dict):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


def _fake_write_scope(row_id, captured: dict):
    """Patches app.db.session.write_scope: records the compiled bind params
    into ``captured["params"]`` and fabricates the RETURNING row as
    {"id": row_id, **params} — mirroring the pre-ORM mock's row shape."""

    @asynccontextmanager
    async def _scope():
        class _Session:
            async def execute(self, stmt):
                params = _bind_params(stmt)
                captured["params"] = params
                return _FakeResult({"id": row_id, **params})

        yield _Session()

    return _scope


@pytest.mark.asyncio
async def test_register_inserts_row_with_provenance(tmp_path, monkeypatch):
    import app.services.library.generated_media_service as gm

    async def _fake_download(dest_path, source_url, **k):
        from pathlib import Path

        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"img")
        return 3

    captured: dict = {}

    monkeypatch.setattr(gm, "_download_to", _fake_download)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(999, captured))

    origin = gm.GenerationOrigin(
        kind="canvas_run",
        run_id="r1",
        canvas_id=7,
        node_id="n1",
        prompt="a cat",
        model="m",
        provider="p",
        params={"size": "1024"},
    )
    row = await gm.register_generated_media(
        user_id="u-uuid",
        scope_id=42,
        source_url="http://x/y.png",
        mime="image/png",
        origin=origin,
    )
    assert row["id"] == 999
    p = captured["params"]
    assert p["scope_id"] == 42 and p["creator_id"] == "u-uuid"
    assert p["media_kind"] == "image" and p["origin_kind"] == "canvas_run"
    assert p["canvas_id"] == 7 and p["prompt"] == "a cat"
    assert "generations/" in p["file_path"]


@pytest.mark.asyncio
async def test_register_source_path_filesystem_copies_local_file(tmp_path, monkeypatch):
    """A local source_path (jimeng-cli output) is COPIED into Tier-1 (no download)."""
    from pathlib import Path

    import app.services.library.generated_media_service as gm

    # A real provider-produced file on disk.
    src = tmp_path / "produced.png"
    src.write_bytes(b"\x89PNG-local-bytes")

    captured: dict = {}

    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path / "store"))
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(1, captured))

    row = await gm.register_generated_media(
        user_id="u1",
        scope_id=42,
        source_path=str(src),
        mime="image/png",
        origin=gm.GenerationOrigin(kind="shot_generate", node_id="shot9"),
    )
    assert row["id"] == 1
    rel = captured["params"]["file_path"]
    assert "generations/" in rel
    # The bytes were actually copied into the store (real _copy_local_to).
    copied = Path(str(tmp_path / "store"), rel)
    assert copied.read_bytes() == b"\x89PNG-local-bytes"
    assert captured["params"]["file_size_bytes"] == len(b"\x89PNG-local-bytes")


@pytest.mark.asyncio
async def test_register_source_path_object_store_reads_file(tmp_path, monkeypatch):
    """Flag on → the local file is content-addressed into the object store."""
    import app.services.library.generated_media_service as gm

    src = tmp_path / "produced.png"
    src.write_bytes(b"objbytes")

    async def _fake_local_object_store(*, scope_id, source_path, mime, kind):
        assert source_path == str(src)  # reads the file, does not download
        return ("sb://chat-media/key.png", 8, "deadbeefsha")

    captured: dict = {}

    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)
    monkeypatch.setattr(
        gm, "_write_local_generation_to_object_store", _fake_local_object_store
    )
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(2, captured))

    row = await gm.register_generated_media(
        user_id="u1",
        scope_id=7,
        source_path=str(src),
        mime="image/png",
        origin=gm.GenerationOrigin(kind="shot_generate"),
    )
    assert row["id"] == 2
    assert captured["params"]["file_path"] == "sb://chat-media/key.png"
    assert captured["params"]["content_sha256"] == "deadbeefsha"


@pytest.mark.asyncio
async def test_register_requires_exactly_one_source(monkeypatch):
    import app.services.library.generated_media_service as gm

    origin = gm.GenerationOrigin(kind="shot_generate")
    with pytest.raises(ValueError):
        await gm.register_generated_media(
            user_id="u", scope_id=1, mime="image/png", origin=origin
        )  # neither
    with pytest.raises(ValueError):
        await gm.register_generated_media(
            user_id="u",
            scope_id=1,
            source_url="http://x/y.png",
            source_path="/tmp/x.png",
            mime="image/png",
            origin=origin,
        )  # both
