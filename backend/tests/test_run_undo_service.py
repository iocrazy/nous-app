"""Run 撤销执行服务（mig 413）：CAS 删/复原 + scene 选择性回滚 + B4 scene 级回流。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.ai.undo.run_undo_service as service_mod
from app.repositories.script_scene_repository import VersionConflict

_RUN_ID = 800100000000000009
_AGENT = f"agent:{_RUN_ID}"
_SCENE_ID = 700100000000000001


# ↓ same shape as tests/test_script_shot_ops_ledger.py's private helpers,
# extended here with `rowcount` (CAS delete/update need it) — not imported
# across test files by that module's own convention.
class _FakeResult:
    def __init__(self, *, first_row=None, scalar=None, all_rows=None, rowcount=None):
        self._first_row = first_row
        self._scalar = scalar
        self._all = all_rows or []
        self.rowcount = rowcount

    def first(self):
        return self._first_row

    def scalar(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._all, first=lambda: self._first_row)

    def all(self):
        return self._all


class _CaptureSession:
    def __init__(self, results=None):
        self.statements: list = []
        self._results = list(results or [])

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _FakeResult()


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _shot_ledger_row(i, shot_id, action, before, after, scene_id=_SCENE_ID):
    return SimpleNamespace(
        id=i,
        shot_id=shot_id,
        scene_id=scene_id,
        action=action,
        before_json=before,
        after_json=after,
        created_at=f"2026-08-09T00:00:{i:02d}Z",
    )


_FULL = {
    "shot_type": "CU",
    "camera_angle": None,
    "camera_movement": None,
    "focal_length": "85mm",
    "lighting": None,
    "description": "a",
}


def _shot_row_full(**over):
    base = dict(
        id=900,
        status="empty",
        image_url=None,
        thumbnail_url=None,
        video_url="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _op_row(seq, actor, ops, inverse, scene_id=_SCENE_ID):
    return {
        "op_seq": seq,
        "actor": actor,
        "scene_id": scene_id,
        "op_json": {"ops": ops, "inverse": inverse},
    }


def _upd(eid, text):
    return {"op": "update", "element_id": eid, "payload": {"text": text}}


@pytest.mark.asyncio
async def test_delete_cas_conditions_and_scene_level_surface_sync():
    shots_session = _CaptureSession(
        [_FakeResult(rowcount=1)]  # DELETE ... RETURNING affects 1 row
    )
    read_session = _CaptureSession(
        [
            _FakeResult(all_rows=[_shot_ledger_row(1, 900, "create", None, _FULL)]),
            _FakeResult(all_rows=[]),  # no scenes touched
        ]
    )

    write_calls = [shots_session]

    def _write_scope():
        return _ScopeCtx(write_calls.pop(0))

    fire_mock = AsyncMock()
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(service_mod, "write_scope", _write_scope),
        patch.object(service_mod, "fire_surface_sync_for_scene", fire_mock),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["shots_deleted"] == 1
    assert report["skipped"] == []
    fire_mock.assert_awaited_once_with(str(_SCENE_ID), surfaces=("storyboard",))

    delete_stmt = shots_session.statements[0]
    compiled = str(delete_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "script_shots" in compiled
    assert "status" in compiled and "'empty'" in compiled
    assert "coalesce" in compiled.lower()
    assert "image_url" in compiled
    assert "thumbnail_url" in compiled
    assert "video_url" in compiled
    # every `expected` field appears in the WHERE — None fields as IS NULL
    assert "camera_angle IS NULL" in compiled
    assert "camera_movement IS NULL" in compiled
    assert "lighting IS NULL" in compiled
    assert "shot_type" in compiled and "'CU'" in compiled
    assert "focal_length" in compiled and "'85mm'" in compiled
    assert "description" in compiled and "'a'" in compiled


@pytest.mark.asyncio
async def test_delete_rowcount_zero_rendered_row_no_surface_sync():
    shots_session = _CaptureSession(
        [
            _FakeResult(rowcount=0),  # CAS delete misses
            _FakeResult(
                first_row=_shot_row_full(status="empty", image_url="http://x/img.png")
            ),  # fetch AFTER the failed CAS
        ]
    )
    read_session = _CaptureSession(
        [
            _FakeResult(all_rows=[_shot_ledger_row(1, 900, "create", None, _FULL)]),
            _FakeResult(all_rows=[]),
        ]
    )
    write_calls = [shots_session]
    fire_mock = AsyncMock()
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(service_mod, "write_scope", lambda: _ScopeCtx(write_calls.pop(0))),
        patch.object(service_mod, "fire_surface_sync_for_scene", fire_mock),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["shots_deleted"] == 0
    assert report["skipped"] == [{"kind": "shot", "id": "900", "reason": "rendered"}]
    fire_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_rowcount_zero_row_missing_edited_after_run():
    shots_session = _CaptureSession(
        [
            _FakeResult(rowcount=0),
            _FakeResult(first_row=None),  # row no longer exists
        ]
    )
    read_session = _CaptureSession(
        [
            _FakeResult(all_rows=[_shot_ledger_row(1, 900, "create", None, _FULL)]),
            _FakeResult(all_rows=[]),
        ]
    )
    write_calls = [shots_session]
    fire_mock = AsyncMock()
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(service_mod, "write_scope", lambda: _ScopeCtx(write_calls.pop(0))),
        patch.object(service_mod, "fire_surface_sync_for_scene", fire_mock),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["skipped"] == [
        {"kind": "shot", "id": "900", "reason": "edited_after_run"}
    ]
    fire_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_revert_rowcount_zero_edited_after_run_no_surface_sync():
    shots_session = _CaptureSession([_FakeResult(rowcount=0)])
    read_session = _CaptureSession(
        [
            _FakeResult(
                all_rows=[
                    _shot_ledger_row(
                        1, 901, "update", {"shot_type": "MS"}, {"shot_type": "CU"}
                    )
                ]
            ),
            _FakeResult(all_rows=[]),
        ]
    )
    write_calls = [shots_session]
    fire_mock = AsyncMock()
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(service_mod, "write_scope", lambda: _ScopeCtx(write_calls.pop(0))),
        patch.object(service_mod, "fire_surface_sync_for_scene", fire_mock),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["shots_reverted"] == 0
    assert report["skipped"] == [
        {"kind": "shot", "id": "901", "reason": "edited_after_run"}
    ]
    fire_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_revert_rowcount_one_no_surface_sync():
    shots_session = _CaptureSession([_FakeResult(rowcount=1)])
    read_session = _CaptureSession(
        [
            _FakeResult(
                all_rows=[
                    _shot_ledger_row(
                        1, 901, "update", {"shot_type": "MS"}, {"shot_type": "CU"}
                    )
                ]
            ),
            _FakeResult(all_rows=[]),
        ]
    )
    write_calls = [shots_session]
    fire_mock = AsyncMock()
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(service_mod, "write_scope", lambda: _ScopeCtx(write_calls.pop(0))),
        patch.object(service_mod, "fire_surface_sync_for_scene", fire_mock),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["shots_reverted"] == 1
    assert report["skipped"] == []
    fire_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_scene_version_conflict_skips_scene_no_element_counts():
    read_session = _CaptureSession(
        [
            _FakeResult(all_rows=[]),  # no shot ledger rows
            _FakeResult(all_rows=[(_SCENE_ID,)]),  # one scene touched
        ]
    )
    ops_rows = [_op_row(5, _AGENT, [_upd("el_1", "new")], [_upd("el_1", "old")])]
    repo = MagicMock()
    repo.list_ops_by_scene = AsyncMock(return_value=ops_rows)
    repo.apply_element_ops = AsyncMock(
        side_effect=VersionConflict(current_version=9, elements=[])
    )
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(
            service_mod, "get_script_scene_repository", MagicMock(return_value=repo)
        ),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["scene_elements_reverted"] == 0
    assert report["skipped"] == [
        {"kind": "scene", "id": str(_SCENE_ID), "reason": "version_conflict"}
    ]
    repo.apply_element_ops.assert_awaited_once()


@pytest.mark.asyncio
async def test_scene_undone_and_skipped_elements_reported():
    read_session = _CaptureSession(
        [
            _FakeResult(all_rows=[]),
            _FakeResult(all_rows=[(_SCENE_ID,)]),
        ]
    )
    # el_1, el_2 undone by the run; el_3 touched by the run then re-touched
    # by a foreign actor afterwards → must be skipped, not undone.
    ops_rows = [
        _op_row(5, _AGENT, [_upd("el_1", "n1")], [_upd("el_1", "o1")]),
        _op_row(6, _AGENT, [_upd("el_2", "n2")], [_upd("el_2", "o2")]),
        _op_row(7, _AGENT, [_upd("el_3", "n3")], [_upd("el_3", "o3")]),
        _op_row(8, "some-user-uuid", [_upd("el_3", "human")], [_upd("el_3", "n3")]),
    ]
    repo = MagicMock()
    repo.list_ops_by_scene = AsyncMock(return_value=ops_rows)
    repo.apply_element_ops = AsyncMock(
        return_value={"content_version": 9, "elements": []}
    )
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(
            service_mod, "get_script_scene_repository", MagicMock(return_value=repo)
        ),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["scene_elements_reverted"] == 2
    assert report["skipped"] == [
        {"kind": "scene_element", "id": "el_3", "reason": "edited_after_run"}
    ]
    repo.apply_element_ops.assert_awaited_once()
    _, kwargs = repo.apply_element_ops.call_args
    assert kwargs["expected_version"] == 8
    assert kwargs["actor"] == f"undo:{_RUN_ID}"


@pytest.mark.asyncio
async def test_report_shape_all_ids_are_str():
    shots_session = _CaptureSession([_FakeResult(rowcount=0)])
    read_session = _CaptureSession(
        [
            _FakeResult(
                all_rows=[
                    _shot_ledger_row(
                        1, 901, "update", {"shot_type": "MS"}, {"shot_type": "CU"}
                    )
                ]
            ),
            _FakeResult(all_rows=[(_SCENE_ID,)]),
        ]
    )
    write_calls = [shots_session]
    ops_rows = [_op_row(5, _AGENT, [_upd("el_1", "n1")], [_upd("el_1", "o1")])]
    repo = MagicMock()
    repo.list_ops_by_scene = AsyncMock(return_value=ops_rows)
    repo.apply_element_ops = AsyncMock(
        side_effect=VersionConflict(current_version=1, elements=[])
    )
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(service_mod, "write_scope", lambda: _ScopeCtx(write_calls.pop(0))),
        patch.object(
            service_mod, "get_script_scene_repository", MagicMock(return_value=repo)
        ),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["skipped"] == [
        {"kind": "shot", "id": "901", "reason": "edited_after_run"},
        {"kind": "scene", "id": str(_SCENE_ID), "reason": "version_conflict"},
    ]
    for item in report["skipped"]:
        assert isinstance(item["id"], str)
