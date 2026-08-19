# backend/tests/api/test_ai_task_titles_and_flow_grouping.py

"""Task Center readability for the AI trigger endpoints (fix wave A2).

Two user-visible defects, both in ``app/api/ai_router.py``:

1. **Rows were titled with the raw platform_id** — "Transcribe:
   7643786260724632866". A Snowflake-shaped number identifies nothing;
   the user wants the file they clicked on. ``_task_display_name``
   resolves filename → media title → id, and BOTH the row titles and the
   flow name go through it so a step card's header cannot disagree with
   its steps.

2. **One job showed up as two cards.** "Send to agent" transcribes, and
   the frontend fires the summary from a *separate* request once the
   transcript lands (``frontend/utils/ensureResourceProcessed.ts``). The
   transcribe request created a flow; the summary request created nothing
   — so the user saw a 1/1 step card plus an unrelated loose row for what
   is, to them, a single job. ``_find_joinable_flow_id`` hangs the summary
   off the transcription's flow.

The join must not over-reach: another user's flow, another resource's
flow, a batch (playlist) flow, or one from last week are all wrong
answers, and each is pinned by a test below.

Direct-coroutine-call + monkeypatch convention, same as the sibling
test_transcribe_auto_extract_chain.py / test_ai_router_summary_identity.py
(no HTTP test client exists for ai_router). No DB, no DBOS runtime.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.api import ai_router
from app.core.deps import AuthContext

# ─── helpers ──────────────────────────────────────────────────────


def _auth(user_id: str = "caller-uuid") -> AuthContext:
    return AuthContext(user_id=user_id, auth_type="jwt")


class _Result:
    def __init__(self, value):
        self._v = value

    def scalars(self):
        return self

    def first(self):
        return self._v


class _RecordingSession:
    """Records every executed statement and answers with one canned
    result (or raises it, for the best-effort failure path)."""

    def __init__(self, result):
        self._result = result
        self.statements: list = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        if isinstance(self._result, BaseException):
            raise self._result
        return _Result(self._result)


def _patch_read_scope(monkeypatch, result) -> _RecordingSession:
    import app.db.session as dbs

    session = _RecordingSession(result)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(dbs, "read_scope", _scope)
    return session


def _compiled(session: _RecordingSession):
    assert session.statements, "the lookup issued no query at all"
    return session.statements[-1].compile(dialect=postgresql.dialect())


# ─── _task_display_name ───────────────────────────────────────────


class TestTaskDisplayName:
    """filename → media title → id, in that order and no other."""

    def test_prefers_the_resource_filename(self) -> None:
        name = ai_router._task_display_name(
            "7643786260724632866",
            {"filename": "Morning briefing.mp4"},
            {"title": "Platform title"},
        )
        assert name == "Morning briefing.mp4"

    def test_falls_back_to_media_title_when_filename_is_missing(self) -> None:
        assert (
            ai_router._task_display_name("pf-1", {}, {"title": "Platform title"})
            == "Platform title"
        )
        assert (
            ai_router._task_display_name(None, None, {"title": "Platform title"})
            == "Platform title"
        )

    def test_blank_and_whitespace_names_do_not_win(self) -> None:
        """An empty string is not a name — a row titled "" is worse than
        one titled with the id."""
        assert (
            ai_router._task_display_name(
                "pf-1", {"filename": "   "}, {"title": "Platform title"}
            )
            == "Platform title"
        )
        assert ai_router._task_display_name(
            "pf-1", {"filename": ""}, {"title": ""}
        ) == ("pf-1")

    def test_platform_id_is_the_last_resort_never_the_first(self) -> None:
        """The whole bug was the id winning. With any name available the
        id must not appear — this fails if the fallback order is reversed."""
        assert (
            ai_router._task_display_name(
                "7643786260724632866", {"filename": "Clip.mp4"}, {"title": "T"}
            )
            == "Clip.mp4"
        )
        assert ai_router._task_display_name("7643786260724632866", None, None) == (
            "7643786260724632866"
        )

    def test_truncates_to_the_column_budget(self) -> None:
        long_name = "x" * 500
        out = ai_router._task_display_name("pf-1", {"filename": long_name}, None)
        assert len(out) == ai_router._TASK_NAME_LIMIT == 200


# ─── _find_joinable_flow_id ───────────────────────────────────────


class TestFindJoinableFlowId:
    @pytest.mark.asyncio
    async def test_returns_the_recent_transcription_flow(self, monkeypatch) -> None:
        _patch_read_scope(monkeypatch, "flow-uuid-7")
        assert (
            await ai_router._find_joinable_flow_id("res-1", "user-1") == "flow-uuid-7"
        )

    @pytest.mark.asyncio
    async def test_missing_resource_id_issues_no_query_at_all(
        self, monkeypatch
    ) -> None:
        """``resource_id == None`` compiles to ``IS NULL`` and would match
        parse roots — grouping a summary into an unrelated submission's
        card. "No resource" must short-circuit, not query."""
        session = _patch_read_scope(monkeypatch, "flow-uuid-7")
        assert await ai_router._find_joinable_flow_id("", "user-1") is None
        assert await ai_router._find_joinable_flow_id(None, "user-1") is None
        assert session.statements == []

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_matches(self, monkeypatch) -> None:
        _patch_read_scope(monkeypatch, None)
        assert await ai_router._find_joinable_flow_id("res-1", "user-1") is None

    @pytest.mark.asyncio
    async def test_lookup_failure_degrades_to_no_grouping(self, monkeypatch) -> None:
        """Grouping is presentation. A broken lookup must cost the user a
        step card, never the summary itself."""
        _patch_read_scope(monkeypatch, RuntimeError("db down"))
        assert await ai_router._find_joinable_flow_id("res-1", "user-1") is None

    @pytest.mark.asyncio
    async def test_scoped_to_this_resource_user_and_task_types(
        self, monkeypatch
    ) -> None:
        """Drop any one of these predicates and the summary starts joining
        someone else's / another asset's / an unrelated job's flow."""
        session = _patch_read_scope(monkeypatch, None)
        await ai_router._find_joinable_flow_id("res-1", "user-1")

        compiled = _compiled(session)
        sql = str(compiled)
        # The IN clause binds as a list, so flatten one level.
        values = set()
        for v in compiled.params.values():
            values.update(v) if isinstance(v, list) else values.add(v)

        assert "res-1" in values, "must key on the resource being summarised"
        assert "user-1" in values, "must key on the requesting user"
        assert {"ai_transcription", "extract_audio"} <= values, (
            "both transcript-producing shapes are joinable; extract_audio is "
            "the two-step variant of the same step"
        )
        # Ownership is asserted on BOTH sides: the row we found and the flow
        # it points at. A flow is per-user; splicing into someone else's card
        # would leak their job into this user's Task Center.
        assert sql.count("task_tracking.user_id = ") == 1
        assert sql.count("task_flows.user_id = ") == 1
        assert "task_tracking.created_at >= " in sql, "no time window"
        assert (
            "ORDER BY public.task_tracking.created_at DESC" in sql
        ), "must pick the most recent chain, not an arbitrary one"
        assert "LIMIT" in sql

    @pytest.mark.asyncio
    async def test_time_window_is_honoured(self, monkeypatch) -> None:
        session = _patch_read_scope(monkeypatch, None)
        await ai_router._find_joinable_flow_id("res-1", "user-1", window_hours=2)

        cutoffs = [
            v for v in _compiled(session).params.values() if isinstance(v, dt.datetime)
        ]
        assert len(cutoffs) == 1
        delta = dt.datetime.now(dt.timezone.utc) - cutoffs[0]
        assert (
            dt.timedelta(hours=1, minutes=59) < delta < dt.timedelta(hours=2, minutes=1)
        )

    @pytest.mark.asyncio
    async def test_default_window_is_24h(self, monkeypatch) -> None:
        session = _patch_read_scope(monkeypatch, None)
        await ai_router._find_joinable_flow_id("res-1", "user-1")

        cutoffs = [
            v for v in _compiled(session).params.values() if isinstance(v, dt.datetime)
        ]
        delta = dt.datetime.now(dt.timezone.utc) - cutoffs[0]
        assert (
            dt.timedelta(hours=23, minutes=59)
            < delta
            < dt.timedelta(hours=24, minutes=1)
        )
        assert ai_router._FLOW_JOIN_WINDOW_HOURS == 24

    @pytest.mark.asyncio
    async def test_batch_flows_are_excluded_but_null_resource_rows_are_not(
        self, monkeypatch
    ) -> None:
        """A flow holding rows for OTHER resources is a batch (Soda
        playlist / batch parse); dropping one summary into a 185-track card
        is noise. But a NULL resource_id row is the parse root of this very
        submission — excluding those would un-group the download chain the
        resource came from."""
        session = _patch_read_scope(monkeypatch, None)
        await ai_router._find_joinable_flow_id("res-1", "user-1")

        sql = str(_compiled(session))
        assert "NOT (EXISTS" in sql, "no batch-flow exclusion at all"
        sub = sql[sql.index("NOT (EXISTS") :]
        assert (
            "resource_id IS NOT NULL" in sub
        ), "NULL resource_id (the parse root) must NOT disqualify a flow"
        assert "resource_id != " in sub


