"""Script version routes: wire parity after they gained response models (P6).

Every route runs over real HTTP through the real router and the real
``VersionService``. Only the repositories are fakes, and they hand back rows
built from the ORM mapper and passed through the repositories' own ``_row``
(``sample_orm``), so every column arrives in its real native type. The body
must equal what FastAPI sent for the dict the handler builds with no model
(``tests/api/wire_parity.py``); the "raw" side is computed by calling the same
service on the same fakes.

Also pinned here: a rollback whose commit vanished after the ownership check
is a typed 404, and the optional ``error_code`` / ``error`` keys of a rollback
result stay absent on scenes that did not fail.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_commit_access,
    verify_script_access,
    verify_script_read_access,
)
from app.main import app
from app.models import ScriptCommits, ScriptScenes
from app.repositories.script_commit_repository import _row as commit_row_of
from app.repositories.script_scene_repository import _row as scene_row_of
from app.schemas import script_version_responses as vr
from app.services.script.version_service import VersionService
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    assert_wire_unchanged,
    column_names,
    sample_orm,
)
from tests.test_version_service import _FakeSceneRepo, _ins, _ledger, _upd

pytestmark = pytest.mark.unit

versions_mod = importlib.import_module("app.api.script_versions_router")

USER = "00000000-0000-0000-0000-000000000042"
OTHER = "00000000-0000-0000-0000-000000000077"
SID = SAMPLE_BIGINT + 11
CID = SAMPLE_BIGINT + 22


def scene_row(scene_id: int, version: int, sort_order: int) -> dict:
    return scene_row_of(
        sample_orm(
            ScriptScenes, id=scene_id, content_version=version, sort_order=sort_order
        )
    )


def snapshot(scene_id: int, sort_order: int) -> dict:
    return {
        "id": str(scene_id),
        "sort_order": sort_order,
        "heading_int_ext": "INT",
        "location_text": "Kitchen",
    }


def commit_row(**over: Any) -> dict:
    values: Dict[str, Any] = {
        "id": CID,
        "script_id": SID,
        "watermarks": {"111": 1, "333": 1},
        "scene_ids": [snapshot(111, 1000), snapshot(333, 3000)],
        "created_by": USER,
    }
    values.update(over)
    return commit_row_of(sample_orm(ScriptCommits, **values))


class FakeCommitRepo:
    def __init__(self, commit: dict | None, usernames: dict | None = None) -> None:
        self.commit = commit
        self.usernames = usernames or {}
        self.deleted: List[str] = []

    async def create(self, data: dict) -> dict:
        return commit_row(
            script_id=int(data["script_id"]),
            message=data["message"],
            watermarks=data["watermarks"],
            scene_ids=data["scene_ids"],
            created_by=data["created_by"],
        )

    async def get(self, commit_id: str) -> dict | None:
        return self.commit

    async def list_by_script(self, script_id: str) -> List[dict]:
        return [
            {**commit_row(), "author_name": "alice"},
            {**commit_row(id=CID + 1, created_by=OTHER), "author_name": None},
        ]

    async def resolve_usernames(self, user_ids: List[str]) -> dict:
        return {u: self.usernames[u] for u in user_ids if u in self.usernames}

    async def delete(self, commit_id: str) -> bool:
        self.deleted.append(commit_id)
        return True


def _ledgers() -> Dict[str, list]:
    rows_111, _ = _ledger(
        [[_ins("el_1", "one")], [_upd("el_1", "one-edited")]],
        actors=[USER, OTHER],
    )
    rows_333, _ = _ledger([[_ins("el_3", "three")]], actors=[USER])
    rows_444, _ = _ledger([[_ins("el_4", "four")]], actors=["copilot"])
    return {"111": rows_111, "333": rows_333, "444": rows_444}


class Wiring:
    scene_repo: _FakeSceneRepo
    commit_repo: FakeCommitRepo


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    for guard in (
        verify_script_access,
        verify_script_read_access,
        verify_commit_access,
    ):
        app.dependency_overrides[guard] = lambda: None
    Wiring.scene_repo = _FakeSceneRepo(
        [scene_row(111, 2, 1000), scene_row(444, 1, 4000)], _ledgers()
    )
    Wiring.commit_repo = FakeCommitRepo(commit_row(), {OTHER: "bob"})
    monkeypatch.setattr(
        versions_mod,
        "get_version_service",
        lambda: VersionService(
            scene_repo=Wiring.scene_repo, commit_repo=Wiring.commit_repo
        ),
    )
    monkeypatch.setattr(
        versions_mod, "get_script_commit_repository", lambda: Wiring.commit_repo
    )
    yield
    for dep in (
        get_auth,
        verify_script_access,
        verify_script_read_access,
        verify_commit_access,
    ):
        app.dependency_overrides.pop(dep, None)


def _svc() -> VersionService:
    return VersionService(scene_repo=Wiring.scene_repo, commit_repo=Wiring.commit_repo)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------- #
# Model pins
# --------------------------------------------------------------------------- #


def test_commit_row_declares_every_column() -> None:
    assert set(vr.ScriptCommitRow.model_fields) == column_names(ScriptCommits)
    assert set(vr.ScriptCommitListItem.model_fields) == column_names(ScriptCommits) | {
        "author_name"
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_commit_wire_unchanged(client) -> None:
    raw_commit = await _svc().create_commit(str(SID), "v1", USER)
    resp = await client.post(f"/api/v1/scripts/{SID}/commits", json={"message": "v1"})
    assert_wire_unchanged(resp, {"success": True, "data": raw_commit})
    # Snowflake ids stay JSON numbers on this surface.
    assert resp.json()["data"]["id"] == CID
    assert resp.json()["data"]["watermarks"] == {"111": 2, "444": 1}


@pytest.mark.asyncio
async def test_list_commits_wire_unchanged(client) -> None:
    raw = await Wiring.commit_repo.list_by_script(str(SID))
    resp = await client.get(f"/api/v1/scripts/{SID}/commits")
    assert_wire_unchanged(resp, {"success": True, "data": raw})
    assert [c["author_name"] for c in resp.json()["data"]] == ["alice", None]


@pytest.mark.asyncio
async def test_diff_against_current_wire_unchanged(client) -> None:
    raw = await _svc().compute_diff(str(SID), commit_row(), None)
    # Non-trivial on every axis: one scene changed, one added, one removed.
    assert raw["scenes"] and raw["scenes_added"] and raw["scenes_removed"]
    assert raw["authors"] == {OTHER: "bob"}
    resp = await client.get(f"/api/v1/scripts/{SID}/commits/{CID}/diff")
    assert_wire_unchanged(resp, {"success": True, "data": raw})


@pytest.mark.asyncio
async def test_diff_against_commit_wire_unchanged(client) -> None:
    raw = await _svc().compute_diff(str(SID), commit_row(), commit_row())
    resp = await client.get(
        f"/api/v1/scripts/{SID}/commits/{CID}/diff", params={"against": str(CID)}
    )
    assert_wire_unchanged(resp, {"success": True, "data": raw})


@pytest.mark.asyncio
async def test_diff_commit_of_another_script_is_404(client) -> None:
    Wiring.commit_repo.commit = commit_row(script_id=SID + 1)
    resp = await client.get(f"/api/v1/scripts/{SID}/commits/{CID}/diff")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rollback_partial_failure_wire_unchanged(client) -> None:
    # 111 rolls back 2 → 1; a second scene at a newer version fails.
    rows_222, _ = _ledger([[_ins("el_2", "two")], [_upd("el_2", "x")]])
    Wiring.scene_repo._scenes.append(scene_row(222, 2, 2000))
    Wiring.scene_repo._ops["222"] = rows_222
    Wiring.scene_repo.fail_scene_ids = {"222"}
    Wiring.commit_repo.commit = commit_row(watermarks={"111": 1, "222": 1, "333": 1})

    raw = await _svc().rollback_to(str(SID), str(CID), USER)
    # rollback_to replays for real: reset the fake's applied log before HTTP.
    Wiring.scene_repo.applied.clear()
    statuses = {r["scene_id"]: r for r in raw["results"]}
    assert statuses["111"] == {"scene_id": "111", "status": "rolled_back"}
    assert statuses["222"]["error_code"] == "error"

    resp = await client.post(f"/api/v1/scripts/{SID}/commits/{CID}/rollback")
    assert_wire_unchanged(resp, {"success": False, "data": raw})
    # Keys the service did not set stay absent.
    body = {r["scene_id"]: r for r in resp.json()["data"]["results"]}
    assert "error_code" not in body["111"] and "error" not in body["111"]


@pytest.mark.asyncio
async def test_rollback_clean_wire_unchanged(client) -> None:
    raw = await _svc().rollback_to(str(SID), str(CID), USER)
    Wiring.scene_repo.applied.clear()
    resp = await client.post(f"/api/v1/scripts/{SID}/commits/{CID}/rollback")
    assert_wire_unchanged(resp, {"success": True, "data": raw})


@pytest.mark.asyncio
async def test_rollback_commit_vanished_is_typed_404(client, monkeypatch) -> None:
    async def _gone(self, script_id, commit_id, actor):
        return None

    monkeypatch.setattr(VersionService, "rollback_to", _gone)
    resp = await client.post(f"/api/v1/scripts/{SID}/commits/{CID}/rollback")
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_delete_commit_wire_unchanged(client) -> None:
    resp = await client.delete(f"/api/v1/commits/{CID}")
    assert_wire_unchanged(resp, {"success": True})
    assert Wiring.commit_repo.deleted == [str(CID)]
