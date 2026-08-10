"""scoped_script_gateway 记账（mig 415）：create/update 同事务写 script_shot_ops。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.ai.scope.scoped_script_gateway as gateway_mod
from app.services.ai.scope.agent_run_scope import AgentRunScope
from app.services.ai.scope.scope_resolver import ResolvedScene, ResolvedShot

_RUN_ID = "800100000000000009"
_PROJECT_A = 900100000000000001
_TEAM_A = 900100000000000003
_SCRIPT_ID = 700100000000000004
_SCENE_ID = 700100000000000001
_SHOT_ID = 700100000000000002


# ↓ copied verbatim from test_screenwriting_tools.py — private test helpers,
# not imported across test files (see that module's docstring).
class _FakeResult:
    def __init__(self, *, first_row=None, scalar=None, all_rows=None):
        self._first_row = first_row
        self._scalar = scalar
        self._all = all_rows or []

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

    async def scalar(self, stmt):
        self.statements.append(stmt)
        result = self._results.pop(0) if self._results else _FakeResult()
        return result._scalar if isinstance(result, _FakeResult) else result


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _resolved_scene(content=None, version=3) -> ResolvedScene:
    return ResolvedScene(
        id=_SCENE_ID,
        script_id=_SCRIPT_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
        heading_int_ext="INT",
        location_text="Kitchen",
        time_of_day="DAY",
        content_json=(
            content
            if content is not None
            else [{"id": "el_1", "type": "action", "text": "She waits."}]
        ),
        content_version=version,
    )


def _resolved_shot() -> ResolvedShot:
    return ResolvedShot(
        id=_SHOT_ID,
        scene_id=_SCENE_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
        shot_number=4,
        shot_type="MS",
        status="empty",
    )


def _scope(run_id=_RUN_ID) -> AgentRunScope:
    return AgentRunScope(run_id=run_id, user_id="u1", project_id=1, team_id=None)


def _returning_row(**over):
    base = dict(
        id=900,
        shot_number=1,
        shot_type="CU",
        camera_angle=None,
        camera_movement=None,
        focal_length="85mm",
        lighting=None,
        description="Her hands.",
        status="empty",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_create_shot_writes_attribution_and_full_snapshot_ledger_row():
    session = _CaptureSession(
        [
            _FakeResult(scalar=0),  # MAX(shot_number)
            _FakeResult(scalar=0),  # MAX(sort_order)
            _FakeResult(first_row=_returning_row()),  # INSERT..RETURNING
            _FakeResult(),  # ledger INSERT
        ]
    )
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="1")),
    ):
        await gateway_mod.create_shot(
            _scope(),
            _resolved_scene(),
            {"shot_type": "CU", "focal_length": "85mm", "description": "Her hands."},
        )
    inserts = [s for s in session.statements if s.__class__.__name__ == "Insert"]
    assert len(inserts) == 2
    shot_values = inserts[0].compile().params
    assert shot_values["created_by_agent_run_id"] == int(_RUN_ID)
    ledger = inserts[1].compile().params
    assert ledger["run_id"] == int(_RUN_ID)
    assert ledger["shot_id"] == 900
    assert ledger["action"] == "create"
    assert ledger["before_json"] is None
    # create 快照必须覆盖全部 6 个 writable 字段（未写的显式 None）
    assert ledger["after_json"] == {
        "shot_type": "CU",
        "camera_angle": None,
        "camera_movement": None,
        "focal_length": "85mm",
        "lighting": None,
        "description": "Her hands.",
    }


@pytest.mark.asyncio
async def test_create_shot_sentinel_run_skips_ledger_and_attribution():
    session = _CaptureSession(
        [
            _FakeResult(scalar=0),
            _FakeResult(scalar=0),
            _FakeResult(first_row=_returning_row()),
        ]
    )
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="1")),
    ):
        await gateway_mod.create_shot(
            _scope(run_id="0"), _resolved_scene(), {"description": "x"}
        )
    inserts = [s for s in session.statements if s.__class__.__name__ == "Insert"]
    assert len(inserts) == 1
    assert "created_by_agent_run_id" not in inserts[0].compile().params


@pytest.mark.asyncio
async def test_update_shot_ledger_carries_only_touched_fields_before_and_after():
    session = _CaptureSession(
        [
            # 事务内先 SELECT ... FOR UPDATE 旧值
            _FakeResult(first_row=_returning_row(shot_type="MS", focal_length="35mm")),
            _FakeResult(
                first_row=_returning_row(shot_type="CU", focal_length="35mm")
            ),  # UPDATE..RETURNING
            _FakeResult(),  # ledger INSERT
        ]
    )
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for_shot", AsyncMock(return_value="1")),
    ):
        await gateway_mod.update_shot(_scope(), _resolved_shot(), {"shot_type": "CU"})
    ledger = (
        [s for s in session.statements if s.__class__.__name__ == "Insert"][0]
        .compile()
        .params
    )
    assert ledger["action"] == "update"
    assert ledger["before_json"] == {"shot_type": "MS"}
    assert ledger["after_json"] == {"shot_type": "CU"}
    assert ledger["scene_id"] == _resolved_shot().scene_id
