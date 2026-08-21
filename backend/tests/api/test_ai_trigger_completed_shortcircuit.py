# backend/tests/api/test_ai_trigger_completed_shortcircuit.py

"""Behavioural tests for the already-finished short-circuit on the two
by-resource AI trigger endpoints.

Production incident (2026-08-20): resource 340888655925500 finished a
transcription at 12:29 and was billed for a second, full one at 12:31. The
frontend trigger paths decide from a resource row they cached BEFORE the
first run finished, so the row still said "not transcribed"; the endpoint's
dedup only ever looked for work IN FLIGHT, and by 12:31 there was none. Two
correct-looking layers, one paid duplicate.

The fix answers "the content you asked for already exists" — 200, typed,
nothing dispatched, nothing charged — and keeps an explicit ``force=true``
way in for a deliberate re-run.

The predicate is a conjunction (status column ``completed`` AND readable
content present), and both halves are pinned below: drop either one and a
test goes red. That matters because each half alone breaks a real path —
column-only would short-circuit forever on a resource whose content row is
gone, content-only would swallow the detail panel's Retry after a run that
failed on top of an earlier success (both tables upsert by resource_id, so
the old row survives a later failure).

These call the endpoint coroutines directly with every module boundary
mocked (resolver, content probe, dedup read_scope, points, task manager,
workflow dispatch) — no DB, no DBOS runtime. Same harness style as
test_transcribe_auto_extract_chain.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import ai_router
from app.core.deps import AuthContext

FLOW_ID = "flow-uuid-1"
TEAM_ID = "team-1"


def _auth() -> AuthContext:
    return AuthContext(user_id="user-1", auth_type="jwt")


# ─── boundaries ───────────────────────────────────────────────────


class _NoActiveSession:
    """Dedup probe that finds nothing in flight (both the transcribe
    endpoint's ``.mappings().all()`` shape and the summary endpoint's
    ``.first()`` shape)."""

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.first.return_value = None
        result.mappings.return_value.all.return_value = []
        return result


@asynccontextmanager
async def _fake_read_scope():
    yield _NoActiveSession()


def _patch_common(monkeypatch, dispatched: list, created: list):
    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _fake_read_scope)

    async def _create(**kwargs):
        created.append(kwargs)
        return "task-row-id"

    fake_mgr = MagicMock()
    fake_mgr.create = AsyncMock(side_effect=_create)
    fake_mgr.fail = AsyncMock()
    fake_mgr.create_flow = AsyncMock(return_value=FLOW_ID)

    import app.services.infra.unified_task_manager as utm

    monkeypatch.setattr(utm, "get_task_manager", lambda: fake_mgr)

    async def _start(name, **kwargs):
        dispatched.append({"name": name, **kwargs})

    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock(side_effect=_start))

    # Summary dispatch looks for a transcription flow to join; irrelevant
    # here and it would otherwise hit the DB.
    monkeypatch.setattr(
        ai_router, "_find_joinable_flow_id", AsyncMock(return_value=None)
    )
    return fake_mgr


def _patch_resource(
    monkeypatch, *, transcript_status=None, summary_status=None, media=None
):
    """Resolver returning a resource row carrying the AI status columns —
    the same shape ``_resources_row_to_dict`` produces."""
    resource = {
        "id": "res-1",
        "media_id": "111",
        "creator_id": "user-1",
        "transcript_status": transcript_status,
        "summary_status": summary_status,
    }
    media = media or {
        "id": "111",
        "platform_id": "pf-1",
        "extract_audio_path": "d/audio.m4a",
        "download_path": "d/video.mp4",
        "title": "T",
    }

    async def _resolve(_rid, _uid):
        return resource, media["platform_id"], media

    monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _resolve)
    return resource, media


def _patch_ai_repo(monkeypatch, *, transcript=None, summary=None):
    """The content half of the conjunction. ``None`` = no row."""
    repo = MagicMock()
    repo.get_transcript = AsyncMock(return_value=transcript)
    repo.get_summary = AsyncMock(return_value=summary)
    monkeypatch.setattr(ai_router, "get_ai_repository", lambda: repo)
    return repo


def _patch_billing_spy(monkeypatch, *, transcription_model="nous-whisper"):
    """Billing wired so a dispatch REALLY would charge — that is what makes
    "nothing charged" falsifiable rather than vacuous. The transcribe
    endpoint only bills for a ``nous-*`` model, so the settings probe hands
    one back; the summary endpoint bills whenever a team exists."""
    settings_repo = MagicMock()
    settings_repo.get_by_user_id = AsyncMock(
        return_value={
            "settings_json": {
                "ai_settings": {
                    "task_assignment": {"transcription": transcription_model}
                }
            }
        }
    )
    monkeypatch.setattr(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        lambda: settings_repo,
    )
    monkeypatch.setattr(
        ai_router, "get_team_id_for_user", AsyncMock(return_value=TEAM_ID)
    )

    nous_repo = MagicMock()
    nous_repo.get_by_name = AsyncMock(
        return_value={
            "is_enabled": True,
            "pricing_type": "per_hour",
            "pricing_value": 6,
        }
    )
    monkeypatch.setattr(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        lambda: nous_repo,
    )

    points = MagicMock()
    points.ensure_team_quota = AsyncMock()
    points.check_and_consume = AsyncMock(
        return_value={"success": True, "points_cost": 7}
    )
    points.refund_points = AsyncMock()
    monkeypatch.setattr(ai_router, "PointsService", lambda: points)
    return points


# ─── transcribe ───────────────────────────────────────────────────


class TestTranscribeAlreadyTranscribed:
    @pytest.mark.asyncio
    async def test_completed_with_transcript_short_circuits_unbilled(
        self, monkeypatch
    ) -> None:
        """The incident, replayed: a finished transcript, triggered again."""
        _patch_resource(monkeypatch, transcript_status="completed")
        _patch_ai_repo(monkeypatch, transcript={"full_text": "hello world"})
        points = _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["already_transcribed"] is True
        assert res["message"] == "Transcript already exists"
        assert res["points_charged"] == 0
        assert res["transcription_pending_audio"] is False
        assert dispatched == [], "no workflow may be dispatched"
        assert created == [], "no task_tracking row may be created"
        points.check_and_consume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_dispatches_a_fresh_billed_run(self, monkeypatch) -> None:
        _patch_resource(monkeypatch, transcript_status="completed")
        _patch_ai_repo(monkeypatch, transcript={"full_text": "hello world"})
        points = _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource(
            "res-1", _auth(), None, force=True
        )

        assert res["already_transcribed"] is False
        assert res["message"] == "Transcription queued"
        assert [d["name"] for d in dispatched] == ["ai_transcription"]
        points.check_and_consume.assert_awaited()

    @pytest.mark.asyncio
    async def test_completed_column_without_content_still_dispatches(
        self, monkeypatch
    ) -> None:
        """Conjunction half A. A column claiming ``completed`` with no
        transcript row must NOT short-circuit — otherwise the resource is
        stuck: no content to read and no run will ever be dispatched."""
        _patch_resource(monkeypatch, transcript_status="completed")
        _patch_ai_repo(monkeypatch, transcript=None)
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["already_transcribed"] is False
        assert [d["name"] for d in dispatched] == ["ai_transcription"]

    @pytest.mark.asyncio
    async def test_failed_column_with_stale_content_still_dispatches(
        self, monkeypatch
    ) -> None:
        """Conjunction half B. ``resource_transcripts`` upserts by
        resource_id, so a run that fails after an earlier success leaves the
        old row behind. The detail panel's Retry button has to work."""
        _patch_resource(monkeypatch, transcript_status="failed")
        _patch_ai_repo(monkeypatch, transcript={"full_text": "stale but present"})
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["already_transcribed"] is False
        assert [d["name"] for d in dispatched] == ["ai_transcription"]

    @pytest.mark.asyncio
    async def test_empty_transcript_text_is_not_content(self, monkeypatch) -> None:
        """A row whose ``full_text`` is empty gives the agent nothing to
        read — that is a run worth paying for, not a short-circuit."""
        _patch_resource(monkeypatch, transcript_status="completed")
        _patch_ai_repo(monkeypatch, transcript={"full_text": ""})
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["already_transcribed"] is False
        assert [d["name"] for d in dispatched] == ["ai_transcription"]

    @pytest.mark.asyncio
    async def test_transcript_wins_over_a_missing_audio_file(self, monkeypatch) -> None:
        """The short-circuit runs BEFORE the audio-readiness gate: content we
        already hold must not be denied with 409 "no audio track"."""
        _patch_resource(
            monkeypatch,
            transcript_status="completed",
            media={
                "id": "111",
                "platform_id": "pf-1",
                "extract_audio_path": "",
                "music_download_path": "",
                "download_path": "douyin/gallery-dir",
                "title": "Gallery",
            },
        )
        _patch_ai_repo(monkeypatch, transcript={"full_text": "hello"})
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        assert res["already_transcribed"] is True
        assert dispatched == []


