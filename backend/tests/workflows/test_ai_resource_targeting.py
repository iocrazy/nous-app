"""The transcription / analysis workflows write to the resource the
dispatcher chose, never to "some resource of this media".

``parsed_media`` is shared: one row per platform id, whoever parsed it, and
several users can each hold a resource of the same video. The workflows used
to resolve their target with ``parsed_media JOIN resources LIMIT 1`` (and the
status flips with ``WHERE media_id = …``), so user B's transcription landed
on — and flipped — user A's row. Now:

- dispatchers pass ``resource_id`` into the workflow input (DBOS freezes
  inputs at dispatch, so it has to be there);
- a workflow recorded before that argument existed falls back to the
  initiating user's OWN resource, and with no initiator it raises;
- every write of the run (transcript target, completed/failed status,
  follow-up summary lookup) keys on that one resource id.

The statements run against real SQLite rows (two holders of one media), so
"picks the right row" is observed, not inferred from compiled SQL.
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import ParsedMedia, Resources
from app.workflows import ai_transcription as at
from app.workflows.analyze_l1 import _analyze_resource_lookup_stmt

pytestmark = pytest.mark.unit

MEDIA_ID = 1
OWNER_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
OWNER_B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
STRANGER = uuid.UUID("cccccccc-0000-0000-0000-000000000003")
RES_A = 7_300_000_000_000_000_101
# B's resource is the OLDER one: an unfiltered "earliest resource of the
# media" fallback would pick it for A.
RES_B = 7_300_000_000_000_000_050


def _ddl(table) -> str:
    def affinity(col) -> str:
        name = type(col.type).__name__
        if name in ("Integer", "BigInteger", "SmallInteger", "Boolean"):
            return "INTEGER"
        if name in ("Numeric", "Double", "Float"):
            return "REAL"
        if name in ("DateTime", "Date"):
            return "TIMESTAMP"
        return "TEXT"

    cols = ", ".join(f'"{c.name}" {affinity(c)}' for c in table.columns)
    return f"CREATE TABLE {table.name} ({cols})"


@asynccontextmanager
async def _two_holders():
    """One parsed_media, a resource each for owners A and B."""
    import datetime as dt

    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_ddl(ParsedMedia.__table__))
        await conn.exec_driver_sql(_ddl(Resources.__table__))
        await conn.execute(
            insert(ParsedMedia.__table__).values(
                id=MEDIA_ID,
                platform_id="pf-shared",
                extract_audio_path="sb://library/shared/audio.mp3",
                title="Shared clip",
            )
        )
        base = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
        for rid, owner, created in (
            (RES_B, OWNER_B, base),
            (RES_A, OWNER_A, base + dt.timedelta(days=1)),
        ):
            await conn.execute(
                insert(Resources.__table__).values(
                    id=rid,
                    media_id=MEDIA_ID,
                    creator_id=owner,
                    file_path=f"sb://library/{rid}.mp4",
                    mime_type="video/mp4",
                    created_at=created,
                    transcript_status="none",
                )
            )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _picked(maker, stmt):
    async with maker() as session:
        row = (await session.execute(stmt)).mappings().first()
    return row["resource_id"] if row else None


# ─── transcription target ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_holder_transcribes_onto_their_own_resource() -> None:
    async with _two_holders() as maker:
        for rid in (RES_A, RES_B):
            stmt = at._transcribe_inputs_select_stmt(MEDIA_ID, resource_id=rid)
            assert await _picked(maker, stmt) == rid


@pytest.mark.asyncio
async def test_legacy_input_falls_back_to_the_initiators_own_resource() -> None:
    """No ``resource_id`` (a workflow recorded before the argument): A gets
    A's row even though B's is older; a user with no resource gets none."""
    async with _two_holders() as maker:
        stmt = at._transcribe_inputs_select_stmt(MEDIA_ID, creator_id=OWNER_A)
        assert await _picked(maker, stmt) == RES_A
        stmt = at._transcribe_inputs_select_stmt(MEDIA_ID, creator_id=STRANGER)
        assert await _picked(maker, stmt) is None


def test_statement_refuses_to_guess_without_a_target() -> None:
    with pytest.raises(ValueError):
        at._transcribe_inputs_select_stmt(MEDIA_ID)


@pytest.mark.asyncio
async def test_load_inputs_without_resource_or_initiator_raises_before_reading() -> (
    None
):
    """DBOS failures must raise (a returned dict reads as SUCCESS)."""

    @asynccontextmanager
    async def _unreachable():
        raise AssertionError("must not reach the database")
        yield  # pragma: no cover

    with patch("app.db.session.read_scope", _unreachable):
        with pytest.raises(RuntimeError, match="refusing to pick a resource"):
            await at.load_transcribe_inputs(MEDIA_ID, "", None)


@pytest.mark.asyncio
async def test_status_flips_touch_only_the_transcribed_resource() -> None:
    async with _two_holders() as maker:

        @asynccontextmanager
        async def _write_scope():
            async with maker() as session:
                async with session.begin():
                    yield session

        with patch("app.db.session.write_scope", _write_scope):
            await at.mark_transcript_completed(RES_A)
            await at.mark_transcript_failed(RES_B)
        async with maker() as session:
            rows = dict(
                (
                    await session.execute(
                        select(Resources.id, Resources.transcript_status)
                    )
                )
                .tuples()
                .all()
            )
    assert rows == {RES_A: "completed", RES_B: "failed"}


# ─── workflow body: one resource for every write ──────────────────


class _Manager:
    async def update_progress(self, *_a, **_k):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, rid", [(str(OWNER_A), RES_A), (str(OWNER_B), RES_B)])
async def test_workflow_threads_its_resource_through_every_write(owner, rid) -> None:
    load = AsyncMock(
        return_value={
            "audio_path": "a.mp3",
            "resource_id": str(rid),
            "provider_key": "openai",
            "provider_config": {},
            "language": "auto",
            "task_assignment": "",
        }
    )
    completed = AsyncMock()
    chain = AsyncMock()
    consume = AsyncMock()
    with (
        patch.object(at, "load_transcribe_inputs", load),
        patch.object(at, "assert_audio_present_step", AsyncMock(return_value="a.mp3")),
        patch.object(
            at, "run_whisper", AsyncMock(return_value={"duration_seconds": 1.0})
        ),
        patch.object(at, "mark_transcript_completed", completed),
        patch.object(at, "charge_transcription_step", AsyncMock(return_value={})),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: _Manager(),
        ),
        patch("app.tasks.download_helpers.chain_summary_for_tags", chain),
        patch("app.tasks.download_helpers.consume_summary_follow_up", consume),
    ):
        await inspect.unwrap(at.ai_transcription_workflow)(MEDIA_ID, owner, rid)

    assert load.await_args.args == (MEDIA_ID, owner, rid)
    completed.assert_awaited_once_with(str(rid))
    assert chain.await_args.kwargs["resource_id"] == str(rid)
    assert consume.await_args.kwargs["resource_id"] == str(rid)


@pytest.mark.asyncio
async def test_legacy_failure_before_a_resource_is_known_marks_nothing() -> None:
    """A legacy input whose initiator holds no resource fails in
    load_transcribe_inputs; there is no row of theirs to mark failed, and
    the old ``WHERE media_id`` flip would have failed everyone else's."""
    failed = AsyncMock()
    with (
        patch.object(
            at,
            "load_transcribe_inputs",
            AsyncMock(side_effect=RuntimeError("no resource to transcribe")),
        ),
        patch.object(at, "mark_transcript_failed", failed),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: _Manager(),
        ),
        patch("app.workflows._failure_handler.record_workflow_failure", AsyncMock()),
    ):
        with pytest.raises(RuntimeError):
            await inspect.unwrap(at.ai_transcription_workflow)(MEDIA_ID, "u-1")
    failed.assert_not_awaited()