# ─── endpoint wiring: /summarize/resource/{id} ────────────────────


def _patch_summary_boundaries(
    monkeypatch,
    *,
    resource: dict,
    media: dict,
    created: list,
    flow_id: str | None = "flow-uuid-7",
):
    """Everything /summarize/resource touches except the two things under
    test (row title + flow_id). Returns the flow-lookup spy."""

    async def _resolve(_rid, _uid):
        return resource, media["platform_id"], media

    monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _resolve)

    class _NoActive:
        async def execute(self, *_a, **_k):
            result = MagicMock()
            result.first.return_value = None
            return result

    @asynccontextmanager
    async def _scope():
        yield _NoActive()

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(ai_router, "get_team_id_for_user", AsyncMock(return_value=None))
    monkeypatch.setattr(ai_router, "PointsService", lambda: MagicMock())

    ai_repo = MagicMock()
    ai_repo.get_transcript = AsyncMock(return_value={"full_text": "hello"})
    monkeypatch.setattr(ai_router, "get_ai_repository", lambda: ai_repo)

    async def _create(**kwargs):
        created.append(kwargs)
        return "task-row-id"

    mgr = MagicMock()
    mgr.create = AsyncMock(side_effect=_create)
    mgr.fail = AsyncMock()
    import app.services.infra.unified_task_manager as utm

    monkeypatch.setattr(utm, "get_task_manager", lambda: mgr)

    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock())

    join_spy = AsyncMock(return_value=flow_id)
    monkeypatch.setattr(ai_router, "_find_joinable_flow_id", join_spy)
    return join_spy