class TestTranscribeDedupUnchanged:
    """The in-flight arms must be byte-for-byte what they were, plus the new
    discriminator. A resource with nothing finished takes exactly the old
    path."""

    def test_in_progress_response_shape(self) -> None:
        res = ai_router._transcription_in_progress_response("res-1", "pf-1")
        assert res["message"] == "Transcription already in progress"
        assert res["points_charged"] == 0
        assert res["transcription_pending_audio"] is False
        assert res["already_transcribed"] is False

    def test_pending_audio_response_shape(self) -> None:
        res = ai_router._audio_extraction_blocks_response("res-1", "pf-1", "wf-9")
        assert res["transcription_pending_audio"] is True
        assert res["blocking_task_id"] == "wf-9"
        assert res["points_charged"] == 0
        # The pending-audio arm is NOT "already transcribed" — conflating
        # them would resurrect the silent no-op this discriminator exists
        # to prevent.
        assert res["already_transcribed"] is False

    @pytest.mark.asyncio
    async def test_uncompleted_resource_reaches_the_dedup(self, monkeypatch) -> None:
        """Not completed → the content probe answers False on the column
        alone and never queries, and the request continues into the old
        dedup + dispatch path."""
        _patch_resource(monkeypatch, transcript_status="none")
        repo = _patch_ai_repo(monkeypatch, transcript={"full_text": "unused"})
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_transcription_by_resource("res-1", _auth(), None)

        repo.get_transcript.assert_not_awaited()
        assert res["already_transcribed"] is False
        assert [d["name"] for d in dispatched] == ["ai_transcription"]


