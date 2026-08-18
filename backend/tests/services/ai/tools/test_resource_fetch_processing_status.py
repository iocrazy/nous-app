"""ResourceFetch tells "being generated" apart from "never processed".

Before this, an @-mentioned video whose transcription was still running got
the same ``"transcript not available; resource not yet processed"`` as one
that had never been touched — so the agent reported a flat failure while a
task was actively running (spec 2026-08-17 §1-F1, RECON#1). The status
columns already live on ``resources``; the fix is to select them alongside
the access check and branch the empty-content message on them.

Scope discipline: the tool must NEVER kick off processing itself (spec §2 —
agent-initiated consumption tasks are a separate permission design). The
last test pins that.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _no_active_tasks(monkeypatch):
    """Default the task_tracking half of the effective status to "nothing
    running" so each test below isolates the COLUMN semantics.

    The columns only ever hold terminal values in production, so the
    in-flight signal now comes from task_tracking (Task 1b). The tests that
    exercise THAT half stub this seam themselves (see
    ``_with_active_task``) or live in tests/services/ai/
    test_resource_ai_status.py.
    """
    import app.services.ai.resource_ai_status as status_module

    async def _none(resource_ids):
        return {}

    monkeypatch.setattr(status_module, "_active_ai_tasks", _none)


def _with_active_task(monkeypatch, **fields):
    """Make the shared resolver report an in-flight task for resource "1"."""
    import app.services.ai.resource_ai_status as status_module

    async def _active(resource_ids):
        return {"1": dict(fields)}

    monkeypatch.setattr(status_module, "_active_ai_tasks", _active)


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _AccessResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _ContentResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


def _patch_sessions(monkeypatch, *, mime: str, row_extra: dict, content=None):
    """First ``read_scope`` session serves the access-check row (with the
    status columns merged in), later ones serve the (empty) content lookup."""
    import app.db.scope as scope_module
    import app.db.session as session_module

    row = {
        "id": "1",
        "mime": mime,
        "name": "clip",
        "file_path": "some/path",
        "brief": None,
        **row_extra,
    }
    captured: dict = {"access_sql": None}

    class _AccessSession:
        async def execute(self, stmt):
            from sqlalchemy.dialects import postgresql

            captured["access_sql"] = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            return _AccessResult([row])

    class _ContentSession:
        async def execute(self, _stmt):
            return _ContentResult(content)

    calls = {"n": 0}

    @asynccontextmanager
    async def _read_scope():
        calls["n"] += 1
        yield _AccessSession() if calls["n"] == 1 else _ContentSession()

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(session_module, "read_scope", _read_scope)
    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)
    return captured


async def _dispatch(monkeypatch, *, mime, mode, row_extra, content=None):
    from app.services.ai.tools import resource_fetch_tool as m

    captured = _patch_sessions(
        monkeypatch, mime=mime, row_extra=row_extra, content=content
    )
    out = await m._fetch_dispatch(resource_id="1", mode=mode, args=None, user_id="u")
    return out, captured


# ── the access check must carry the status columns ──────────────────


async def test_access_check_selects_the_status_columns(monkeypatch):
    out, captured = await _dispatch(
        monkeypatch,
        mime="video/mp4",
        mode="summary",
        row_extra={"transcript_status": "none", "summary_status": "none"},
    )
    sql = captured["access_sql"] or ""
    assert "transcript_status" in sql
    assert "summary_status" in sql


# ── transcript branch ───────────────────────────────────────────────


@pytest.mark.parametrize("status", ["processing", "pending"])
async def test_in_flight_transcript_says_it_is_being_generated(monkeypatch, status):
    out, _ = await _dispatch(
        monkeypatch,
        mime="audio/mpeg",
        mode="transcript",
        row_extra={"transcript_status": status, "summary_status": "none"},
    )
    assert "being generated" in out["error"]
    assert "retry" in out["error"]
    assert "not available" not in out["error"]


@pytest.mark.parametrize("status", ["none", "failed", "skipped"])
async def test_not_in_flight_transcript_keeps_the_existing_wording(monkeypatch, status):
    out, _ = await _dispatch(
        monkeypatch,
        mime="audio/mpeg",
        mode="transcript",
        row_extra={"transcript_status": status, "summary_status": "none"},
    )
    assert out == {"error": "transcript not available; resource not yet processed"}


async def test_transcript_status_enum_member_is_understood(monkeypatch):
    """Real rows carry ``AiTaskStatus`` members, not plain strings. Branch
    logic itself is safe (``in {"pending", ...}`` holds for a ``(str, Enum)``
    mixin) — the real hazard is any boundary that ``str()``s the value into
    user/agent-visible text, where the member renders as its repr
    (``'AiTaskStatus.PROCESSING'``). This test pins that enum members flow
    through the tool without leaking that repr."""
    from app.models._enums import AiTaskStatus

    out, _ = await _dispatch(
        monkeypatch,
        mime="audio/mpeg",
        mode="transcript",
        row_extra={
            "transcript_status": AiTaskStatus.PROCESSING,
            "summary_status": AiTaskStatus.NONE,
        },
    )
    assert "being generated" in out["error"]


# ── summary branch ──────────────────────────────────────────────────


async def test_in_flight_summary_reports_processing(monkeypatch):
    out, _ = await _dispatch(
        monkeypatch,
        mime="video/mp4",
        mode="summary",
        row_extra={"transcript_status": "completed", "summary_status": "processing"},
    )
    assert "still being processed" in out["error"]
    assert "retry" in out["error"]


async def test_summary_falls_back_to_transcript_progress(monkeypatch):
    """A summary can only follow a transcript, so a still-transcribing
    resource is "being generated" for the summary mode too — otherwise the
    common case (transcription running, summary_status still 'none') would
    report a flat failure while work is in flight."""
    out, _ = await _dispatch(
        monkeypatch,
        mime="video/mp4",
        mode="summary",
        row_extra={"transcript_status": "processing", "summary_status": "none"},
    )
    assert "still being processed" in out["error"]


async def test_idle_summary_keeps_the_existing_wording(monkeypatch):
    out, _ = await _dispatch(
        monkeypatch,
        mime="video/mp4",
        mode="summary",
        row_extra={"transcript_status": "completed", "summary_status": "failed"},
    )
    assert out == {"error": "summary not available; resource not yet processed"}


# ── present content is unaffected by status ─────────────────────────


async def test_present_content_wins_over_any_status(monkeypatch):
    out, _ = await _dispatch(
        monkeypatch,
        mime="video/mp4",
        mode="summary",
        row_extra={"transcript_status": "processing", "summary_status": "processing"},
        content="the gist",
    )
    assert out["content"] == "the gist"


# ── the in-flight half comes from task_tracking, not the column ─────


async def test_a_running_task_makes_an_idle_column_read_as_in_flight(monkeypatch):
    """The production shape: transcript_status is 'none' (nothing ever
    writes an intermediate value) while an ai_transcription workflow is
    actually running. Before Task 1b this returned the flat "not yet
    processed" failure — the bug spec §3-③ is about."""
    _with_active_task(monkeypatch, transcript_status="processing")
    out, _ = await _dispatch(
        monkeypatch,
        mime="audio/mpeg",
        mode="transcript",
        row_extra={"transcript_status": "none", "summary_status": "none"},
    )
    assert "being generated" in out["error"]
    assert "not available" not in out["error"]


