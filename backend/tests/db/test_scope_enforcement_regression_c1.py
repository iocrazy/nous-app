"""C1 regression pin (Phase C task 1, 2026-08-05): proves the
``is_enforced("resources")``-gated scope wrap added at each of the ~14
Resources-touching call sites across the 7 AI/media workflow files is
LOAD-BEARING in production, not decorative — same contract as
``test_scope_enforcement_regression_i1.py`` (Phase A), extended to cover
Phase C's workflow sites and its two write-shaped call classes (bulk Core
UPDATE, not just SELECT).

Context: ``SCOPE_ENFORCE_RESOURCES`` DEFAULTS to false in code, but
production sets it TRUE via ``secrets/backend.env`` (CLAUDE.md 部署陷阱).
These DBOS workflow steps have NO ambient per-request scope of their own
(they are background steps, not HTTP handlers), so every scope wrap added
in this migration batch is what stands between the statement and a
fail-closed 500 once the flag is on.

deferred-minors batch (2026-08, scope-judgment-pass Minor 1): site 5
(``ai_summary.py::load_summary_inputs``) was flipped from
``system_request_scope`` to a real per-user ``request_scope(Scope(user_id=
...))`` — its query already filters ``Resources.creator_id == user_id``
explicitly and the function has a single determinate ``user_id`` arg, so a
real user Scope is defense-in-depth-correct (mirrors C3's
``tags_repository._get_tag_counts_fallback``) rather than opening
unrestricted SYSTEM visibility. Every other site's Resources query either
has no per-owner filter (cross-tenant sweep/backfill by design) or is a
preference-only ``ORDER BY`` (analyze_l1's creator-match-first lookup,
which FALLS BACK to another user's resource — injecting a real filter
there would break that fallback) or is itself the read that resolves
``creator_id``/``user_id`` in the first place (volcengine ASR's creator
lookup) — all of those correctly stay ``system_request_scope``. See the
per-site docstrings in the workflow files for the individual judgment call.

Three call-site shapes, three assertion helpers:

  SELECT sites wrapped in ``system_request_scope``
  (``_assert_select_load_bearing``) — mirrors I1:
    (a) POSITIVE — wrapped in ``system_request_scope`` (SYSTEM scope: no
        injection, no raise), the statement clears the choke point and
        reaches real execution, failing only with ``OperationalError`` (no
        such table) — proof it was never intercepted.
    (b) COUNTERFACTUAL — with NO scope open at all (simulating "the wrap
        was deleted"), the exact same statement is intercepted and
        fail-closed raises ``UnscopedQueryError`` BEFORE reaching the DB.

  SELECT site wrapped in a real per-user ``request_scope(Scope(user_id=...))``
  (``_assert_select_load_bearing_via_user_scope``) — mirrors C3's
  ``_get_tag_counts_fallback`` variant; only site 5 (``ai_summary``) uses
  this shape:
    (a) POSITIVE — wrapped in ``request_scope(Scope(user_id=_UID))`` (a REAL
        user Scope, not SYSTEM) using the SAME identity the query already
        filters on — the choke point's injected predicate matches the
        query's own explicit filter, clears the choke point, and reaches
        real execution (``OperationalError``, never ``UnscopedQueryError``).
    (b) COUNTERFACTUAL — with NO scope open at all, fail-closed raises
        ``UnscopedQueryError`` BEFORE reaching the DB (same as the
        system-scope sites — the point being pinned is "some correctly-typed
        scope wrap is load-bearing here", regardless of which kind).

  Bulk Core UPDATE sites (``_assert_write_load_bearing``) — the write-path
  guard (``_forbid_scoped_bulk_dml``) is STRICTER than the SELECT path: a
  bulk UPDATE on a scoped model is forbidden under ANY real user ``Scope``,
  not just "no scope" — only ``SYSTEM`` may issue it (app/db/scope.py's
  docstring: "cannot be safely tenant-filtered / owner-stamped"). So each
  write site gets a THIRD assertion proving a real user scope also raises,
  confirming the site can only ever run as SYSTEM:
    (a) POSITIVE — system_request_scope (SYSTEM) — reaches real execution.
    (b) COUNTERFACTUAL — no scope at all — fail-closed raises.
    (c) COUNTERFACTUAL — a REAL user Scope — ALSO fail-closed raises (bulk
        DML forbidden under a real Scope, not merely permitted-if-scoped).

No ``INTEGRATION_DATABASE_URL`` needed — same dialect-independent in-memory
SQLite rationale as I1 (the choke point's fail-closed raise happens from a
pure Python statement-tree walk before any SQL is compiled or sent to a
database).

UUID note: wherever a statement binds ``Resources.creator_id`` (a
``Uuid``-typed column), the POSITIVE branch reaches real parameter binding
against the throwaway SQLite session. SQLAlchemy's generic ``Uuid`` type has
a REAL (non-no-op) bind_processor on the sqlite dialect that calls
``value.hex`` — so a plain ``str`` user id raises ``AttributeError`` instead
of the expected ``OperationalError`` (verified: on the ``postgresql``
dialect ``bind_processor`` is ``None`` — asyncpg/psycopg2 accept a plain str
natively, so this is purely a SQLite-test-harness accommodation, not a
production concern; the codebase's many existing
``Resources.creator_id == user_id`` str comparisons are correct against real
Postgres). Use a real ``uuid.UUID`` (``_UID`` below) wherever a statement
builder takes a ``user_id`` parameter, mirroring I1's own ``_UID`` convention.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import String, cast, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.scope import Scope, UnscopedQueryError, request_scope, system_request_scope
from app.models import Resources, TaskTracking
from app.services.infra.dbos_orchestrator import _routing_select_stmt
from app.workflows.ai_summary import _summary_inputs_select_stmt
from app.workflows.ai_transcription import _transcribe_inputs_select_stmt
from app.workflows.analyze_l1 import _analyze_resource_lookup_stmt
from app.workflows.thumbnail import _backfill_candidates_stmt

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# A real UUID — see module docstring for why a plain str breaks the POSITIVE
# branch's real parameter binding against the throwaway SQLite session.
_UID = uuid.uuid4()


@pytest.fixture
async def sqlite_sessionmaker():
    """Real AsyncSession machinery bound to an in-memory SQLite engine with
    NO schema created — enough to exercise the real do_orm_execute event
    (see module docstring for why no schema is needed)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    sm = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture
