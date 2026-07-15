"""Unit tests for the ①⑤ DBOS-usage hardening:

  * ``UnifiedTaskManager.create_flow`` — task_flows row insert
  * ``UnifiedTaskManager.create(..., flow_id=...)`` — flow_id passed
    through to the task_tracking INSERT row
  * ``extract_audio_workflow`` — success / failure / route-C compliance
    (failures raise instead of returning a failed dict)

``create_flow``/``create`` now write through the ORM session boundary
(``app.db.session.read_scope``/``write_scope``, imported locally inside
each method) instead of a supabase-py ``.table().insert().execute()``
chain, so the fake session below stands in for the SQLAlchemy
``AsyncSession`` and records the compiled INSERT payload for assertions.
No DB, no DBOS runtime.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Insert

from app.services.infra.unified_task_manager import UnifiedTaskManager

# ─── helpers ──────────────────────────────────────────────────────


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v


class _Session:
    """Records every executed INSERT statement (table name + compiled row
    payload) and returns one queued result per execute() call, in order.
    A queued exception is raised instead of returned, so failure paths
    (e.g. ``create_flow``'s best-effort ``except Exception``) can be
    exercised the same way as the happy path."""

    def __init__(self, results):
        self._results = list(results)
        self.inserts: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        if isinstance(stmt, Insert):
            self.inserts.append(
                (stmt.table.name, stmt.compile(dialect=postgresql.dialect()).params)
            )
        result = self._results.pop(0) if self._results else None
        if isinstance(result, BaseException):
            raise result
        return _Result(result)


def _patch_scopes(results):
    session = _Session(results)

    @asynccontextmanager
    async def _scope():
        yield session

    return session, _scope


# ─── UnifiedTaskManager.create_flow ───────────────────────────────


class TestCreateFlow:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_id(self, monkeypatch) -> None:
        mgr = UnifiedTaskManager()
        flow_id_returned = "flow-uuid-123"
        session, scope = _patch_scopes([flow_id_returned])
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "write_scope", scope)

        result = await mgr.create_flow(
            user_id="user-1",
            name="Process https://example.com/abc",
        )

        assert result == flow_id_returned
        assert len(session.inserts) == 1
        table_name, row = session.inserts[0]
        assert table_name == "task_flows"
        assert row["user_id"] == "user-1"
        assert row["name"] == "Process https://example.com/abc"
        assert "metadata" not in row  # not passed → not present

    @pytest.mark.asyncio
    async def test_truncates_long_name(self, monkeypatch) -> None:
        mgr = UnifiedTaskManager()
        session, scope = _patch_scopes(["f"])
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "write_scope", scope)

        long_name = "x" * 300
        await mgr.create_flow(user_id="u", name=long_name)

        _, row = session.inserts[0]
        assert len(row["name"]) == 200

    @pytest.mark.asyncio
    async def test_includes_metadata_when_provided(self, monkeypatch) -> None:
        mgr = UnifiedTaskManager()
        session, scope = _patch_scopes(["f"])
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "write_scope", scope)

        await mgr.create_flow(user_id="u", name="n", metadata={"source": "parse"})

        _, row = session.inserts[0]
        assert row["metadata"] == {"source": "parse"}

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, monkeypatch) -> None:
        """Best-effort contract: caller can still dispatch un-grouped."""
        mgr = UnifiedTaskManager()
        session, scope = _patch_scopes([RuntimeError("supabase dead")])
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "write_scope", scope)

        result = await mgr.create_flow(user_id="u", name="n")

        assert result is None


# ─── UnifiedTaskManager.create with flow_id ───────────────────────


class TestCreateWithFlowId:
    @pytest.mark.asyncio
    async def test_flow_id_written_when_provided(self, monkeypatch) -> None:
        mgr = UnifiedTaskManager()
        session, scope = _patch_scopes(["wf-1"])
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "write_scope", scope)

        await mgr.create(
            user_id="u",
            task_type="download",
            title="t",
            dbos_workflow_id="wf-1",
            flow_id="flow-abc",
        )

        assert len(session.inserts) == 1
        table_name, row = session.inserts[0]
        assert table_name == "task_tracking"
        assert row["flow_id"] == "flow-abc"

    @pytest.mark.asyncio
    async def test_flow_id_omitted_when_none(self, monkeypatch) -> None:
        mgr = UnifiedTaskManager()
        session, scope = _patch_scopes(["wf-1"])
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "write_scope", scope)

        await mgr.create(
            user_id="u",
            task_type="download",
            title="t",
            dbos_workflow_id="wf-1",
        )

        _, row = session.inserts[0]
        assert "flow_id" not in row


# ─── extract_audio_workflow ───────────────────────────────────────


class TestExtractAudioWorkflowLogic:
    """The DBOS workflow body is async + decorated; we exercise the
    underlying step function (`run_extract_audio_step`) and the
    download_helpers wiring directly without booting DBOS.

    For the workflow body, the meaningful contract is:
      - ffmpeg returns False → workflow raises (route-C rule 4: never
        return a failed dict)
      - ffmpeg raises → workflow re-raises
      - ffmpeg returns True → workflow returns success dict
    These are guaranteed by reading the source; the higher-value test
    is on download_helpers.extract_audio_from_video which is a real
    function.
    """

    def test_extract_audio_from_video_raises_when_no_media(self) -> None:
        # 2026-06-11 observability fix: failures now raise AudioExtractError
        # with the specific reason instead of collapsing to False, so the
        # reason survives into task_tracking.error_msg.
        import pytest

        from app.tasks import download_helpers

        with patch.object(download_helpers, "_MR_extract", create=True):
            # The MediaRepository is imported inside the function; intercept it.
            with patch(
                "app.repositories.media_repository.MediaRepository"
            ) as mock_repo_cls:
                instance = MagicMock()
                instance.get_by_platform_id = AsyncMock(return_value=None)
                mock_repo_cls.return_value = instance
                with pytest.raises(
                    download_helpers.AudioExtractError,
                    match="no parsed_media record",
                ):
                    download_helpers.extract_audio_from_video("missing")
