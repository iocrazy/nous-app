# backend/tests/api/test_transcribe_auto_extract_chain.py

"""Behavioural tests for the transcribe endpoints' audio-readiness gate.

The old gate 409'd whenever a media had no extracted audio — including
old downloads that DID have a video file on disk but never got their
audio extracted (extract_audio_path / music_download_path both empty).
"Wait for extraction to complete, then retry" was a dead end: nothing
was ever going to extract it. The manual extract-audio entry point was
buried in a detail page the user never sees.

The fix makes the gate three-way:
  1. audio on disk          → dispatch ai_transcription directly
  2. no audio + video file  → dispatch extract_audio(chain_transcription=
                              True), which extracts then unconditionally
                              chains transcription
  3. neither                → 409 with a clear "no audio track" message

Plus: the dead-end 409 is now decided BEFORE points are consumed, so a
409 never leaves the user charged.

These tests call the endpoint coroutines directly with the module
boundaries (resolver, dedup read_scope, points, task manager, workflow
dispatch) mocked, and assert the dispatch sequence + points ordering.
No DB, no DBOS runtime.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import ai_router
from app.core.deps import AuthContext

# ─── helpers ──────────────────────────────────────────────────────


def _auth() -> AuthContext:
    return AuthContext(user_id="user-1", auth_type="jwt")


class _NoActiveSession:
    """Stands in for the ORM AsyncSession used by the dedup probe — its
    execute().first() returns None (no in-flight transcription)."""

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.first.return_value = None
        return result


@asynccontextmanager
async def _fake_read_scope():
    yield _NoActiveSession()


def _patch_common(monkeypatch, dispatched: list, created: list):
    """Patch the shared boundaries: dedup read_scope, task manager,
    workflow dispatch. Returns the (fake) task manager + start_workflow
    mocks with call recording."""
    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _fake_read_scope)

    async def _create(**kwargs):
        created.append(kwargs)
        return "task-row-id"

    fake_mgr = MagicMock()
    fake_mgr.create = AsyncMock(side_effect=_create)
    fake_mgr.fail = AsyncMock()

    import app.services.infra.unified_task_manager as utm

    monkeypatch.setattr(utm, "get_task_manager", lambda: fake_mgr)

    async def _start(name, **kwargs):
        dispatched.append({"name": name, **kwargs})

    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock(side_effect=_start))

    return fake_mgr


def _patch_resource_resolver(monkeypatch, media: dict):
    resource = {"id": "res-1", "media_id": "111", "creator_id": "user-1"}

    async def _resolve(_rid, _uid):
        return resource, media["platform_id"], media

    monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _resolve)
    return resource


def _patch_no_nous_billing(monkeypatch):
    """Default (non-nous) model → the resource endpoint's billing branch is
    skipped, but the pre-branch calls (user_settings probe, team lookup,
    PointsService()) still run — stub them so they don't hit the DB."""
    settings_repo = MagicMock()
    settings_repo.get_by_user_id = AsyncMock(return_value={"settings_json": {}})
    monkeypatch.setattr(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        lambda: settings_repo,
    )
    monkeypatch.setattr(ai_router, "get_team_id_for_user", AsyncMock(return_value=None))
    monkeypatch.setattr(ai_router, "PointsService", lambda: MagicMock())


# ─── _has_extractable_video predicate ─────────────────────────────


class TestHasExtractableVideo:
    def test_video_file_is_extractable(self) -> None:
        assert ai_router._has_extractable_video({"download_path": "d/x/video.mp4"})
        assert ai_router._has_extractable_video({"download_path": "a/b/clip.MOV"})
        assert ai_router._has_extractable_video({"download_path": "a/b/c.webm"})

    def test_gallery_directory_not_extractable(self) -> None:
        # 图文 gallery: download_path points at a directory, no extension.
        assert not ai_router._has_extractable_video({"download_path": "douyin/12345"})

    def test_empty_or_missing_not_extractable(self) -> None:
        assert not ai_router._has_extractable_video({"download_path": ""})
        assert not ai_router._has_extractable_video({})
        assert not ai_router._has_extractable_video(None)


