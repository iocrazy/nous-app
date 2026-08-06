"""ResourceFetch video/audio branch reads resource_summaries/_transcripts.

The pre-2026-08 implementation joined ``public.videos`` — a table renamed
away by migration 066 — so every video/audio fetch raised and degraded to
"fetch failed" for months (see PR for the behaviour-change disclosure).
These tests pin the repaired branch end-to-end on a real engine: a
transcript/summary row inserted for the resource is returned as content,
and a missing row yields the typed "not available" error instead of an
exception.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import ResourceSummaries, ResourceTranscripts

RID = 331438000000001

# Hand-written SQLite DDL: the real models carry PG-only DDL (JSONB columns,
# gen_random_uuid() server defaults, schema "public") that the SQLite
# compiler cannot render — same approach as test_scheduled_master_row_shape_e2e.
_CREATE_SQL = """
CREATE TABLE resource_transcripts (
    id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
    resource_id BIGINT NOT NULL UNIQUE,
    language TEXT,
    full_text TEXT,
    segments TEXT,
    whisper_model TEXT,
    duration_seconds REAL,
    created_at TEXT
);
CREATE TABLE resource_summaries (
    id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
    resource_id BIGINT NOT NULL UNIQUE,
    summary_type TEXT,
    summary_text TEXT,
    key_points TEXT,
    topics TEXT,
    llm_model TEXT,
    llm_provider TEXT,
    created_at TEXT
);
"""


@pytest.fixture()
async def sqlite_session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        for ddl in _CREATE_SQL.strip().split(";"):
            if ddl.strip():
                await conn.exec_driver_sql(ddl)
    yield lambda: AsyncSession(engine, expire_on_commit=False)
    await engine.dispose()


def _patched_read_scope(factory):
    @asynccontextmanager
    async def _read_scope():
        session = factory()
        try:
            yield session
        finally:
            await session.close()

    return _read_scope


async def _call_video_branch(factory, *, mime: str, mode=None):
    """Drive _fetch_dispatch past the access check straight to the branch."""
    from app.services.ai.tools import resource_fetch_tool as mod

    row = {"id": str(RID), "mime": mime, "name": "clip.mp4", "file_path": ""}

    async def _fake_execute(stmt):  # access-check row
        class _R:
            @staticmethod
            def mappings():
                class _M:
                    @staticmethod
                    def first():
                        return row

                return _M()

        return _R()

    class _AccessSession:
        execute = staticmethod(_fake_execute)

        async def close(self):
            pass

    calls = {"n": 0}
    real = _patched_read_scope(factory)

    @asynccontextmanager
    async def _switching_read_scope():
        calls["n"] += 1
        if calls["n"] == 1:  # first read_scope = access check
            yield _AccessSession()
        else:  # subsequent = content lookup, real engine
            async with real() as s:
                yield s

    with patch("app.db.session.read_scope", _switching_read_scope):
        return await mod._fetch_dispatch(
            resource_id=str(RID), user_id="u1", mode=mode, args=None
        )


@pytest.mark.asyncio
async def test_transcript_row_is_returned_as_content(sqlite_session_factory):
    factory = sqlite_session_factory
    async with factory() as s:
        await s.execute(
            ResourceTranscripts.__table__.insert().values(
                id=uuid.uuid4(),
                resource_id=RID,
                full_text="hello transcript",
                language="en",
            )
        )
        await s.commit()
    out = await _call_video_branch(factory, mime="audio/mpeg")
    assert out.get("content") == "hello transcript"
    assert out["meta"]["mode"] == "transcript"


@pytest.mark.asyncio
async def test_summary_row_is_returned_as_content(sqlite_session_factory):
    factory = sqlite_session_factory
    async with factory() as s:
        await s.execute(
            ResourceSummaries.__table__.insert().values(
                id=uuid.uuid4(), resource_id=RID, summary_text="the gist"
            )
        )
        await s.commit()
    out = await _call_video_branch(factory, mime="video/mp4", mode="summary")
    assert out.get("content") == "the gist"
    assert out["meta"]["mode"] == "summary"


@pytest.mark.asyncio
async def test_missing_rows_yield_typed_not_available_errors(
    sqlite_session_factory,
):
    factory = sqlite_session_factory
    out = await _call_video_branch(factory, mime="video/mp4", mode="summary")
    assert "summary not available" in out["error"]
    out = await _call_video_branch(factory, mime="audio/mpeg", mode="transcript")
    assert "transcript not available" in out["error"]


def test_dead_videos_table_reference_is_gone():
    import inspect

    from app.services.ai.tools import resource_fetch_tool as mod

    src = inspect.getsource(mod)
    assert (
        "FROM public.videos" not in src
    ), "the phantom public.videos table must not be queried"
