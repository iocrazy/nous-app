"""Vision injection tests for conversation_agent_turn.

Pins the contract that lets group-chat agents SEE uploaded images:
  - image bodies render as "[image: alt]" placeholders (never raw dicts)
  - _inject_image_blocks turns image messages into multimodal content when
    the model supports vision (data URL from local storage bytes)
  - text-only models keep placeholders (no injection)
  - cross-conversation media ids are skipped (exfiltration guard)
  - oversize files are skipped
"""

from __future__ import annotations

import uuid as _uuid_mod
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import insert as _sa_insert
from sqlalchemy import select as _sa_select
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

from app.models import GeneratedMedia
from app.services.chat.conversation_agent_turn import (
    _MAX_VISION_IMAGE_BYTES,
    _build_history,
    _inject_image_blocks,
    _render_body,
    _vision_image_lookup_stmt,
)

CONV_ID = 42


def _fake_read_scope_returning(row: dict | None):
    """ORM equivalent of the old ``db_engine.fetch_one`` mock (Phase B5
    Task 1) — patches ``conversation_agent_turn.read_scope`` so
    ``_inject_image_blocks``'s column-level select resolves to ``row`` via
    ``.mappings().first()``."""

    class _FakeResult:
        def mappings(self):
            return self

        def first(self):
            return row

    class _FakeSession:
        async def execute(self, stmt):
            return _FakeResult()

    @asynccontextmanager
    async def _scope():
        yield _FakeSession()

    return _scope


def _image_msg(gm_id: int = 7, alt: str = "photo.png") -> dict:
    return {
        "sender_type": "user",
        "type": "image",
        "body": {"kind": "image", "generated_media_id": gm_id, "alt": alt},
    }


def _text_msg(text: str = "hello") -> dict:
    return {"sender_type": "user", "type": "text", "body": {"text": text}}


# ── placeholder rendering ────────────────────────────────────────────────────


def test_image_body_renders_placeholder_not_raw_dict() -> None:
    assert (
        _render_body({"kind": "image", "alt": "cat.png"}, "image") == "[image: cat.png]"
    )
    assert _render_body({"kind": "image"}, "image") == "[image]"


# ── injection ────────────────────────────────────────────────────────────────


def _media_row(tmp_path, name="img.png", data=b"\x89PNG fake", conv=CONV_ID, size=None):
    f = tmp_path / name
    f.write_bytes(data)
    return {
        "file_path": name,
        "mime": "image/png",
        "file_size_bytes": size if size is not None else len(data),
        "conversation_id": conv,
    }