async def test_a_running_transcription_also_covers_the_summary_mode(monkeypatch):
    _with_active_task(monkeypatch, transcript_status="pending")
    out, _ = await _dispatch(
        monkeypatch,
        mime="video/mp4",
        mode="summary",
        row_extra={"transcript_status": "none", "summary_status": "none"},
    )
    assert "still being processed" in out["error"]


async def test_a_failed_column_still_wins_over_a_stale_task_row(monkeypatch):
    """Terminal column beats the task table: a transcript that failed is a
    result the agent can report, not a promise to wait."""
    _with_active_task(monkeypatch, transcript_status="processing")
    out, _ = await _dispatch(
        monkeypatch,
        mime="audio/mpeg",
        mode="transcript",
        row_extra={"transcript_status": "failed", "summary_status": "none"},
    )
    assert out == {"error": "transcript not available; resource not yet processed"}


# ── scope tripwire ──────────────────────────────────────────────────


async def test_tool_never_triggers_processing_itself():
    """spec §2: agents do not start consumption tasks. The trigger helpers
    live in ai_router / the transcription+summary services; none of them may
    be reachable from this module."""
    import inspect

    from app.services.ai.tools import resource_fetch_tool as m

    src = inspect.getsource(m)
    for forbidden in (
        "trigger_transcription",
        "trigger_summary",
        "transcribe_resource",
        "summarize_resource",
        "DBOS.start_workflow",
        "enqueue",
    ):
        assert forbidden not in src, (
            f"resource_fetch_tool references {forbidden!r} — the tool must "
            "report status, never start processing (spec §2)."
        )
