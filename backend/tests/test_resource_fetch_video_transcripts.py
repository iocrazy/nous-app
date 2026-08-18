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

import datetime as _datetime
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import ResourceSummaries, ResourceTranscripts, TaskTracking

RID = 331438000000001


def _dt(year: int, month: int, day: int) -> _datetime.datetime:
    return _datetime.datetime(year, month, day, tzinfo=_datetime.timezone.utc)


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
CREATE TABLE task_tracking (
    dbos_workflow_id TEXT PRIMARY KEY,
    user_id TEXT,
    resource_id TEXT,
    task_type TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,
    phase TEXT,
    metadata TEXT
);
"""


@pytest.fixture()
async def sqlite_session_factory():
    async for factory in _make_factory(_CREATE_SQL):
        yield factory


@pytest.fixture()
async def multirow_session_factory():
    """Same two tables with ``UNIQUE(resource_id)`` dropped.

    Today both tables carry that constraint, so more than one row per
    resource is unreachable — which is exactly why the "newest wins"
    ordering cannot be exercised against the real schema. This fixture
    simulates a future relaxation of the constraint so the ordering is
    pinned by a test that actually fails if it regresses, rather than
    living only in a comment.
    """
    async for factory in _make_factory(_CREATE_SQL.replace(" UNIQUE", "")):
        yield factory


async def _make_factory(ddl_sql: str):
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        for ddl in ddl_sql.strip().split(";"):
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


@pytest.mark.asyncio
async def test_transcript_multiple_rows_returns_the_newest(
    multirow_session_factory,
):
    """Defence in depth: if UNIQUE(resource_id) is ever relaxed, the branch
    must return the most recent row — not raise MultipleResultsFound (which
    would degrade the whole fetch to a generic "fetch failed")."""
    factory = multirow_session_factory
    async with factory() as s:
        for text, when in (
            ("stale transcript", _dt(2026, 1, 1)),
            ("newest transcript", _dt(2026, 6, 1)),
            ("middle transcript", _dt(2026, 3, 1)),
        ):
            await s.execute(
                ResourceTranscripts.__table__.insert().values(
                    id=uuid.uuid4(),
                    resource_id=RID,
                    full_text=text,
                    created_at=when,
                )
            )
        await s.commit()
    out = await _call_video_branch(factory, mime="audio/mpeg")
    assert out.get("content") == "newest transcript"


@pytest.mark.asyncio
async def test_summary_multiple_rows_returns_the_newest(multirow_session_factory):
    factory = multirow_session_factory
    async with factory() as s:
        for text, when in (
            ("newest gist", _dt(2026, 6, 1)),
            ("stale gist", _dt(2026, 1, 1)),
        ):
            await s.execute(
                ResourceSummaries.__table__.insert().values(
                    id=uuid.uuid4(),
                    resource_id=RID,
                    summary_text=text,
                    created_at=when,
                )
            )
        await s.commit()
    out = await _call_video_branch(factory, mime="video/mp4", mode="summary")
    assert out.get("content") == "newest gist"


@pytest.mark.asyncio
async def test_null_created_at_does_not_outrank_a_timestamped_row(
    multirow_session_factory,
):
    """``ORDER BY created_at DESC`` puts NULLs FIRST on Postgres, which would
    let a row with no timestamp beat a real one. Both tables default
    created_at to now(), so a NULL only arrives via a direct backfill — pin
    NULLS LAST so "newest" still means the newest known timestamp."""
    factory = multirow_session_factory
    async with factory() as s:
        for text, when in (("no timestamp", None), ("dated", _dt(2026, 1, 1))):
            await s.execute(
                ResourceTranscripts.__table__.insert().values(
                    id=uuid.uuid4(),
                    resource_id=RID,
                    full_text=text,
                    created_at=when,
                )
            )
        await s.commit()
    out = await _call_video_branch(factory, mime="audio/mpeg")
    assert out.get("content") == "dated"


def test_dead_videos_table_reference_is_gone():
    import inspect

    from app.services.ai.tools import resource_fetch_tool as mod

    src = inspect.getsource(mod)
    assert (
        "FROM public.videos" not in src
    ), "the phantom public.videos table must not be queried"


@pytest.mark.asyncio
async def test_a_running_task_turns_not_available_into_being_generated(
    sqlite_session_factory,
):
    """End-to-end on a real engine: no transcript row, no intermediate value
    in the status column (production never writes one) — the "still working
    on it" answer has to come from an active task_tracking row.

    Runs the actual ORM statement rather than a stubbed reducer, so a
    predicate that does not compile or does not match is a red test.
    """
    factory = sqlite_session_factory
    async with factory() as s:
        await s.execute(
            TaskTracking.__table__.insert().values(
                dbos_workflow_id="wf-1",
                user_id=uuid.uuid4(),
                resource_id=str(RID),
                task_type="extract_audio",
                title="Audio clip",
                status="processing",
                phase="in_progress",
                # Only a chaining run ends in a transcript — see the
                # non-chaining twin below.
                metadata={"chain_transcription": True},
            )
        )
        await s.commit()

    out = await _call_video_branch(factory, mime="audio/mpeg", mode="transcript")
    assert "being generated" in out["error"]
    assert "not available" not in out["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [{"chain_transcription": False}, {}, None],
    ids=["false", "empty", "null"],
)
async def test_a_non_chaining_extract_audio_does_not_promise_a_transcript(
    sqlite_session_factory, metadata
):
    """The post-download auto-extract and the "Extract Audio" button create
    this same task_type without chain_transcription, so no transcript
    follows unless the resource carries intent tags. Telling the agent
    "being generated; ask the user to retry shortly" there sends it —
    and the user — to wait for something that never arrives.

    The three parameters are the shapes really present in the table: the
    recorded false, an empty metadata object, and every row written before
    the field existed.
    """
    factory = sqlite_session_factory
    async with factory() as s:
        await s.execute(
            TaskTracking.__table__.insert().values(
                dbos_workflow_id="wf-audio-only",
                user_id=uuid.uuid4(),
                resource_id=str(RID),
                task_type="extract_audio",
                title="Audio clip",
                status="processing",
                phase="in_progress",
                metadata=metadata,
            )
        )
        await s.commit()

    out = await _call_video_branch(factory, mime="audio/mpeg", mode="transcript")
    assert out == {"error": "transcript not available; resource not yet processed"}


@pytest.mark.asyncio
async def test_a_finished_task_leaves_the_not_available_wording_alone(
    sqlite_session_factory,
):
    factory = sqlite_session_factory
    async with factory() as s:
        await s.execute(
            TaskTracking.__table__.insert().values(
                dbos_workflow_id="wf-2",
                user_id=uuid.uuid4(),
                resource_id=str(RID),
                task_type="ai_transcription",
                title="Transcribe clip",
                status="completed",
                phase="completed",
            )
        )
        await s.commit()

    out = await _call_video_branch(factory, mime="audio/mpeg", mode="transcript")
    assert out == {"error": "transcript not available; resource not yet processed"}