class TestSummaryJoinsTheTranscriptionFlow:
    @staticmethod
    def _media() -> dict:
        return {"id": "111", "platform_id": "7643786260724632866", "title": "Plat T"}

    @pytest.mark.asyncio
    async def test_summary_row_lands_on_the_transcription_flow(
        self, monkeypatch
    ) -> None:
        created: list = []
        join_spy = _patch_summary_boundaries(
            monkeypatch,
            resource={
                "id": "res-1",
                "media_id": "111",
                "creator_id": "caller-uuid",
                "filename": "Morning briefing.mp4",
            },
            media=self._media(),
            created=created,
        )

        await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert (
            created[0]["flow_id"] == "flow-uuid-7"
        ), "the follow-up summary must join the transcription's step card"
        # Scoped at the call site too — the lookup is only as safe as what
        # it is handed.
        assert join_spy.await_args.args == ("res-1", "caller-uuid")

    @pytest.mark.asyncio
    async def test_row_title_is_the_filename_not_the_id(self, monkeypatch) -> None:
        created: list = []
        _patch_summary_boundaries(
            monkeypatch,
            resource={
                "id": "res-1",
                "media_id": "111",
                "creator_id": "caller-uuid",
                "filename": "Morning briefing.mp4",
            },
            media=self._media(),
            created=created,
        )

        await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert created[0]["title"] == "Summarize Morning briefing.mp4"
        assert "7643786260724632866" not in created[0]["title"]

    @pytest.mark.asyncio
    async def test_no_joinable_flow_still_dispatches_ungrouped(
        self, monkeypatch
    ) -> None:
        """The old behaviour is the floor: a standalone summary (no recent
        transcription) is a loose row, not a failure."""
        created: list = []
        _patch_summary_boundaries(
            monkeypatch,
            resource={"id": "res-1", "media_id": "111", "creator_id": "caller-uuid"},
            media=self._media(),
            created=created,
            flow_id=None,
        )

        res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert created[0]["flow_id"] is None
        assert created[0]["title"] == "Summarize Plat T"
        assert res["message"] == "Summary generation queued"


