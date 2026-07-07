"""Authz wiring + repo behavior tests for the beats router / repository (PR-BT1).

Three halves, matching the plan's Task-1 gate:

1. **Guard matrix** (mirrors test_shots_authz_wiring.py): every beats route MUST
   declare a known access guard as a dependency — a future endpoint added
   without one fails here instead of shipping an IDOR. Plus the
   ``verify_beat_access`` resolution chain: beat → script → team, so a foreign
   caller gets 403 and a missing beat 404 (before the 403).

2. **Reorder** (capture-emitted-SQL, no DSN): ``create`` lands at MAX+STEP;
   ``move`` bisects to the midpoint on a wide gap (single UPDATE) and renumbers
   the whole script group when the neighbour gap is exhausted; ``_resolve_slot``
   places front / after-anchor / unknown-tail correctly.

3. **scene_ids round-trip**: ids are string-coerced on create + update (#1006).
"""

from __future__ import annotations

import datetime as _dt
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

import app.repositories.script_beat_repository as beat_mod
from app.api.script_beats_router import router as beats_router
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_beat_access, verify_script_access
from app.main import app
from app.models.scripts import ScriptBeats
from app.repositories.script_beat_repository import ScriptBeatRepository

pytestmark = pytest.mark.unit

_SCRIPT_ID = 900000000000000001
_BEAT_ID = 900000000000000002


# --------------------------------------------------------------------------- #
# 1a. Structural: every route declares a guard
# --------------------------------------------------------------------------- #

KNOWN_GUARDS = {verify_script_access, verify_beat_access}
FAKE_USER_ID = str(uuid4())


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


_ALL_ROUTES = [r for r in list(beats_router.routes) if hasattr(r, "dependant")]


@pytest.mark.parametrize(
    "route",
    _ALL_ROUTES,
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_beat_route_declares_guard(route):
    calls = set(_flat_dependency_calls(route.dependant))
    assert (
        calls & KNOWN_GUARDS
    ), f"{sorted(route.methods)} {route.path} declares no access guard"


# --------------------------------------------------------------------------- #
# 1b. Behavior: verify_beat_access / verify_script_access resolution chain
# --------------------------------------------------------------------------- #


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def _foreign_team(monkeypatch):
    """Wire the guard chain so the caller's team never matches the beat's:
    beat → script_id → OWNER_TEAM, while the caller is a non-member."""
    import app.core.scope_guards as guards
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_beat_get(self, beat_id):
        return {"id": beat_id, "script_id": "9001"}

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "OWNER_TEAM"}

    async def fake_member(team_id, user_id):
        return False

    monkeypatch.setattr(ScriptBeatRepository, "get_by_id", fake_beat_get)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)
    monkeypatch.setattr(guards, "_is_team_member", fake_member)