def enforce_resources_on(monkeypatch):
    import app.db.scope as scope_module

    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)


async def _assert_select_load_bearing(sqlite_sessionmaker, stmt) -> None:
    """Shared (a)+(b) assertion pair for a SELECT statement that references
    Resources exactly like a gated call site does."""
    # (b) COUNTERFACTUAL — no ambient scope at all: fail-closed BEFORE any
    # DB round-trip (no "no such table" noise — the raise pre-empts it).
    async with sqlite_sessionmaker() as session:
        with pytest.raises(UnscopedQueryError):
            await session.execute(stmt)

    # (a) POSITIVE — real system_request_scope wrap: clears the choke
    # point, reaches real execution, fails ONLY on the throwaway DB having
    # no schema (never UnscopedQueryError).
    async with system_request_scope(reason="c1-regression-pin"):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(OperationalError):
                await session.execute(stmt)


async def _assert_select_load_bearing_via_user_scope(sqlite_sessionmaker, stmt) -> None:
    """(a)+(b) pair for the one SELECT site (ai_summary's
    ``load_summary_inputs``) whose correct wrap is a REAL per-user
    ``request_scope(Scope(user_id=_UID))`` rather than
    ``system_request_scope`` — the query already filters on that same
    identity (mirrors C3's ``_get_tag_counts_fallback`` helper of the same
    name)."""
    # (b) COUNTERFACTUAL — no ambient scope at all: fail-closed BEFORE any
    # DB round-trip.
    async with sqlite_sessionmaker() as session:
        with pytest.raises(UnscopedQueryError):
            await session.execute(stmt)

    # (a) POSITIVE — a REAL user Scope (not SYSTEM) carrying the SAME
    # identity the statement already filters on: clears the choke point
    # (the injected predicate matches the explicit one) and reaches real
    # execution, failing only on the throwaway DB having no schema.
    async with request_scope(Scope(user_id=_UID)):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(OperationalError):
                await session.execute(stmt)