# ─── endpoint wiring: legacy /summarize/{platform_id} ─────────────


class TestLegacySummaryEndpoint:
    """Whether the user gets a step card must not depend on which button
    they clicked — the homepage MediaCard still calls this one."""

    @staticmethod
    def _patch(monkeypatch, created: list, *, resource: dict | None):
        media = {"id": "111", "platform_id": "7643786260724632866", "title": "Plat T"}

        async def _get(_pid):
            return media

        monkeypatch.setattr(ai_router, "_get_media_or_404", _get)
        repo = MagicMock()
        repo.get_resource_by_media_id_and_creator = AsyncMock(return_value=resource)
        monkeypatch.setattr(ai_router, "ResourcesRepository", lambda: repo)
        monkeypatch.setattr(
            ai_router, "get_team_id_for_user", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(ai_router, "PointsService", lambda: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_transcript = AsyncMock(return_value={"full_text": "hello"})
        monkeypatch.setattr(ai_router, "get_ai_repository", lambda: ai_repo)

        async def _create(**kwargs):
            created.append(kwargs)
            return "task-row-id"

        mgr = MagicMock()
        mgr.create = AsyncMock(side_effect=_create)
        mgr.fail = AsyncMock()
        import app.services.infra.unified_task_manager as utm

        monkeypatch.setattr(utm, "get_task_manager", lambda: mgr)
        import app.services.infra.dbos_orchestrator as orch

        monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock())

        join_spy = AsyncMock(return_value="flow-uuid-7")
        monkeypatch.setattr(ai_router, "_find_joinable_flow_id", join_spy)
        return join_spy

    @pytest.mark.asyncio
    async def test_groups_and_titles_like_the_by_resource_endpoint(
        self, monkeypatch
    ) -> None:
        created: list = []
        join_spy = self._patch(
            monkeypatch,
            created,
            resource={"id": "res-9", "filename": "Morning briefing.mp4"},
        )

        await ai_router.trigger_summary("7643786260724632866", _auth(), None)

        assert created[0]["title"] == "Summarize Morning briefing.mp4"
        assert created[0]["flow_id"] == "flow-uuid-7"
        assert join_spy.await_args.args == ("res-9", "caller-uuid")

    @pytest.mark.asyncio
    async def test_no_resource_means_no_summary_row_and_no_flow_lookup(
        self, monkeypatch
    ) -> None:
        """Without a resource there is no per-user transcript to summarise,
        so this endpoint dispatches transcription instead and never reaches
        the summary row. The flow lookup must not run either: it is keyed on
        resource_id, and ``resource_id IS NULL`` would match parse roots —
        grouping this summary into an unrelated submission's card."""
        created: list = []
        join_spy = self._patch(monkeypatch, created, resource=None)
        import app.services.infra.dbos_orchestrator as orch

        res = await ai_router.trigger_summary("7643786260724632866", _auth(), None)

        join_spy.assert_not_awaited()
        assert created == []
        assert orch.start_workflow_routed.await_args.args[0] == "ai_transcription"
        assert "Transcription queued" in res["message"]


# ─── endpoint wiring: transcribe row titles ───────────────────────


class TestTranscribeRowTitles:
    """The flow header and its steps read from one name, so they cannot
    drift apart (the screenshot showed "Transcribe: 764378…" under a
    differently-named card)."""

    @staticmethod
    def _patch(monkeypatch, created: list, flows: list, *, resource: dict, audio: bool):
        media = {
            "id": "111",
            "platform_id": "7643786260724632866",
            "extract_audio_path": "d/a.m4a" if audio else "",
            "music_download_path": "",
            "download_path": "d/video.mp4",
            "title": "Plat T",
        }

        async def _resolve(_rid, _uid):
            return resource, media["platform_id"], media

        monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _resolve)

        class _NoActive:
            async def execute(self, *_a, **_k):
                result = MagicMock()
                result.first.return_value = None
                return result

        @asynccontextmanager
        async def _scope():
            yield _NoActive()

        import app.db.session as dbs

        monkeypatch.setattr(dbs, "read_scope", _scope)
        settings_repo = MagicMock()
        settings_repo.get_by_user_id = AsyncMock(return_value={"settings_json": {}})
        monkeypatch.setattr(
            "app.repositories.user_settings_repository.UserSettingsRepository",
            lambda: settings_repo,
        )
        monkeypatch.setattr(
            ai_router, "get_team_id_for_user", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(ai_router, "PointsService", lambda: MagicMock())

        async def _create(**kwargs):
            created.append(kwargs)
            return "task-row-id"

        async def _create_flow(**kwargs):
            flows.append(kwargs)
            return "flow-uuid-7"

        mgr = MagicMock()
        mgr.create = AsyncMock(side_effect=_create)
        mgr.create_flow = AsyncMock(side_effect=_create_flow)
        mgr.fail = AsyncMock()
        import app.services.infra.unified_task_manager as utm

        monkeypatch.setattr(utm, "get_task_manager", lambda: mgr)
        import app.services.infra.dbos_orchestrator as orch

        monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock())

    @pytest.mark.asyncio
    async def test_row_and_flow_read_the_same_filename(self, monkeypatch) -> None:
        created: list = []
        flows: list = []
        self._patch(
            monkeypatch,
            created,
            flows,
            resource={
                "id": "res-1",
                "media_id": "111",
                "creator_id": "caller-uuid",
                "filename": "Morning briefing.mp4",
            },
            audio=True,
        )

        await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert created[0]["title"] == "Transcribe Morning briefing.mp4"
        # "Process", not "Transcribe": the same flow carries extract_audio
        # and the follow-up summary, matching the parse chain's
        # "Process {url}".
        assert flows[0]["name"] == "Process Morning briefing.mp4"
        assert "7643786260724632866" not in flows[0]["name"]

    @pytest.mark.asyncio
    async def test_extract_chain_threads_the_same_name_into_the_workflow(
        self, monkeypatch
    ) -> None:
        """The chained ai_transcription row is titled by download_helpers
        from the ``video_title`` kwarg — if that still carried the id, step
        2 of the card would go back to showing a number."""
        created: list = []
        flows: list = []
        self._patch(
            monkeypatch,
            created,
            flows,
            resource={
                "id": "res-1",
                "media_id": "111",
                "creator_id": "caller-uuid",
                "filename": "Morning briefing.mp4",
            },
            audio=False,
        )
        import app.services.infra.dbos_orchestrator as orch

        await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        kwargs = orch.start_workflow_routed.await_args.kwargs["dbos_workflow_kwargs"]
        assert kwargs["video_title"] == "Morning briefing.mp4"
        assert created[0]["title"] == "Audio Morning briefing.mp4"
