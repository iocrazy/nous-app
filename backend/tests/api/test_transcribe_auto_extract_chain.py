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


FLOW_ID = "flow-uuid-1"


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
    # Both manual transcribe endpoints create a task_flows parent row before
    # dispatching, so the chain's steps group into ONE Task Center step card.
    # Has to be an AsyncMock: a bare MagicMock attribute returns a non-
    # awaitable, which would blow up inside the dispatch try/except and turn
    # every test in this file into a 500 for the wrong reason.
    fake_mgr.create_flow = AsyncMock(return_value=FLOW_ID)

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
    the probe goes back to empty, the endpoint dispatches, and the
    assertions below fail — which is exactly the bug being fixed.

    ``chains`` is the recorded chain_transcription intent of the row it
    serves; ``None`` models a row written before that field existed.
    """

    def __init__(self, task_type: str, chains=None, workflow_id="wf-existing"):
        self._task_type = task_type
        self._chains = chains
        self._workflow_id = workflow_id
        self.seen_sql: str | None = None

    async def execute(self, stmt, *_a, **_k):
        from sqlalchemy.dialects import postgresql

        self.seen_sql = str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        rows = (
            [
                {
                    "dbos_workflow_id": self._workflow_id,
                    "resource_id": "res-1",
                    "task_type": self._task_type,
                    "status": "processing",
                    "phase": "in_progress",
                    "task_metadata": (
                        None
                        if self._chains is None
                        else {"chain_transcription": self._chains}
                    ),
                }
            ]
            if self._task_type in self.seen_sql
            else []
        )

        class _Result:
            @staticmethod
            def mappings():
                class _M:
                    @staticmethod
                    def all():
                        return rows

                return _M()

        return _Result()


def _patch_dedup_session(monkeypatch, session):
    import app.db.session as dbs

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(dbs, "read_scope", _scope)


class TestTranscribeDedup:
    def _no_audio_media(self) -> dict:
        return {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "",
            "music_download_path": "",
            "download_path": "bilibili/329/video.mp4",
            "title": "Old Bilibili clip",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "active_type,chains",
        [("ai_transcription", None), ("extract_audio", True)],
    )
    async def test_a_task_that_will_transcribe_short_circuits(
        self, monkeypatch, active_type, chains
    ) -> None:
        """``extract_audio`` is the half that used to be missed: a video with
        no audio track yet is transcribed via extract_audio(chain=True), so
        during that whole window the dedup found nothing, dispatched again,
        and tripped migration 121's unique index into a 500.

        Both rows here really do end in a transcript, so "already in
        progress" is a true statement."""
        _patch_resource_resolver(monkeypatch, self._no_audio_media())
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)
        _patch_dedup_session(
            monkeypatch, _ActiveTaskSession(active_type, chains=chains)
        )

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["message"] == "Transcription already in progress"
        assert res["transcription_pending_audio"] is False
        # The contract task-1b-status.md hands to the frontend: "already in
        # progress" always means "and you were not charged for it".
        assert res["points_charged"] == 0
        assert dispatched == []
        assert created == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("chains", [False, None])
    async def test_a_non_chaining_extract_audio_is_not_reported_as_transcribing(
        self, monkeypatch, chains
    ) -> None:
        """THE silent no-op this rework exists for.

        The post-download auto-extract (download.py) and the "Extract Audio"
        button create `extract_audio` WITHOUT chain_transcription, so they
        end in `chain_transcript_summary_for_tags`, which returns early
        unless the resource carries intent tags — production: 14/14
        historical rows are of this kind. Answering "Transcription already
        in progress" there is a 200 that promises work nobody will do:
        the user waits, and no transcript ever appears.

        `chains=None` is every row written before the intent was recorded —
        it must degrade to the honest answer, not to the convenient one.
        """
        _patch_resource_resolver(monkeypatch, self._no_audio_media())
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)
        _patch_dedup_session(
            monkeypatch,
            _ActiveTaskSession("extract_audio", chains=chains, workflow_id="wf-audio"),
        )

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["message"] != "Transcription already in progress"
        # Machine-readable, so the caller can act without parsing prose.
        assert res["transcription_pending_audio"] is True
        assert res["blocking_task_id"] == "wf-audio"
        assert res["points_charged"] == 0
        assert "retry" in res["message"]
        # Still no dispatch — the unique index would reject it anyway.
        assert dispatched == []
        assert created == []

    def _audio_ready_media(self) -> dict:
        return {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "d/audio.m4a",
            "download_path": "d/video.mp4",
            "title": "T",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("chains", [False, None])
    async def test_audio_already_on_disk_is_not_blocked_by_an_audio_only_run(
        self, monkeypatch, chains
    ) -> None:
        """With audio on disk we insert `ai_transcription`, and migration
        121's index is per (resource_id, task_type) — a non-chaining
        `extract_audio` is a different task_type, so it neither blocks that
        insert nor produces a transcript. Refusing here would be a rejection
        that protects nothing: the user is told to retry later when the
        dispatch would have worked right now.
        """
        _patch_resource_resolver(monkeypatch, self._audio_ready_media())
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)
        _patch_dedup_session(
            monkeypatch, _ActiveTaskSession("extract_audio", chains=chains)
        )

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["message"] == "Transcription queued"
        assert res["transcription_pending_audio"] is False
        assert [d["name"] for d in dispatched] == ["ai_transcription"]
        assert created[0]["task_type"] == "ai_transcription"

    @pytest.mark.asyncio
    async def test_audio_already_on_disk_still_defers_to_a_chaining_run(
        self, monkeypatch
    ) -> None:
        """The other half of the same rule: a run that WILL transcribe makes
        "already in progress" true, and dispatching again would double-charge
        for the same transcript."""
        _patch_resource_resolver(monkeypatch, self._audio_ready_media())
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)
        _patch_dedup_session(
            monkeypatch, _ActiveTaskSession("extract_audio", chains=True)
        )

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["message"] == "Transcription already in progress"
        assert res["points_charged"] == 0
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_a_blocker_that_finished_first_yields_a_null_task_to_wait_on(
        self, monkeypatch
    ) -> None:
        """Degenerate race: our INSERT lost, but by the time we re-read, the
        winner had finished. Nothing is running, so `blocking_task_id: None`
        tells the caller to retry now rather than wait on a task id."""
        _patch_resource_resolver(monkeypatch, self._no_audio_media())
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        class _AlwaysEmptySession:
            async def execute(self, *_a, **_k):
                class _Result:
                    @staticmethod
                    def mappings():
                        class _M:
                            @staticmethod
                            def all():
                                return []

                        return _M()

                return _Result()

        _patch_dedup_session(monkeypatch, _AlwaysEmptySession())

        async def _conflict(**_kwargs):
            raise Exception(
                "duplicate key value violates unique constraint "
                '"idx_task_tracking_active_per_resource_type"'
            )

        mgr.create = AsyncMock(side_effect=_conflict)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["transcription_pending_audio"] is True
        assert res["blocking_task_id"] is None
        assert res["points_charged"] == 0
        assert dispatched == []

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
    @pytest.mark.parametrize(
        "chains,expect_in_progress",
        [(True, True), (False, False), (None, False)],
    )
    async def test_losing_the_extract_audio_race_answers_by_the_winners_intent(
        self, monkeypatch, chains, expect_in_progress
    ) -> None:
        """TOCTOU on the extract_audio slot: our dedup found nothing, but
        between the SELECT and the INSERT someone created one and migration
        121's unique index rejected ours.

        The unique index is keyed on (resource_id, task_type) and cannot see
        intent, so the winner may or may not transcribe — the endpoint must
        re-read and answer accordingly rather than assuming the convenient
        case.
        """
        _patch_resource_resolver(monkeypatch, self._no_audio_media())
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        # Empty on the dedup probe, populated on the conflict re-read.
        probe = _ActiveTaskSession("extract_audio", chains=chains, workflow_id="wf-won")
        calls = {"n": 0}

        class _RaceSession:
            async def execute(self, stmt, *a, **k):
                calls["n"] += 1
                if calls["n"] == 1:

                    class _Empty:
                        @staticmethod
                        def mappings():
                            class _M:
                                @staticmethod
                                def all():
                                    return []

                            return _M()

                    return _Empty()
                return await probe.execute(stmt, *a, **k)

        _patch_dedup_session(monkeypatch, _RaceSession())

        async def _conflict(**_kwargs):
            raise Exception(
                "duplicate key value violates unique constraint "
                '"idx_task_tracking_active_per_resource_type"'
            )

        mgr.create = AsyncMock(side_effect=_conflict)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert dispatched == []
        assert res["points_charged"] == 0
        if expect_in_progress:
            assert res["message"] == "Transcription already in progress"
            assert res["transcription_pending_audio"] is False
        else:
            assert res["message"] != "Transcription already in progress"
            assert res["transcription_pending_audio"] is True
            assert res["blocking_task_id"] == "wf-won"

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