async def _assert_write_load_bearing(sqlite_sessionmaker, stmt) -> None:
    """Shared (a)+(b)+(c) assertion triple for a bulk Core UPDATE statement
    that touches Resources exactly like a gated workflow write site does.
    See module docstring for why bulk DML gets a third (c) assertion the
    SELECT sites don't."""
    # (b) COUNTERFACTUAL — no ambient scope at all: fail-closed.
    async with sqlite_sessionmaker() as session:
        with pytest.raises(UnscopedQueryError):
            await session.execute(stmt)

    # (c) COUNTERFACTUAL — a REAL user Scope also forbids bulk DML on a
    # scoped model (not merely "some scope must be open") — proves these
    # workflow writes can ONLY run as SYSTEM.
    async with request_scope(Scope(user_id=_UID)):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(UnscopedQueryError):
                await session.execute(stmt)

    # (a) POSITIVE — real system_request_scope wrap: clears the choke
    # point, reaches real execution, fails ONLY on the throwaway DB having
    # no schema.
    async with system_request_scope(reason="c1-regression-pin"):
        async with sqlite_sessionmaker() as session:
            with pytest.raises(OperationalError):
                await session.execute(stmt)


# ── 1. ai_transcription.py::load_transcribe_inputs ──────────────────────


async def test_ai_transcription_load_inputs_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/ai_transcription.py::load_transcribe_inputs —
    parsed_media JOIN resources."""
    await _assert_select_load_bearing(
        sqlite_sessionmaker, _transcribe_inputs_select_stmt(1)
    )


# ── 2. ai_transcription.py::_run_volcengine_asr — creator_id lookup ─────


async def test_ai_transcription_volcengine_creator_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/ai_transcription.py::_run_volcengine_asr — SELECT
    creator_id FROM resources WHERE id = :rid."""
    stmt = select(Resources.creator_id).where(Resources.id == 1)
    await _assert_select_load_bearing(sqlite_sessionmaker, stmt)


# ── 3. ai_transcription.py::mark_transcript_completed ────────────────────


async def test_ai_transcription_mark_completed_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/ai_transcription.py::mark_transcript_completed — bulk
    UPDATE resources SET transcript_status='completed' WHERE media_id=:pid."""
    stmt = (
        update(Resources)
        .where(Resources.media_id == 1)
        .values(transcript_status="completed")
    )
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)


# ── 4. ai_transcription.py::mark_transcript_failed ───────────────────────


async def test_ai_transcription_mark_failed_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/ai_transcription.py::mark_transcript_failed — bulk
    UPDATE resources SET transcript_status='failed' WHERE media_id=:pid AND
    transcript_status <> 'completed'."""
    stmt = (
        update(Resources)
        .where(Resources.media_id == 1)
        .where(Resources.transcript_status != "completed")
        .values(transcript_status="failed")
    )
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)


# ── 5. ai_summary.py::load_summary_inputs ────────────────────────────────


async def test_ai_summary_load_inputs_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/ai_summary.py::load_summary_inputs — parsed_media JOIN
    resources JOIN resource_transcripts, WHERE r.creator_id = :uid.

    deferred-minors batch: this call site now opens a real per-user
    request_scope(Scope(user_id=user_id)) — not system_request_scope — so
    it is pinned via the user-scope helper (see that helper's docstring)."""
    await _assert_select_load_bearing_via_user_scope(
        sqlite_sessionmaker, _summary_inputs_select_stmt(1, _UID)
    )


# ── 6. ai_summary.py::persist_summary — resources.summary_status UPDATE ──


async def test_ai_summary_persist_summary_status_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/ai_summary.py::persist_summary — bulk UPDATE resources
    SET summary_status='completed' WHERE id=:rid (the one Resources-touching
    statement of the 3 in that atomic transaction)."""
    stmt = update(Resources).where(Resources.id == 1).values(summary_status="completed")
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)


# ── 7. analyze_l1.py::call_analyze_l1 — resource lookup ──────────────────


async def test_analyze_l1_resource_lookup_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/analyze_l1.py::call_analyze_l1 — parsed_media JOIN
    resources, ORDER BY creator match then created_at."""
    await _assert_select_load_bearing(
        sqlite_sessionmaker, _analyze_resource_lookup_stmt(1, _UID)
    )


# ── 8-10. analyze_l1.py — visual_analysis_status UPDATEs (processing/failed/completed) ──


@pytest.mark.parametrize("new_status", ["processing", "failed", "completed"])
async def test_analyze_l1_visual_status_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker, new_status
):
    """app/workflows/analyze_l1.py::call_analyze_l1 — the three bulk UPDATE
    resources SET visual_analysis_status=<processing|failed|completed>
    WHERE id=:rid sites (identical shape, different literal value)."""
    stmt = (
        update(Resources)
        .where(Resources.id == 1)
        .values(visual_analysis_status=new_status)
    )
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)