@pytest.mark.asyncio
async def test_injects_data_url_for_vision_model(tmp_path) -> None:
    recent = [_text_msg(), _image_msg()]
    history = _build_history(recent)
    row = _media_row(tmp_path)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.read_scope",
            new=_fake_read_scope_returning(row),
        ),
        patch("app.services.chat.conversation_agent_turn.settings") as mock_settings,
    ):
        mock_settings.DOWNLOAD_PATH = str(tmp_path)
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 1
    content = history[1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "[image: photo.png]"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # text message untouched
    assert history[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_text_only_model_keeps_placeholders() -> None:
    recent = [_image_msg()]
    history = _build_history(recent)
    with patch(
        "app.services.chat.conversation_agent_turn.model_supports_vision",
        new=AsyncMock(return_value=False),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-lite",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0
    assert history[0]["content"] == "[image: photo.png]"


@pytest.mark.asyncio
async def test_cross_conversation_media_is_skipped(tmp_path) -> None:
    """An id-swapped body pointing at another conversation's media is refused."""
    recent = [_image_msg()]
    history = _build_history(recent)
    row = _media_row(tmp_path, conv=CONV_ID + 1)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.read_scope",
            new=_fake_read_scope_returning(row),
        ),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0
    assert history[0]["content"] == "[image: photo.png]"


@pytest.mark.asyncio
async def test_oversize_file_is_skipped(tmp_path) -> None:
    recent = [_image_msg()]
    history = _build_history(recent)
    row = _media_row(tmp_path, size=_MAX_VISION_IMAGE_BYTES + 1)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.read_scope",
            new=_fake_read_scope_returning(row),
        ),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0


@pytest.mark.asyncio
async def test_missing_media_row_is_skipped() -> None:
    recent = [_image_msg()]
    history = _build_history(recent)
    with (
        patch(
            "app.services.chat.conversation_agent_turn.model_supports_vision",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.chat.conversation_agent_turn.read_scope",
            new=_fake_read_scope_returning(None),
        ),
    ):
        n = await _inject_image_blocks(
            history,
            recent,
            model="doubao-seed-2-0",
            provider=None,
            conversation_id=CONV_ID,
        )
    assert n == 0


# ── Real-aiosqlite row-shape regression (B5 review leftover — deferred
# minors batch, Minor 4) ─────────────────────────────────────────────────
#
# Every test above fakes the Result (_fake_read_scope_returning), so the B4
# row-shape bug class has no coverage for _vision_image_lookup_stmt's own
# fetch->consume chain (``.mappings().first()`` -> ``row.get(...)`` in
# _inject_image_blocks). Mirrors tests/test_orm_b5_task1_row_shape_e2e.py's
# positive/negative-control pair with a genuine aiosqlite engine.

_GENERATED_MEDIA_DDL = """
CREATE TABLE generated_media (
    id INTEGER PRIMARY KEY, scope_id INTEGER, creator_id TEXT,
    media_kind TEXT, mime TEXT, file_path TEXT, file_size_bytes INTEGER,
    content_sha256 TEXT, origin_kind TEXT, origin_run_id TEXT,
    agent_id TEXT, canvas_id INTEGER, node_id TEXT, prompt TEXT,
    model TEXT, provider TEXT, params TEXT, cost_cents REAL,
    parent_resource_id INTEGER, derivation_kind TEXT,
    promoted_resource_id INTEGER, review_state TEXT, source_asset_id INTEGER,
    conversation_id INTEGER,
    created_at TIMESTAMP
)
"""


async def _seeded_generated_media_engine():
    engine = _create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_GENERATED_MEDIA_DDL)
        await conn.execute(
            _sa_insert(GeneratedMedia.__table__).values(
                id=7,
                scope_id=1,
                creator_id=_uuid_mod.uuid4(),
                media_kind="image",
                mime="image/png",
                file_path="teams/1/chat/x/photo.png",
                file_size_bytes=1234,
                origin_kind="chat_upload",
                params={},
                conversation_id=CONV_ID,
            )
        )
    return engine


@pytest.mark.asyncio
async def test_vision_image_lookup_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (``_vision_image_lookup_stmt``, imported
    — not reconstructed here) round-tripped through a genuine aiosqlite
    ``Result`` gives a column-keyed RowMapping matching
    ``_inject_image_blocks``'s ``row.get(...)`` reads."""
    engine = await _seeded_generated_media_engine()
    sessionmaker = _async_sessionmaker(
        engine, class_=_AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            row = (
                (await session.execute(_vision_image_lookup_stmt(7))).mappings().first()
            )
    finally:
        await engine.dispose()

    assert row is not None
    assert row.get("file_path") == "teams/1/chat/x/photo.png"
    assert row.get("mime") == "image/png"
    assert row.get("file_size_bytes") == 1234
    assert str(row.get("conversation_id")) == str(CONV_ID)


@pytest.mark.asyncio
async def test_vision_image_lookup_stmt_entity_level_negative_control_proves_sensitivity():
    """Negative control: the KNOWN-BAD ``select(GeneratedMedia)`` form
    (entity-level), executed against the exact same real table/row, must
    produce the wrong shape — proving the test above actually distinguishes
    correct from broken."""
    engine = await _seeded_generated_media_engine()
    sessionmaker = _async_sessionmaker(
        engine, class_=_AsyncSession, expire_on_commit=False
    )
    try:
        bad_stmt = _sa_select(GeneratedMedia).where(GeneratedMedia.id == 7)
        async with sessionmaker() as session:
            bad_row = (await session.execute(bad_stmt)).mappings().first()
    finally:
        await engine.dispose()

    assert bad_row is not None
    assert list(bad_row.keys()) == ["GeneratedMedia"]  # entity-keyed
    assert bad_row.get("file_path") is None  # _inject_image_blocks's read would break
