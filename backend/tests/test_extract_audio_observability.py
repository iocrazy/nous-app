"""Tests for the extract_audio 3-layer observability fix.

Layer 1 — sweeper surfaces real DBOS errors instead of "never claimed".
Layer 2 — extract_audio_from_video raises AudioExtractError with the
          specific reason instead of collapsing everything to False.
Layer 3 — retry_task accepts 'lost' and re-keys the row to the fresh
          workflow id the endpoint actually dispatches with.
"""

from __future__ import annotations

import base64
import pickle

import pytest

from app.workflows.scheduled_recovery import _readable_dbos_error

# ============================================================
# Layer 1a: _readable_dbos_error
# ============================================================


class TestReadableDbosError:
    def _encode(self, exc: BaseException) -> str:
        return base64.b64encode(pickle.dumps(exc)).decode()

    def test_extracts_message_from_pickled_exception(self) -> None:
        raw = self._encode(
            RuntimeError("audio extraction failed for 7648318168977792410")
        )
        msg = _readable_dbos_error(raw)
        assert "audio extraction failed for 7648318168977792410" in msg

    def test_never_unpickles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # pickle.loads must NOT run — a poisoned payload would execute code.
        def boom(*a, **k):  # pragma: no cover - defensive
            raise AssertionError("pickle.loads was called")

        monkeypatch.setattr(pickle, "loads", boom)
        raw = self._encode(ValueError("safe extraction"))
        assert "safe extraction" in _readable_dbos_error(raw)

    def test_garbage_and_empty_fall_back(self) -> None:
        assert "unreadable" in _readable_dbos_error("not-base64!!!")
        assert "unreadable" in _readable_dbos_error(None)
        assert "unreadable" in _readable_dbos_error("")

    def test_clips_long_messages(self) -> None:
        raw = self._encode(RuntimeError("long message " + "x" * 1000))
        assert len(_readable_dbos_error(raw)) <= 300


# ============================================================
# Layer 1b: reap step — ERROR rows become real failures
# ============================================================


@pytest.fixture
def engine_rec(monkeypatch: pytest.MonkeyPatch) -> dict:
    rec: dict = {"fetch_all": [], "execute": [], "errored_rows": []}

    async def fake_fetch_all(sql: str, params=None):
        rec["fetch_all"].append({"sql": sql, "params": params})
        return rec["errored_rows"]

    async def fake_execute(sql: str, params=None):
        rec["execute"].append({"sql": sql, "params": params})
        return 1

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr("app.db.engine.execute", fake_execute)
    # Boot grace would short-circuit the whole step.
    monkeypatch.setattr("app.workflows.sweep_guard.within_boot_grace", lambda: False)
    return rec


@pytest.mark.asyncio
async def test_reap_converts_dbos_error_rows_to_failed(engine_rec: dict) -> None:
    from app.workflows.scheduled_recovery import reap_stuck_pending_tasks_step

    raw = base64.b64encode(
        pickle.dumps(RuntimeError("audio extraction failed for 123"))
    ).decode()
    engine_rec["errored_rows"] = [{"dbos_workflow_id": "wf-1", "error": raw}]

    result = await reap_stuck_pending_tasks_step()

    assert result["errored_failed"] == 1
    failed_updates = [
        c for c in engine_rec["execute"] if "error_code = 'DBOS_ERROR'" in c["sql"]
    ]
    assert len(failed_updates) == 1
    assert "audio extraction failed for 123" in failed_updates[0]["params"]["msg"]
    assert failed_updates[0]["params"]["wid"] == "wf-1"


@pytest.mark.asyncio
async def test_reap_lost_pass_excludes_terminal_dbos_rows(engine_rec: dict) -> None:
    from app.workflows.scheduled_recovery import reap_stuck_pending_tasks_step

    await reap_stuck_pending_tasks_step()

    lost_updates = [c for c in engine_rec["execute"] if "'lost'" in c["sql"]]
    assert len(lost_updates) == 1
    # ERROR rows belong to pass A; SUCCESS rows must never read "never
    # claimed" — both excluded from the generic lost sweep.
    assert "'ERROR'" in lost_updates[0]["sql"]
    assert "'SUCCESS'" in lost_updates[0]["sql"]


# ============================================================
# Layer 2: AudioExtractError carries the reason
# ============================================================


@pytest.mark.asyncio
async def test_extract_raises_named_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tasks import download_helpers as dh

    class FakeRepo:
        def __init__(self, media):
            self._media = media

        async def get_by_platform_id(self, pid):
            return self._media

    # No media row.
    monkeypatch.setattr(
        "app.repositories.media_repository.MediaRepository",
        lambda: FakeRepo(None),
    )
    with pytest.raises(dh.AudioExtractError, match="no parsed_media record"):
        dh.extract_audio_from_video("p1")

    # Media row without download_path.
    monkeypatch.setattr(
        "app.repositories.media_repository.MediaRepository",
        lambda: FakeRepo({"id": 1}),
    )
    with pytest.raises(dh.AudioExtractError, match="no download_path"):
        dh.extract_audio_from_video("p1")

    # Video file missing on disk.
    monkeypatch.setattr(
        "app.repositories.media_repository.MediaRepository",
        lambda: FakeRepo({"id": 1, "download_path": "x/y/video.mp4"}),
    )
    with pytest.raises(dh.AudioExtractError, match="missing on disk"):
        dh.extract_audio_from_video("p1")


# ============================================================
# Layer 3: retry_task accepts 'lost' + re-keys the row
# ============================================================


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v

    def mappings(self):
        return self

    def first(self):
        return self._v


class _Session:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._results.pop(0) if self._results else None)


def _patch_scopes(monkeypatch, results):
    from contextlib import asynccontextmanager

    session = _Session(results)

    @asynccontextmanager
    async def _scope():
        yield session

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)
    return session


@pytest.mark.asyncio
async def test_retry_task_accepts_lost_and_rekeys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.dialects import postgresql

    from app.services.infra.unified_task_manager import UnifiedTaskManager

    manager = UnifiedTaskManager()
    # retry_task reads the row (mappings().first()), then UPDATEs.
    session = _patch_scopes(
        monkeypatch, [{"status": "lost", "task_type": "extract_audio"}]
    )

    task = await manager.retry_task("old-wf", "u1", new_workflow_id="new-wf")
    assert task is not None
    # statements[0] = read, statements[1] = update
    params = session.statements[1].compile(dialect=postgresql.dialect()).params
    assert params["dbos_workflow_id"] == "new-wf"
    assert params["status"] == "pending"


@pytest.mark.asyncio
async def test_retry_task_still_rejects_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.infra.unified_task_manager import UnifiedTaskManager

    manager = UnifiedTaskManager()
    session = _patch_scopes(monkeypatch, [{"status": "processing"}])

    assert await manager.retry_task("wf", "u1") is None
    # non-retryable → only the read ran, no UPDATE
    assert len(session.statements) == 1