# ─── summarize ────────────────────────────────────────────────────


class TestSummarizeAlreadySummarized:
    @pytest.mark.asyncio
    async def test_completed_with_summary_short_circuits_unbilled(
        self, monkeypatch
    ) -> None:
        _patch_resource(
            monkeypatch, transcript_status="completed", summary_status="completed"
        )
        _patch_ai_repo(
            monkeypatch,
            transcript={"full_text": "hello"},
            summary={"summary_text": "a summary"},
        )
        points = _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert res["already_summarized"] is True
        assert res["message"] == "Summary already exists"
        assert res["points_charged"] == 0
        assert dispatched == []
        assert created == []
        points.check_and_consume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_dispatches_a_fresh_billed_run(self, monkeypatch) -> None:
        _patch_resource(
            monkeypatch, transcript_status="completed", summary_status="completed"
        )
        _patch_ai_repo(
            monkeypatch,
            transcript={"full_text": "hello"},
            summary={"summary_text": "a summary"},
        )
        points = _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_summary_by_resource(
            "res-1", _auth(), None, force=True
        )

        assert res["already_summarized"] is False
        assert res["message"] == "Summary generation queued"
        assert [d["name"] for d in dispatched] == ["ai_summary"]
        points.check_and_consume.assert_awaited()

    @pytest.mark.asyncio
    async def test_completed_column_without_content_still_dispatches(
        self, monkeypatch
    ) -> None:
        _patch_resource(
            monkeypatch, transcript_status="completed", summary_status="completed"
        )
        _patch_ai_repo(monkeypatch, transcript={"full_text": "hello"}, summary=None)
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert res["already_summarized"] is False
        assert [d["name"] for d in dispatched] == ["ai_summary"]

    @pytest.mark.asyncio
    async def test_failed_column_with_stale_content_still_dispatches(
        self, monkeypatch
    ) -> None:
        _patch_resource(
            monkeypatch, transcript_status="completed", summary_status="failed"
        )
        _patch_ai_repo(
            monkeypatch,
            transcript={"full_text": "hello"},
            summary={"summary_text": "stale but present"},
        )
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert res["already_summarized"] is False
        assert [d["name"] for d in dispatched] == ["ai_summary"]

    @pytest.mark.asyncio
    async def test_no_transcript_branch_keeps_its_shape(self, monkeypatch) -> None:
        """Zero semantic change to the "no transcript yet" arm — it still
        dispatches transcription and tells the caller to come back, now with
        the discriminator present."""
        _patch_resource(monkeypatch, transcript_status="none", summary_status="none")
        _patch_ai_repo(monkeypatch, transcript=None, summary=None)
        _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert res["already_summarized"] is False
        assert "trigger summary again" in res["message"]
        assert [d["name"] for d in dispatched] == ["ai_transcription"]


class TestSummarizeDedupUnchanged:
    @pytest.mark.asyncio
    async def test_in_flight_summary_still_answers_already_in_progress(
        self, monkeypatch
    ) -> None:
        _patch_resource(
            monkeypatch, transcript_status="completed", summary_status="processing"
        )
        _patch_ai_repo(monkeypatch, transcript={"full_text": "hello"}, summary=None)
        points = _patch_billing_spy(monkeypatch)
        dispatched: list = []
        created: list = []
        _patch_common(monkeypatch, dispatched, created)

        class _ActiveSession:
            async def execute(self, *_a, **_k):
                result = MagicMock()
                result.first.return_value = ("wf-existing",)
                return result

        @asynccontextmanager
        async def _scope():
            yield _ActiveSession()

        import app.db.session as dbs

        monkeypatch.setattr(dbs, "read_scope", _scope)

        res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

        assert res["message"] == "Summary already in progress"
        assert res["points_charged"] == 0
        assert res["already_summarized"] is False
        assert dispatched == []
        points.check_and_consume.assert_not_awaited()
