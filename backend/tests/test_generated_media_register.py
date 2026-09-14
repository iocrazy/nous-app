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


@pytest.mark.asyncio
async def test_register_writes_source_asset_id_into_the_column(tmp_path, monkeypatch):
    """``GenerationOrigin.source_asset_id`` must reach the INSERT, not just the
    dataclass.

    ``GET /generated?source_asset_id=`` and the asset sheet's generation
    history both filter ``generated_media.source_asset_id`` — the COLUMN. A
    writer that only put the id in ``params`` produced rows neither of them
    could find, which is the defect this field was added to close.
    """
    import app.services.library.generated_media_service as gm

    src = tmp_path / "produced.png"
    src.write_bytes(b"\x89PNG")
    captured: dict = {}

    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path / "store"))
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(1, captured))

    await gm.register_generated_media(
        user_id="u1",
        scope_id=42,
        source_path=str(src),
        mime="image/png",
        origin=gm.GenerationOrigin(
            kind="canvas_run",
            source_asset_id=700000000000000001,
            params={"source_asset_id": "700000000000000001"},
        ),
    )

    assert captured["params"]["source_asset_id"] == 700000000000000001
    # And the params copy is still there — the two are not alternatives.
    assert captured["params"]["params"]["source_asset_id"] == "700000000000000001"


@pytest.mark.asyncio
async def test_register_leaves_the_column_null_when_no_asset_was_named(
    tmp_path, monkeypatch
):
    """The negative control for the test above: the column is bound on every
    insert, so "it was written" has to mean the VALUE arrived, not the key."""
    import app.services.library.generated_media_service as gm

    src = tmp_path / "produced.png"
    src.write_bytes(b"\x89PNG")
    captured: dict = {}

    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path / "store"))
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(1, captured))

    await gm.register_generated_media(
        user_id="u1",
        scope_id=42,
        source_path=str(src),
        mime="image/png",
        origin=gm.GenerationOrigin(kind="canvas_run"),
    )

    assert captured["params"]["source_asset_id"] is None


# ---------------------------------------------------------------------------
# TEXT 列收 int（2026-09-14 真栈）：分镜链的 run_id 是 int，agent 工具链是
# str，同一个登记口两种形状都会到。asyncpg 对 TEXT 列只接受 str：
#   DataError: invalid input for query argument $9: 349441401106307
#              (expected str, got int)
# 归一化的责任在咽喉点一处（「公共契约两侧都要遵守」），不是 17 个调用点。
# ---------------------------------------------------------------------------

#: 真栈里那次失败的 run（MH-96, 2026-09-14 10:01Z）。
_REAL_INT_RUN_ID = 349441401106307


def _text_columns() -> tuple[str, ...]:
    """模型里真正是 TEXT 的列名——手数一份清单会随迁移漂移。"""
    from sqlalchemy import Text

    from app.models import GeneratedMedia

    return tuple(
        c.name
        for c in GeneratedMedia.__table__.columns
        if isinstance(c.type, Text.__class__) or isinstance(c.type, Text)
    )


def _assert_every_text_bind_is_str(params: dict) -> None:
    for name in _text_columns():
        if name not in params:
            continue
        value = params[name]
        assert value is None or isinstance(value, str), (
            f"TEXT 列 {name} 收到 {type(value).__name__} {value!r}"
            " —— asyncpg 会拒绝整条 INSERT"
        )


@pytest.mark.asyncio
async def test_register_normalises_an_int_run_id_into_the_text_column(
    tmp_path, monkeypatch
):
    """DBOS 分镜链给的是 int run_id（``gateway.ledger_run_id``）。"""
    import app.services.library.generated_media_service as gm

    src = tmp_path / "gen.png"
    src.write_bytes(b"\x89PNG")
    captured: dict = {}

    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path / "store"))
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(1, captured))

    await gm.register_generated_media(
        user_id="u1",
        scope_id=42,
        source_path=str(src),
        mime="image/png",
        origin=gm.GenerationOrigin(
            kind="shot_generate",
            run_id=_REAL_INT_RUN_ID,
            node_id=349441401106999,
        ),
    )

    p = captured["params"]
    assert p["origin_run_id"] == str(_REAL_INT_RUN_ID)
    assert p["node_id"] == "349441401106999"
    _assert_every_text_bind_is_str(p)


@pytest.mark.asyncio
async def test_register_keeps_a_str_run_id_untouched(tmp_path, monkeypatch):
    """负向对照：agent 工具链给 str，归一化不许把它改成别的东西。"""
    import app.services.library.generated_media_service as gm

    src = tmp_path / "gen.png"
    src.write_bytes(b"\x89PNG")
    captured: dict = {}

    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path / "store"))
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(1, captured))

    await gm.register_generated_media(
        user_id="u1",
        scope_id=42,
        source_path=str(src),
        mime="image/png",
        origin=gm.GenerationOrigin(kind="agent_run", run_id="913402881190401"),
    )

    p = captured["params"]
    assert p["origin_run_id"] == "913402881190401"
    assert p["origin_kind"] == "agent_run"
    _assert_every_text_bind_is_str(p)


@pytest.mark.asyncio
async def test_register_does_not_mutate_the_caller_s_origin(tmp_path, monkeypatch):
    """归一化落在 INSERT 的值上，不回写调用方手里的 dataclass——
    调用方随后还要用 ``origin.run_id`` 去登记血缘。"""
    import app.services.library.generated_media_service as gm

    src = tmp_path / "gen.png"
    src.write_bytes(b"\x89PNG")
    captured: dict = {}

    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path / "store"))
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(1, captured))

    origin = gm.GenerationOrigin(kind="shot_generate", run_id=_REAL_INT_RUN_ID)
    await gm.register_generated_media(
        user_id="u1",
        scope_id=42,
        source_path=str(src),
        mime="image/png",
        origin=origin,
    )

    assert origin.run_id == _REAL_INT_RUN_ID