@pytest.mark.asyncio
async def test_beat_patch_403_foreign_team(client, _foreign_team):
    resp = await client.patch("/api/v1/beats/123", json={"title": "X"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_beat_move_403_foreign_team(client, _foreign_team):
    resp = await client.post("/api/v1/beats/123/move", json={})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_script_beats_list_403_foreign_team(client, _foreign_team):
    # The script-scoped route resolves via verify_script_access → same team gate.
    resp = await client.get("/api/v1/scripts/9001/beats")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_beat_missing_returns_404(client, monkeypatch):
    """A missing beat 404s before any team check (guard row-missing ordering)."""

    async def fake_beat_get(self, beat_id):
        return None

    monkeypatch.setattr(ScriptBeatRepository, "get_by_id", fake_beat_get)
    resp = await client.delete("/api/v1/beats/123")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 2 + 3. Repository behavior (capture-emitted-SQL, no DSN)
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, *, scalar_first=None, all_rows=None):
        self._scalar_first = scalar_first
        self._all_rows = all_rows or []

    def scalars(self):
        return self

    def first(self):
        return self._scalar_first

    def all(self):
        return self._all_rows


class _CaptureSession:
    def __init__(self, results):
        self.statements: list = []
        self._results = list(results)

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0)

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0)._scalar_first


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _beat_obj(sort_order=1000, scene_ids=None):
    return ScriptBeats(
        id=_BEAT_ID,
        script_id=_SCRIPT_ID,
        title="Inciting incident",
        summary="The hero gets the call.",
        scene_ids=scene_ids if scene_ids is not None else [],
        sort_order=sort_order,
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


@pytest.mark.asyncio
async def test_create_auto_sort_order_max_plus_1000():
    """create() sets sort_order = script MAX + 1000 and bigint-coerces script_id."""
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=3000),  # func.max(sort_order) → 3000
            _FakeResult(scalar_first=_beat_obj(sort_order=4000)),  # INSERT RETURNING
        ]
    )
    with patch.object(beat_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptBeatRepository().create(
            {"script_id": str(_SCRIPT_ID), "title": "Inciting incident"}
        )
    ins_sql, ins_params = _rendered(session.statements[1])
    assert ins_sql.strip().upper().startswith("INSERT INTO PUBLIC.SCRIPT_BEATS")
    assert 4000 in ins_params.values()  # 3000 + 1000
    assert _SCRIPT_ID in ins_params.values()  # bigint-coerced script_id
    assert all(
        not isinstance(v, str) or v != str(_SCRIPT_ID) for v in ins_params.values()
    )
    assert out["sort_order"] == 4000


@pytest.mark.asyncio
async def test_create_empty_script_starts_at_1000():
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=None),  # no beats yet
            _FakeResult(scalar_first=_beat_obj(sort_order=1000)),
        ]
    )
    with patch.object(beat_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptBeatRepository().create(
            {"script_id": str(_SCRIPT_ID), "title": "A"}
        )
    _, ins_params = _rendered(session.statements[1])
    assert 1000 in ins_params.values()


@pytest.mark.asyncio
async def test_create_coerces_scene_ids_to_strings():
    """Mixed int/str scene ids land as an ordered list of strings (#1006)."""
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=0),
            _FakeResult(scalar_first=_beat_obj()),
        ]
    )
    with patch.object(beat_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptBeatRepository().create(
            {
                "script_id": str(_SCRIPT_ID),
                "title": "Links",
                "scene_ids": [123, "456", 789],
            }
        )
    _, ins_params = _rendered(session.statements[1])
    assert ["123", "456", "789"] in ins_params.values()


@pytest.mark.asyncio
async def test_update_coerces_scene_ids_and_ignores_non_whitelist():
    session = _CaptureSession([_FakeResult(scalar_first=_beat_obj())])
    with patch.object(beat_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptBeatRepository().update(
            str(_BEAT_ID),
            {"title": "New", "scene_ids": [1, 2], "sort_order": 999, "id": 5},
        )
    upd_sql, upd_params = _rendered(session.statements[0])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_BEATS")
    assert ["1", "2"] in upd_params.values()
    # sort_order / id are NOT in the update whitelist → never bound.
    assert 999 not in upd_params.values()


@pytest.mark.asyncio
async def test_move_midpoint_single_update():
    """A wide neighbour gap → one UPDATE placing the beat at the midpoint."""
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=_beat_obj()),  # SELECT moved beat
            _FakeResult(all_rows=[(111, 1000), (222, 2000)]),  # siblings
            _FakeResult(scalar_first=None),  # UPDATE
            _FakeResult(scalar_first=_beat_obj(sort_order=1500)),  # SELECT re-read
        ]
    )
    with patch.object(beat_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptBeatRepository().move(str(_BEAT_ID), after_beat_id="111")
    # SELECT, SELECT siblings, UPDATE, SELECT → exactly one UPDATE.
    assert len(session.statements) == 4
    upd_sql, upd_params = _rendered(session.statements[2])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_BEATS")
    assert 1500 in upd_params.values()  # midpoint of (1000, 2000)


@pytest.mark.asyncio
async def test_move_renumbers_on_exhausted_gap():
    """Adjacent neighbours (gap < 2) → renumber the whole group to a fresh ladder."""
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=_beat_obj()),  # SELECT moved beat
            _FakeResult(all_rows=[(111, 1000), (222, 1001)]),  # siblings, no gap
            _FakeResult(scalar_first=None),  # UPDATE 1
            _FakeResult(scalar_first=None),  # UPDATE 2
            _FakeResult(scalar_first=None),  # UPDATE 3
            _FakeResult(scalar_first=_beat_obj(sort_order=2000)),  # SELECT re-read
        ]
    )
    with patch.object(beat_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptBeatRepository().move(str(_BEAT_ID), after_beat_id="111")
    # SELECT + SELECT siblings + 3 renumber UPDATEs + SELECT re-read.
    assert len(session.statements) == 6
    # The spliced beat (after 111) lands at the 2nd ladder rung → 2000.
    _, mid_params = _rendered(session.statements[3])
    assert 2000 in mid_params.values()


def test_resolve_slot_front_after_and_unknown():
    siblings = [(111, 1000), (222, 2000), (333, 3000)]
    # after=None → front, before the first sibling.
    assert ScriptBeatRepository._resolve_slot(siblings, None) == (None, 1000, 0)
    # after a middle anchor → between it and the next.
    assert ScriptBeatRepository._resolve_slot(siblings, "222") == (2000, 3000, 2)
    # after the last anchor → tail (upper None).
    assert ScriptBeatRepository._resolve_slot(siblings, "333") == (3000, None, 3)
    # unknown anchor → append at tail.
    assert ScriptBeatRepository._resolve_slot(siblings, "999") == (3000, None, 3)
    # empty script → base slot.
    assert ScriptBeatRepository._resolve_slot([], None) == (None, None, 0)


def test_sparse_between_midpoint_and_exhaustion():
    assert ScriptBeatRepository._sparse_between(None, None) == 1000
    assert ScriptBeatRepository._sparse_between(None, 2000) == 1000
    assert ScriptBeatRepository._sparse_between(1000, None) == 2000
    assert ScriptBeatRepository._sparse_between(1000, 2000) == 1500
    assert ScriptBeatRepository._sparse_between(1000, 1001) is None  # gap < 2
