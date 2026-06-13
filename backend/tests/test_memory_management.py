"""User-facing memory management (Honcho 开闸包 product surface).

Claude-style memory controls: per-user learn/inject toggles, view the
observation list, edit the user-curated "About me" card, delete single
observations, forget everything. Raw chat messages are retained (same
as Claude — deleting memory doesn't delete chat history); only derived
observations + card are user-managed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.ai.memory.honcho_memory import (
    HonchoMemoryConfig,
    HonchoMemoryService,
)

_USER = "11111111-1111-1111-1111-111111111111"
_PEER = f"user-{_USER}"


def _service(handler, *, enabled: bool = True) -> HonchoMemoryService:
    config = HonchoMemoryConfig(
        enabled=enabled, base_url="http://honcho.test", workspace_id="mediahub"
    )
    client = httpx.AsyncClient(
        base_url="http://honcho.test", transport=httpx.MockTransport(handler)
    )
    return HonchoMemoryService(config=config, client=client)


def _conclusion(cid: str, content: str, observed: str = _PEER) -> dict:
    return {
        "id": cid,
        "content": content,
        "observer_id": observed,
        "observed_id": observed,
        "session_id": "s1",
        "created_at": "2026-06-12T15:00:22Z",
    }


# ============================================================
# Service: conclusions CRUD + card
# ============================================================


@pytest.mark.asyncio
async def test_list_conclusions_scoped_to_observed_peer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/workspaces/mediahub/conclusions/list"
        return httpx.Response(
            200,
            json={
                "items": [
                    _conclusion("c1", "likes fast cuts"),
                    _conclusion("c2", "about someone else", observed="user-other"),
                ]
            },
        )

    items = await _service(handler).list_conclusions(user_id=_USER)
    # Client-side re-filter: only facts ABOUT this user survive even if
    # the server-side filter semantics drift.
    assert [i["id"] for i in items] == ["c1"]
    assert items[0]["content"] == "likes fast cuts"


@pytest.mark.asyncio
async def test_delete_conclusion_true_on_200() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    ok = await _service(handler).delete_conclusion(
        conclusion_id="c1", workspace_id=None
    )
    assert ok is True
    assert paths == ["DELETE /v3/workspaces/mediahub/conclusions/c1"]


@pytest.mark.asyncio
async def test_delete_conclusion_false_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    assert await _service(handler).delete_conclusion(conclusion_id="c1") is False


@pytest.mark.asyncio
async def test_get_peer_card_lines() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v3/workspaces/mediahub/peers/{_PEER}/card"
        return httpx.Response(200, json={"peer_card": ["likes EN+zh output"]})

    assert await _service(handler).get_peer_card(user_id=_USER) == [
        "likes EN+zh output"
    ]


@pytest.mark.asyncio
async def test_get_peer_card_none_when_unset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"peer_card": None})

    assert await _service(handler).get_peer_card(user_id=_USER) is None


@pytest.mark.asyncio
async def test_set_peer_card_puts_lines() -> None:
    bodies: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.method, json.loads(request.content)))
        return httpx.Response(200, json={})

    ok = await _service(handler).set_peer_card(user_id=_USER, lines=["a", "b"])
    assert ok is True
    assert bodies == [("PUT", {"peer_card": ["a", "b"]})]


@pytest.mark.asyncio
async def test_forget_user_deletes_conclusions_and_clears_card() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/conclusions/list"):
            return httpx.Response(
                200, json={"items": [_conclusion("c1", "x"), _conclusion("c2", "y")]}
            )
        return httpx.Response(200, json={})

    deleted = await _service(handler).forget_user(user_id=_USER)
    assert deleted == 2
    assert "DELETE /v3/workspaces/mediahub/conclusions/c1" in calls
    assert "DELETE /v3/workspaces/mediahub/conclusions/c2" in calls
    assert f"PUT /v3/workspaces/mediahub/peers/{_PEER}/card" in calls


# ============================================================
# Per-user prefs
# ============================================================


class TestMemoryPrefs:
    @pytest.mark.asyncio
    async def test_defaults_to_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.ai.memory import memory_prefs

        class _Repo:
            async def get_by_user_id(self, uid):
                return {"settings_json": {"ai_settings": {}}}

        monkeypatch.setattr(memory_prefs, "UserSettingsRepository", lambda: _Repo())
        prefs = await memory_prefs.get_memory_prefs(_USER)
        assert prefs.learn is True and prefs.inject is True

    @pytest.mark.asyncio
    async def test_explicit_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.ai.memory import memory_prefs

        class _Repo:
            async def get_by_user_id(self, uid):
                return {
                    "settings_json": {
                        "ai_settings": {
                            "memory_learn_enabled": False,
                            "memory_inject_enabled": False,
                        }
                    }
                }

        monkeypatch.setattr(memory_prefs, "UserSettingsRepository", lambda: _Repo())
        prefs = await memory_prefs.get_memory_prefs(_USER)
        assert prefs.learn is False and prefs.inject is False

    @pytest.mark.asyncio
    async def test_fail_open_on_db_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.ai.memory import memory_prefs

        class _Repo:
            async def get_by_user_id(self, uid):
                raise RuntimeError("db down")

        monkeypatch.setattr(memory_prefs, "UserSettingsRepository", lambda: _Repo())
        prefs = await memory_prefs.get_memory_prefs(_USER)
        assert prefs.learn is True and prefs.inject is True


# ============================================================
# Toggle enforcement: inject (wiring) + learn (write path)
# ============================================================


@pytest.mark.asyncio
async def test_inject_disabled_skips_recall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.ai.memory.honcho_memory as hm
    from app.services.ai.chat import ai_library_chat_wiring as wiring
    from app.services.ai.memory.memory_prefs import MemoryPrefs

    class _Svc:
        class config:
            @staticmethod
            def operative() -> bool:
                return True

        async def get_user_representation(self, **kwargs):  # pragma: no cover
            raise AssertionError("must not fetch when inject disabled")

    monkeypatch.setattr(hm, "get_honcho_memory_service", lambda: _Svc())
    from app.services.ai.memory import memory_prefs as prefs_mod

    async def _prefs(uid):
        return MemoryPrefs(learn=True, inject=False)

    monkeypatch.setattr(prefs_mod, "get_memory_prefs", _prefs)
    result = await wiring._safe_recall_honcho_context(user_id=_USER, session_id=None)
    assert result is None


@pytest.mark.asyncio
async def test_inject_prepends_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.ai.memory.honcho_memory as hm
    from app.services.ai.chat import ai_library_chat_wiring as wiring
    from app.services.ai.memory.memory_prefs import MemoryPrefs

    class _Svc:
        class config:
            @staticmethod
            def operative() -> bool:
                return True

        async def get_user_representation(self, **kwargs):
            return "## Explicit Observations\nlikes fast cuts"

        async def get_peer_card(self, **kwargs):
            return ["Prefers bilingual output"]

    monkeypatch.setattr(hm, "get_honcho_memory_service", lambda: _Svc())
    from app.services.ai.memory import memory_prefs as prefs_mod

    async def _prefs(uid):
        return MemoryPrefs(learn=True, inject=True)

    monkeypatch.setattr(prefs_mod, "get_memory_prefs", _prefs)
    result = await wiring._safe_recall_honcho_context(user_id=_USER, session_id=None)
    assert result is not None
    assert "Prefers bilingual output" in result
    assert "likes fast cuts" in result
    assert result.index("Prefers bilingual") < result.index("likes fast cuts")


@pytest.mark.asyncio
async def test_learn_disabled_skips_honcho_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.ai.memory.honcho_memory as hm
    from app.services.ai.memory.memory_prefs import MemoryPrefs
    from app.workflows import write_memory as wm

    class _Svc:
        class config:
            enabled = True

        async def add_chat_turn(self, **kwargs):  # pragma: no cover
            raise AssertionError("must not write when learn disabled")

    monkeypatch.setattr(hm, "get_honcho_memory_service", lambda: _Svc())
    from app.services.ai.memory import memory_prefs as prefs_mod

    async def _prefs(uid):
        return MemoryPrefs(learn=False, inject=True)

    monkeypatch.setattr(prefs_mod, "get_memory_prefs", _prefs)
    written = await wm._write_honcho_turn(
        user_id=_USER,
        agent_id="a1",
        session_id="123",
        user_msgs=["hello"],
        asst_msgs=["hi"],
    )
    assert written is False


# ============================================================
# Router
# ============================================================


def _app() -> FastAPI:
    from app.api.ai_memory_router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _Auth:
        user_id = _USER
        email = "u@example.com"

    async def _fake_auth():
        return _Auth()

    app.dependency_overrides[get_auth] = _fake_auth
    return app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return TestClient(_app())


def _patch_service(monkeypatch: pytest.MonkeyPatch, svc: Any) -> None:
    import importlib

    router_mod = importlib.import_module("app.api.ai_memory_router")

    monkeypatch.setattr(router_mod, "get_honcho_memory_service", lambda: svc)


class _HappyService:
    class config:
        workspace_id = "mediahub"

        @staticmethod
        def operative() -> bool:
            return True

    async def list_conclusions(self, *, user_id, workspace_id=None, limit=200):
        return [
            {
                "id": "c1",
                "content": "likes fast cuts",
                "created_at": "2026-06-12T15:00:22Z",
                "session_id": "s1",
            }
        ]

    async def get_peer_card(self, *, user_id, workspace_id=None):
        return ["Prefers bilingual output"]

    async def set_peer_card(self, *, user_id, lines, workspace_id=None):
        self.last_card = lines
        return True

    async def get_conclusion(self, *, conclusion_id, workspace_id=None):
        if conclusion_id == "c1":
            return {"id": "c1", "observed_id": _PEER}
        if conclusion_id == "other":
            return {"id": "other", "observed_id": "user-someone-else"}
        return None

    async def delete_conclusion(self, *, conclusion_id, workspace_id=None):
        self.deleted = conclusion_id
        return True

    async def forget_user(self, *, user_id, workspace_id=None):
        self.forgot = user_id
        return 3


def test_profile_returns_observations_card_and_flags(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_service(monkeypatch, _HappyService())
    import importlib

    router_mod = importlib.import_module("app.api.ai_memory_router")
    from app.services.ai.memory.memory_prefs import MemoryPrefs

    async def _prefs(uid):
        return MemoryPrefs(learn=True, inject=False)

    monkeypatch.setattr(router_mod, "get_memory_prefs", _prefs)

    resp = client.get("/api/v1/ai/memory/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["observations"][0]["content"] == "likes fast cuts"
    assert body["card"] == ["Prefers bilingual output"]
    assert body["learn_enabled"] is True
    assert body["inject_enabled"] is False
    assert body["service_available"] is True


def test_put_card_validates_and_saves(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _HappyService()
    _patch_service(monkeypatch, svc)
    resp = client.put("/api/v1/ai/memory/card", json={"lines": ["  a  ", "", "b"]})
    assert resp.status_code == 200
    assert svc.last_card == ["a", "b"]  # trimmed, empties dropped


def test_put_card_rejects_oversized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_service(monkeypatch, _HappyService())
    resp = client.put("/api/v1/ai/memory/card", json={"lines": ["x" * 1000]})
    assert resp.status_code == 422


def test_delete_observation_checks_ownership(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _HappyService()
    _patch_service(monkeypatch, svc)

    assert client.delete("/api/v1/ai/memory/observations/c1").status_code == 200
    assert svc.deleted == "c1"
    # Someone else's observation → 404, never deleted
    assert client.delete("/api/v1/ai/memory/observations/other").status_code == 404
    assert svc.deleted == "c1"


def test_put_prefs_patches_ai_settings(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_service(monkeypatch, _HappyService())
    import importlib

    router_mod = importlib.import_module("app.api.ai_memory_router")
    saved: dict = {}

    class _Repo:
        async def get_by_user_id(self, uid):
            return {"settings_json": {"ai_settings": {"ai_providers": {"x": 1}}}}

        async def patch_settings_json(self, uid, patch):
            saved.update(patch)

    monkeypatch.setattr(router_mod, "UserSettingsRepository", lambda: _Repo())
    from app.services.ai.memory.memory_prefs import MemoryPrefs

    async def _prefs(uid):
        return MemoryPrefs(learn=False, inject=True)

    monkeypatch.setattr(router_mod, "get_memory_prefs", _prefs)

    resp = client.put("/api/v1/ai/memory/prefs", json={"learn_enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"learn_enabled": False, "inject_enabled": True}
    # Merge preserved the sibling ai_providers key (no clobber).
    assert saved["ai_settings"]["ai_providers"] == {"x": 1}
    assert saved["ai_settings"]["memory_learn_enabled"] is False


def test_forget_everything(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _HappyService()
    _patch_service(monkeypatch, svc)
    resp = client.delete("/api/v1/ai/memory")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3
    assert svc.forgot == _USER


def test_team_workspace_requires_membership(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_service(monkeypatch, _HappyService())
    import importlib

    router_mod = importlib.import_module("app.api.ai_memory_router")

    class _Teams:
        async def get_team_by_id(self, team_id, user_id):
            return None  # not a member

    monkeypatch.setattr(router_mod, "TeamRepository", lambda: _Teams())
    resp = client.get("/api/v1/ai/memory/profile?workspace=team-42")
    assert resp.status_code == 403


def test_team_workspace_allowed_for_member(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_service(monkeypatch, _HappyService())
    import importlib

    router_mod = importlib.import_module("app.api.ai_memory_router")
    from app.services.ai.memory.memory_prefs import MemoryPrefs

    async def _prefs(uid):
        return MemoryPrefs(learn=True, inject=True)

    monkeypatch.setattr(router_mod, "get_memory_prefs", _prefs)

    class _Teams:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id}

    monkeypatch.setattr(router_mod, "TeamRepository", lambda: _Teams())
    resp = client.get("/api/v1/ai/memory/profile?workspace=team-42")
    assert resp.status_code == 200


def test_invalid_workspace_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_service(monkeypatch, _HappyService())
    resp = client.get("/api/v1/ai/memory/profile?workspace=evil-ws")
    assert resp.status_code == 422