# ── 11. thumbnail.py::_backfill_scan_step ────────────────────────────────


async def test_thumbnail_backfill_candidates_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/thumbnail.py::_backfill_scan_step — SELECT id,
    file_path, mime_type FROM resources WHERE thumbnail_path IS NULL ..."""
    await _assert_select_load_bearing(
        sqlite_sessionmaker, _backfill_candidates_stmt(60)
    )


# ── 12. extract_audio.py::mark_transcript_failed_step ────────────────────


async def test_extract_audio_mark_transcript_failed_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/extract_audio.py::mark_transcript_failed_step — bulk
    UPDATE resources SET transcript_status='failed' WHERE id=:rid AND
    transcript_status <> 'completed'."""
    stmt = (
        update(Resources)
        .where(Resources.id == 1)
        .where(Resources.transcript_status != "completed")
        .values(transcript_status="failed")
    )
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)


# ── 13. scheduled_cleanup.py::cleanup_orphan_storage_step ────────────────


async def test_scheduled_cleanup_orphan_storage_ids_query_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/scheduled_cleanup.py::cleanup_orphan_storage_step::
    _load_resource_ids — SELECT id FROM resources (unfiltered, cross-tenant
    system scan)."""
    stmt = select(Resources.id)
    await _assert_select_load_bearing(sqlite_sessionmaker, stmt)


# ── 14. scheduled_recovery.py::reap_stuck_pending_tasks_step ─────────────


async def test_scheduled_recovery_reap_resources_update_is_load_bearing(
    enforce_resources_on, sqlite_sessionmaker
):
    """app/workflows/scheduled_recovery.py::reap_stuck_pending_tasks_step —
    bulk UPDATE resources SET <field>='failed' WHERE <field>='pending' AND
    updated_at < cutoff AND id::text NOT IN (live task_tracking subquery).
    Representative of all 3 looped fields (identical shape, different
    column)."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    live_subq = (
        select(TaskTracking.resource_id)
        .where(TaskTracking.status.in_(["pending", "processing", "running"]))
        .where(
            TaskTracking.task_type.in_(
                [
                    "ai_extract",
                    "ai_transcription",
                    "ai_summary",
                    "ai_pipeline",
                    "ai_visual_analysis",
                ]
            )
        )
        .where(TaskTracking.resource_id.is_not(None))
        .distinct()
    )
    stmt = (
        update(Resources)
        .where(Resources.transcript_status == "pending")
        .where(Resources.updated_at < cutoff)
        .where(cast(Resources.id, String).not_in(live_subq))
        .values(transcript_status="failed")
    )
    await _assert_write_load_bearing(sqlite_sessionmaker, stmt)


# ── Negative control: an UNSCOPED table (dbos_workflow_routing) never raises ──


async def test_unscoped_dbos_workflow_routing_query_never_raises_unscoped_query_error(
    enforce_resources_on, sqlite_sessionmaker
):
    """dbos_workflow_routing carries no scope mixin (a global routing config
    table, not per-tenant), so even with SCOPE_ENFORCE_RESOURCES on and no
    scope open, it must NOT be treated as a scoped table — confirms the
    choke point is precise, not blanket, and that
    dbos_orchestrator._routing_select_stmt needs no scope wrap."""
    stmt = _routing_select_stmt()
    async with sqlite_sessionmaker() as session:
        with pytest.raises(OperationalError):
            await session.execute(stmt)
