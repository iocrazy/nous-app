"""build_evidence_bundle: each source independent, failures are facts."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.services.issues.verification import evidence as ev

pytestmark = pytest.mark.unit  # asyncio_mode = "auto" runs the async tests


async def test_bundle_from_stubbed_sources(monkeypatch):
    d = (
        ev.Deliverable("script_shot", "10", 1, "r1", "t"),
        ev.Deliverable("generated_media", "5", 1, "r1", "t"),
    )
    monkeypatch.setattr(ev, "_load_deliverables", AsyncMock(return_value=d))
    shots = (ev.ShotFacts(10, 1, 1, "wide", "eye", "d", None, "draft"),)
    load_shots = AsyncMock(return_value=shots)
    monkeypatch.setattr(ev, "_load_shots", load_shots)
    monkeypatch.setattr(
        ev,
        "_load_scene_scope",
        AsyncMock(return_value=(ev.SceneFacts(1, "1", 1, False, 1),)),
    )
    monkeypatch.setattr(ev, "_load_prior_texts", AsyncMock(return_value=("earlier",)))
    b = await ev.build_evidence_bundle(
        issue_id=1, run_id="r1", final_text="x" * 13000, session_id="s", user_id="u"
    )
    load_shots.assert_awaited_once_with([10])
    assert b.media_count == 1 and b.shots == shots and b.prior_texts == ("earlier",)
    assert b.final_text_truncated and len(b.final_text) == ev.FINAL_TEXT_MAX_CHARS
    assert b.errors == ()


async def test_a_failing_source_becomes_an_error_fact(monkeypatch):
    monkeypatch.setattr(
        ev, "_load_deliverables", AsyncMock(side_effect=RuntimeError("db"))
    )
    monkeypatch.setattr(ev, "_load_shots", AsyncMock(return_value=()))
    monkeypatch.setattr(ev, "_load_scene_scope", AsyncMock(return_value=()))
    monkeypatch.setattr(ev, "_load_prior_texts", AsyncMock(return_value=()))
    b = await ev.build_evidence_bundle(
        issue_id=1, run_id=None, final_text="t", session_id=None, user_id=None
    )
    assert b.errors == ("deliverables: RuntimeError",)
    assert b.deliverables == ()


async def test_prior_texts_drop_the_final_text_and_cap(monkeypatch):
    """get_messages is chronological, so the result is too (oldest first):
    the judge prints them before the worker's final text."""
    from app.services.ai.chat import ai_library_chat_service as chat

    msgs = [{"role": "assistant", "content": f"m{i}"} for i in range(6)] + [
        {"role": "assistant", "content": "final"}
    ]

    class _Svc:
        async def get_messages(self, session_id, *, user_id, limit, newest):
            return msgs

    monkeypatch.setattr(chat, "AILibraryChatService", lambda: _Svc())
    got = await ev._load_prior_texts(
        "s", "22222222-2222-2222-2222-222222222222", "final"
    )
    assert got == ("m3", "m4", "m5")


# ── the ORM statements compile against Postgres (columns really exist) ──


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


def _capturing_scope(monkeypatch, answers):
    """read_scope stand-in: records each statement compiled for postgresql
    and answers with the next canned row list."""
    from sqlalchemy.dialects import postgresql

    compiled: list[str] = []
    queue = list(answers)

    class _Session:
        async def execute(self, stmt):
            compiled.append(str(stmt.compile(dialect=postgresql.dialect())))
            return _Result(queue.pop(0))

    @asynccontextmanager
    async def _scope():
        yield _Session()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", _scope)
    return compiled


async def test_shot_query_selects_script_shots_by_id(monkeypatch):
    compiled = _capturing_scope(monkeypatch, [[]])
    assert await ev._load_shots([10, 11]) == ()
    assert (
        "FROM public.script_shots" in compiled[0]
        and "script_shots.id IN" in compiled[0]
    )


async def test_scene_scope_counts_only_the_touched_scripts_scenes(monkeypatch):
    class _Scene:
        id, scene_number, content_version, omitted_at = 1, "1", 2, None

    compiled = _capturing_scope(monkeypatch, [[7], [_Scene()], [(1, 3)]])
    scope = await ev._load_scene_scope([1])
    assert scope == (ev.SceneFacts(1, "1", 2, False, 3),)
    assert "script_scenes.id IN" in compiled[0]
    assert "script_scenes.script_id IN" in compiled[1]
    # the shot count is bounded to those scenes, not a table-wide GROUP BY
    assert (
        "script_shots.scene_id IN" in compiled[2]
        and "GROUP BY public.script_shots.scene_id" in compiled[2]
    )