# ─── resource endpoint ────────────────────────────────────────────


class TestTranscribeByResource:
    @pytest.mark.asyncio
    async def test_audio_ready_dispatches_transcription_directly(
        self, monkeypatch
    ) -> None:
        media = {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "d/audio.m4a",
            "download_path": "d/video.mp4",
            "title": "T",
        }
        _patch_resource_resolver(monkeypatch, media)
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert len(dispatched) == 1
        assert dispatched[0]["name"] == "ai_transcription"
        assert created[0]["task_type"] == "ai_transcription"
        assert res["message"] == "Transcription queued"
        assert res["extracting_audio"] is False

    @pytest.mark.asyncio
    async def test_no_audio_but_video_dispatches_extract_chain(
        self, monkeypatch
    ) -> None:
        media = {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "",
            "music_download_path": "",
            "download_path": "bilibili/329/video.mp4",  # old B站 download
            "title": "Old Bilibili clip",
        }
        _patch_resource_resolver(monkeypatch, media)
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert len(dispatched) == 1
        d = dispatched[0]
        assert d["name"] == "extract_audio"
        # The chain flag is what makes extract_audio_workflow dispatch
        # transcription unconditionally after extraction.
        assert d["dbos_workflow_kwargs"]["chain_transcription"] is True
        assert d["dbos_workflow_kwargs"]["platform_id"] == "pf-1"
        # task_tracking row is created as extract_audio (matched wf id)
        assert created[0]["task_type"] == "extract_audio"
        assert created[0]["dbos_workflow_id"] == d["workflow_id"]
        assert res["extracting_audio"] is True
        assert "transcription will follow" in res["message"]

    @pytest.mark.asyncio
    async def test_no_audio_no_video_returns_409_without_charging(
        self, monkeypatch
    ) -> None:
        media = {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "",
            "music_download_path": "",
            "download_path": "douyin/gallery-dir",  # no video extension
            "title": "Gallery",
        }
        _patch_resource_resolver(monkeypatch, media)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        # Spy PointsService — the 409 must fire BEFORE any consume.
        consume = AsyncMock()
        pts = MagicMock()
        pts.check_and_consume = consume
        monkeypatch.setattr(ai_router, "PointsService", lambda: pts)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert exc.value.status_code == 409
        assert "no audio track available" in exc.value.detail
        consume.assert_not_awaited()  # points ordering: gate is before billing
        assert dispatched == []


# ─── dedup during the extract-audio window (T2 review I1) ─────────


class _ActiveTaskSession:
    """Dedup probe that answers "yes, one is running" ONLY when the query
    really asks about the given task_type.

    Compiling the statement rather than returning a canned row is what makes
    this falsifiable: drop ``extract_audio`` from the dedup's IN list and
    the probe goes back to None, the endpoint dispatches, and the assertions
    below fail — which is exactly the bug being fixed.
    """

    def __init__(self, task_type: str):
        self._task_type = task_type
        self.seen_sql: str | None = None

    async def execute(self, stmt, *_a, **_k):
        from sqlalchemy.dialects import postgresql

        self.seen_sql = str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        result = MagicMock()
        result.first.return_value = (
            ("wf-existing",) if self._task_type in self.seen_sql else None
        )
        return result


def _patch_dedup_session(monkeypatch, session):
    import app.db.session as dbs

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(dbs, "read_scope", _scope)