# ─── summary chain follows the transcribed resource ───────────────


@pytest.mark.asyncio
async def test_summary_chain_and_follow_up_read_the_named_resource() -> None:
    from app.repositories.media_repository import MediaRepository
    from app.repositories.resources_repository import ResourcesRepository
    from app.tasks import download_helpers as dh

    by_id = AsyncMock(return_value={"id": str(RES_A), "creator_id": str(OWNER_A)})
    by_media = AsyncMock(return_value={"id": str(RES_B), "creator_id": str(OWNER_B)})
    dispatch = AsyncMock()
    with (
        patch.object(
            MediaRepository, "get_by_id", AsyncMock(return_value={"id": MEDIA_ID})
        ),
        patch.object(ResourcesRepository, "get_resource_by_id", by_id),
        patch.object(ResourcesRepository, "get_resource_by_media_id", by_media),
        patch.object(
            dh, "read_resource_tag_slugs", AsyncMock(return_value={"summary"})
        ),
        patch.object(dh, "_dispatch_post_transcript_summary", dispatch),
    ):
        await dh.chain_summary_for_tags(MEDIA_ID, str(OWNER_A), resource_id=str(RES_A))
        await dh.consume_summary_follow_up(MEDIA_ID, resource_id=str(RES_A))

    by_media.assert_not_awaited()
    assert dispatch.await_args_list[0].kwargs["resource_id"] == str(RES_A)
    assert dispatch.await_args_list[0].kwargs["workflow_user_id"] == str(OWNER_A)


# ─── analyze_l1 fallback ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_fallback_is_the_initiators_own_resource() -> None:
    """It used to fall back to the media's earliest resource of ANY owner
    (B's, here) when the initiator had none."""
    async with _two_holders() as maker:
        assert (
            await _picked(maker, _analyze_resource_lookup_stmt(MEDIA_ID, OWNER_A))
            == RES_A
        )
        assert (
            await _picked(maker, _analyze_resource_lookup_stmt(MEDIA_ID, STRANGER))
            is None
        )


@pytest.mark.asyncio
async def test_analyze_without_resource_or_initiator_raises() -> None:
    from app.workflows.analyze_l1 import call_analyze_l1

    with pytest.raises(RuntimeError, match="refusing to pick a resource"):
        await inspect.unwrap(call_analyze_l1)(
            media_id=MEDIA_ID,
            cover_url="https://cdn.example/c.jpg",
            title="",
            description="",
            user_id=None,
            provider_key="openai",
            provider_config={},
            agent_model="m",
            agent_slug="analyze",
            wf_id="wf-1",
            fallback_models=[],
            resource_id=None,
        )