# ─── flow grouping (manual click = one pipeline root) ─────────────


class TestManualTranscribeFlowGrouping:
    """A manual Transcribe click must produce ONE step card in the Task
    Center, not loose rows.

    The Task Center groups rows client-side by ``task_tracking.flow_id``
    (frontend/components/TaskCenter/flowGrouping.ts) and renders each group
    as a "N/N steps" card. Before this, the URL parse/download pipelines
    created a ``task_flows`` root but the manual transcribe endpoints did
    not — so the very chain that most needs the grouping (extract_audio →
    ai_transcription, two rows, minutes apart) showed up as two unrelated
    single rows and the user had no way to tell they belonged together.

    Two shapes, one flow either way:
      audio on disk → 1 row  (ai_transcription)
      no audio      → 2 rows (extract_audio, then the ai_transcription the
                      workflow chains) — which means flow_id has to travel
                      into the workflow kwargs too, not just onto the first
                      row. The last test here closes that loop by running
                      the chain helper itself.
    """

    @staticmethod
    def _media(*, audio: bool) -> dict:
        return {
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": "d/audio.m4a" if audio else "",
            "music_download_path": "",
            "download_path": "d/video.mp4",
            "title": "Clip title",
        }

    @pytest.mark.asyncio
    async def test_audio_ready_single_step_still_gets_a_flow(self, monkeypatch) -> None:
        """One-step chains are flows too — a 1/1 step card is the normal
        rendering, and it keeps the presentation consistent regardless of
        whether the media happened to have its audio already."""
        _patch_resource_resolver(monkeypatch, self._media(audio=True))
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        mgr.create_flow.assert_awaited_once()
        flow_kwargs = mgr.create_flow.await_args.kwargs
        assert flow_kwargs["user_id"] == "user-1"
        assert flow_kwargs["name"] == "Transcribe Clip title"
        assert len(created) == 1
        assert created[0]["task_type"] == "ai_transcription"
        assert created[0]["flow_id"] == FLOW_ID

    @pytest.mark.asyncio
    async def test_extract_chain_puts_both_steps_on_one_flow(self, monkeypatch) -> None:
        """The extract_audio row AND the workflow kwargs must carry the same
        flow_id. Dropping it from the kwargs is the silent half of the bug:
        step 1 would be grouped, step 2 would appear as an orphan row minutes
        later."""
        _patch_resource_resolver(monkeypatch, self._media(audio=False))
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        mgr.create_flow.assert_awaited_once()  # one click == one flow
        assert len(created) == 1
        assert created[0]["task_type"] == "extract_audio"
        assert created[0]["flow_id"] == FLOW_ID
        kwargs = dispatched[0]["dbos_workflow_kwargs"]
        assert kwargs["chain_transcription"] is True
        assert kwargs["flow_id"] == FLOW_ID, (
            "extract_audio must hand the flow to the transcription it chains, "
            "otherwise step 2 lands outside the step card"
        )

    @pytest.mark.asyncio
    async def test_legacy_platform_id_endpoint_groups_the_same_chain(
        self, monkeypatch
    ) -> None:
        """The deprecated platform_id endpoint is still what the homepage
        MediaCard's Transcribe button calls, so it needs the same flow —
        otherwise whether the user gets a step card depends on which button
        they clicked."""
        media = self._media(audio=False)
        media["platform_id"] = "pf-legacy"

        async def _get(_pid):
            return media

        monkeypatch.setattr(ai_router, "_get_media_or_404", _get)
        repo = MagicMock()
        repo.get_resource_by_media_id_and_creator = AsyncMock(
            return_value={"id": "res-9"}
        )
        monkeypatch.setattr(
            "app.repositories.resources_repository.ResourcesRepository",
            lambda: repo,
        )
        monkeypatch.setattr(
            ai_router, "get_team_id_for_user", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(ai_router, "PointsService", lambda: MagicMock())
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)

        await ai_router.trigger_transcription("pf-legacy", _auth(), None)

        mgr.create_flow.assert_awaited_once()
        assert mgr.create_flow.await_args.kwargs["name"] == "Transcribe Clip title"
        assert created[0]["flow_id"] == FLOW_ID
        assert dispatched[0]["dbos_workflow_kwargs"]["flow_id"] == FLOW_ID

    @pytest.mark.asyncio
    async def test_flow_creation_failure_does_not_block_dispatch(
        self, monkeypatch
    ) -> None:
        """``create_flow`` is best-effort and returns None on failure.
        Grouping is presentation — losing it must degrade to the old
        un-grouped rows, never to a failed transcription."""
        _patch_resource_resolver(monkeypatch, self._media(audio=False))
        _patch_no_nous_billing(monkeypatch)
        dispatched: list = []
        created: list = []
        mgr = _patch_common(monkeypatch, dispatched, created)
        mgr.create_flow = AsyncMock(return_value=None)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["extracting_audio"] is True
        assert len(dispatched) == 1
        assert created[0]["flow_id"] is None
        assert dispatched[0]["dbos_workflow_kwargs"]["flow_id"] is None

    @pytest.mark.asyncio
    async def test_chained_transcription_row_lands_on_the_given_flow(
        self, monkeypatch
    ) -> None:
        """Closes the loop: the workflow hands ``flow_id`` to
        ``chain_transcription_unconditional``, which must write it onto the
        ai_transcription row it creates. That row is step 2 of the card."""
        from app.tasks import download_helpers

        media_repo = MagicMock()
        media_repo.get_by_platform_id = AsyncMock(
            return_value={"id": 111, "title": "Clip title"}
        )
        monkeypatch.setattr(
            "app.repositories.media_repository.MediaRepository", lambda: media_repo
        )
        res_repo = MagicMock()
        res_repo.get_resource_by_media_id_and_creator = AsyncMock(
            return_value={"id": "res-1"}
        )
        monkeypatch.setattr(
            "app.repositories.resources_repository.ResourcesRepository",
            lambda: res_repo,
        )
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        await download_helpers.chain_transcription_unconditional(
            "pf-1", "user-1", flow_id=FLOW_ID, video_title="Clip title"
        )

        # The helper swallows its own exceptions (it must never fail the
        # extract_audio workflow), so assert the row exists BEFORE reading it
        # — otherwise a broken mock would make this test vacuously green.
        assert len(created) == 1, "no ai_transcription row was created at all"
        assert created[0]["task_type"] == "ai_transcription"
        assert created[0]["flow_id"] == FLOW_ID
        assert len(dispatched) == 1
        assert dispatched[0]["name"] == "ai_transcription"