class TestTranscribeDedup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("active_type", ["ai_transcription", "extract_audio"])
    async def test_an_active_task_short_circuits_without_dispatching(
        self, monkeypatch, active_type
    ) -> None:
        """``extract_audio`` is the half that used to be missed: a video with
        no audio track yet is transcribed via extract_audio(chain=True), so
        during that whole window the dedup found nothing, dispatched again,
        and tripped migration 121's unique index into a 500."""
        media = {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "",
            "music_download_path": "",
            "download_path": "bilibili/329/video.mp4",
            "title": "Old Bilibili clip",
        }
        _patch_resource_resolver(monkeypatch, media)
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)
        session = _ActiveTaskSession(active_type)
        _patch_dedup_session(monkeypatch, session)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["message"] == "Transcription already in progress"
        assert dispatched == []
        assert created == []

    @pytest.mark.asyncio
    async def test_losing_the_insert_race_reports_in_progress_not_500(
        self, monkeypatch
    ) -> None:
        """TOCTOU: another request creates the active row between our SELECT
        and our INSERT. Migration 121's own comment says callers must treat
        the unique violation as "already in progress" — a 500 would tell the
        user their media failed while it is being processed."""
        media = {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "d/audio.m4a",
            "download_path": "d/video.mp4",
            "title": "T",
        }
        _patch_resource_resolver(monkeypatch, media)
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        async def _conflict(**_kwargs):
            raise Exception(
                "duplicate key value violates unique constraint "
                '"idx_task_tracking_active_per_resource_type"'
            )

        mgr.create = AsyncMock(side_effect=_conflict)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["message"] == "Transcription already in progress"
        assert res["points_charged"] == 0
        assert dispatched == []
        mgr.fail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_race_loser_gets_its_points_back(self, monkeypatch) -> None:
        media = {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "d/audio.m4a",
            "download_path": "d/video.mp4",
            "title": "T",
            "duration": 600,
        }
        _patch_resource_resolver(monkeypatch, media)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        settings_repo = MagicMock()
        settings_repo.get_by_user_id = AsyncMock(
            return_value={
                "settings_json": {
                    "ai_settings": {"task_assignment": {"transcription": "nous-asr"}}
                }
            }
        )
        monkeypatch.setattr(
            "app.repositories.user_settings_repository.UserSettingsRepository",
            lambda: settings_repo,
        )
        monkeypatch.setattr(
            ai_router, "get_team_id_for_user", AsyncMock(return_value="team-1")
        )
        nous_repo = MagicMock()
        nous_repo.get_by_name = AsyncMock(
            return_value={
                "is_enabled": True,
                "pricing_type": "per_hour",
                "pricing_value": 60,
            }
        )
        monkeypatch.setattr(
            "app.repositories.mediahub_model_repository."
            "get_mediahub_model_repository",
            lambda: nous_repo,
        )
        refund = AsyncMock()
        pts = MagicMock()
        pts.ensure_team_quota = AsyncMock()
        pts.check_and_consume = AsyncMock(
            return_value={"success": True, "points_cost": 10}
        )
        pts.refund_points = refund
        monkeypatch.setattr(ai_router, "PointsService", lambda: pts)

        async def _conflict(**_kwargs):
            raise Exception(
                "duplicate key value violates unique constraint "
                '"idx_task_tracking_active_per_resource_type"'
            )

        mgr.create = AsyncMock(side_effect=_conflict)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["message"] == "Transcription already in progress"
        refund.assert_awaited_once()
        assert refund.await_args.kwargs["amount"] == 10

    @pytest.mark.asyncio
    async def test_an_unrelated_dispatch_failure_is_still_a_500(
        self, monkeypatch
    ) -> None:
        """The conflict branch must not swallow real failures — a broken
        dispatch reported as "already in progress" would be the silent
        no-op this codebase keeps banning."""
        media = {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "d/audio.m4a",
            "download_path": "d/video.mp4",
            "title": "T",
        }
        _patch_resource_resolver(monkeypatch, media)
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        async def _boom(**_kwargs):
            raise RuntimeError("DBOS is not launched")

        mgr.create = AsyncMock(side_effect=_boom)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert exc.value.status_code == 500


# ─── legacy platform_id endpoint ──────────────────────────────────


class TestTranscribeLegacy:
    def _patch_media(self, monkeypatch, media: dict):
        async def _get(_pid):
            return media

        monkeypatch.setattr(ai_router, "_get_media_or_404", _get)

    def _patch_owner_resource(self, monkeypatch):
        repo = MagicMock()
        repo.get_resource_by_media_id_and_creator = AsyncMock(
            return_value={"id": "res-9"}
        )
        monkeypatch.setattr(
            "app.repositories.resources_repository.ResourcesRepository",
            lambda: repo,
        )

    @pytest.mark.asyncio
    async def test_no_audio_but_video_dispatches_extract_chain(
        self, monkeypatch
    ) -> None:
        media = {
            "id": "329",
            "platform_id": "pf-legacy",
            "extract_audio_path": "",
            "music_download_path": "",
            "download_path": "bilibili/329/video.mp4",
            "title": "Legacy clip",
        }
        self._patch_media(monkeypatch, media)
        self._patch_owner_resource(monkeypatch)
        monkeypatch.setattr(
            ai_router, "get_team_id_for_user", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(ai_router, "PointsService", lambda: MagicMock())
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription("pf-legacy", _auth(), None)

        assert dispatched[0]["name"] == "extract_audio"
        assert dispatched[0]["dbos_workflow_kwargs"]["chain_transcription"] is True
        assert res["extracting_audio"] is True

    @pytest.mark.asyncio
    async def test_no_audio_no_video_returns_409_before_billing(
        self, monkeypatch
    ) -> None:
        media = {
            "id": "329",
            "platform_id": "pf-legacy",
            "extract_audio_path": "",
            "music_download_path": "",
            "download_path": "",  # nothing downloaded
            "title": "Nothing",
        }
        self._patch_media(monkeypatch, media)

        team_spy = AsyncMock(return_value="team-1")
        monkeypatch.setattr(ai_router, "get_team_id_for_user", team_spy)
        consume = AsyncMock()
        pts = MagicMock()
        pts.check_and_consume = consume
        pts.ensure_team_quota = AsyncMock()
        monkeypatch.setattr(ai_router, "PointsService", lambda: pts)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await ai_router.trigger_transcription("pf-legacy", _auth(), None)

        assert exc.value.status_code == 409
        assert "no audio track available" in exc.value.detail
        # The classification runs before the points block is even entered.
        consume.assert_not_awaited()
        team_spy.assert_not_awaited()


# ─── extract_audio_workflow chain routing ─────────────────────────


class TestExtractAudioWorkflowChainRouting:
    """The workflow body picks the chain helper by ``chain_transcription``:
    True → unconditional transcription dispatch (manual click), False →
    tag-driven (post-download auto-chain).

    The body is a live ``@DBOS.workflow()`` coroutine that raises
    "invoked before DBOS initialized" unless a DBOS runtime is up, so —
    matching the existing convention in test_flow_id_and_extract_audio.py
    — this pins the routing by reading the source. The behavioural half
    (that the endpoints actually dispatch extract_audio with the flag) is
    covered above with the fake orchestrator.
    """

    @staticmethod
    def _wf_source() -> str:
        import importlib
        import inspect

        mod = importlib.import_module("app.workflows.extract_audio")
        return inspect.getsource(mod.extract_audio_workflow)

    def test_signature_has_chain_transcription_flag(self) -> None:
        import inspect

        from app.workflows.extract_audio import extract_audio_workflow

        sig = inspect.signature(extract_audio_workflow)
        param = sig.parameters["chain_transcription"]
        assert param.default is False, "flag must default False (auto-chain path)"

    def test_routes_true_to_unconditional_false_to_tag_driven(self) -> None:
        source = self._wf_source()
        assert "if chain_transcription:" in source
        assert "chain_transcription_unconditional(" in source
        assert "chain_transcript_summary_for_tags(" in source
        # The unconditional helper must sit under the chain_transcription
        # branch, the tag-driven one under else — verify ordering.
        true_idx = source.index("chain_transcription_unconditional(")
        else_idx = source.index("else:", true_idx)
        tagged_idx = source.index("chain_transcript_summary_for_tags(", else_idx)
        assert true_idx < else_idx < tagged_idx


class TestExtractAudioChainFailurePropagation:
    """When a manual /transcribe click chains through extract_audio and the
    extraction fails, the resource's transcript_status must flip to 'failed'
    so the frontend Transcript tab stops its "Transcribing..." spinner.
    Without this the row stays 'none' and the spinner never resolves (the
    reported bug: no audio track → ffmpeg fails → forever-spinning tab)."""

    @staticmethod
    def _wf_source() -> str:
        import importlib
        import inspect

        mod = importlib.import_module("app.workflows.extract_audio")
        return inspect.getsource(mod.extract_audio_workflow)

    @pytest.mark.asyncio
    async def test_mark_transcript_failed_step_keys_on_resource_id(self) -> None:
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import app.workflows.extract_audio as m

        class _FakeWriteResult:
            rowcount = 1

        class _CapturingWriteSession:
            def __init__(self) -> None:
                self.statements: list = []

            async def execute(self, stmt):
                self.statements.append(stmt)
                return _FakeWriteResult()

        session = _CapturingWriteSession()

        @asynccontextmanager
        async def _fake_write_scope():
            yield session

        # Phase C task 1: migrated off raw db_engine.execute onto a real ORM
        # update(Resources) statement run through write_scope().
        with patch("app.db.session.write_scope", _fake_write_scope):
            await m.mark_transcript_failed_step("555")

        assert len(session.statements) == 1
        sql_text, params = str(session.statements[0].compile()), dict(
            session.statements[0].compile().params
        )
        assert "resources.id" in sql_text
        assert sql_text.count("transcript_status") >= 2  # SET + <> 'completed' guard
        assert 555 in params.values()
        assert "failed" in params.values()
        assert "completed" in params.values()

    @pytest.mark.asyncio
    async def test_mark_transcript_failed_step_swallows_write_error(self) -> None:
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import app.workflows.extract_audio as m

        class _BoomWriteSession:
            async def execute(self, stmt):
                raise RuntimeError("pg down")

        @asynccontextmanager
        async def _fake_write_scope():
            yield _BoomWriteSession()

        with patch("app.db.session.write_scope", _fake_write_scope):
            await m.mark_transcript_failed_step("1")  # must not raise

    def test_both_failure_branches_propagate_when_chained(self) -> None:
        source = self._wf_source()
        # Both the exception branch and the `if not ok:` branch must flip
        # transcript_status, gated on chain_transcription + resource_id.
        assert (
            source.count("await mark_transcript_failed_step(resource_id)") == 2
        ), "both failure branches must propagate transcript_status='failed'"
        assert "if chain_transcription and resource_id:" in source

    def test_success_path_does_not_mark_failed(self) -> None:
        source = self._wf_source()
        # The success tail returns a success dict; the failed-mark must only
        # live in the two failure branches (both before a raise).
        success_idx = source.index('return {"status": "success"')
        assert "mark_transcript_failed_step" not in source[success_idx:]


class TestChainTranscriptionUnconditional:
    """The helper dispatched by the manual chain must NOT gate on intent
    tags (unlike chain_transcript_summary_for_tags) — the click is the
    intent."""

    def test_helper_has_no_tag_gate(self) -> None:
        import importlib
        import inspect

        mod = importlib.import_module("app.tasks.download_helpers")
        source = inspect.getsource(mod.chain_transcription_unconditional)
        assert "read_resource_tag_names" not in source, (
            "manual transcribe chain must dispatch unconditionally — no "
            "Transcript/Summary tag gate"
        )
        assert "ai_transcription_workflow" in source
        assert "dbos_workflow_id=tr_wf_id" in source
        assert "workflow_id=tr_wf_id" in source
