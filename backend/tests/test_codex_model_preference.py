"""Per-user choice of the Codex ORCHESTRATOR model for the local daemon.

IC's CLI settings let the user pick the model; ours did not — the model came
only from the catalog row (`actual_model`), editable by an admin. After
2026-09-05 (OpenAI dropped gpt-5.4 for ChatGPT-account Codex) that is a knob
the user has to be able to turn themselves.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.services.codex import preferences as prefs

USER = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def store(monkeypatch):
    """In-memory user_settings.settings_json."""
    state: dict = {}

    async def get_by_user_id(uid):
        return {"settings_json": dict(state)} if state else None

    async def patch_settings_json(uid, partial):
        state.update(partial)
        return {"settings_json": dict(state)}

    repo = SimpleNamespace(
        get_by_user_id=AsyncMock(side_effect=get_by_user_id),
        patch_settings_json=AsyncMock(side_effect=patch_settings_json),
    )
    monkeypatch.setattr(prefs, "_repo", lambda: repo)
    return state


@pytest.mark.asyncio
async def test_get_returns_null_choice_and_the_known_options(client, store):
    resp = await client.get("/api/v1/codex-daemon/preferences")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["codex_model"] is None
    assert "gpt-6-astra" in body["options"]


@pytest.mark.asyncio
async def test_put_saves_under_its_own_settings_key_only(client, store):
    store["ai_settings"] = {"keep": "me"}
    resp = await client.put(
        "/api/v1/codex-daemon/preferences", json={"codex_model": "gpt-5.6-sol"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["codex_model"] == "gpt-5.6-sol"
    assert store["local_cli"] == {"codex_model": "gpt-5.6-sol"}
    assert store["ai_settings"] == {
        "keep": "me"
    }, "other keys must survive (settings_json clobber lesson)"


@pytest.mark.asyncio
async def test_put_null_clears_the_choice(client, store):
    store["local_cli"] = {"codex_model": "gpt-5.5"}
    resp = await client.put(
        "/api/v1/codex-daemon/preferences", json={"codex_model": None}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["codex_model"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "gpt 5.4", "x" * 65, "../etc", "gpt-5.4; rm -rf"])
async def test_put_rejects_a_model_name_the_cli_could_not_take(client, store, bad):
    """It becomes a `--model` argv token on the user's machine: letters, digits,
    dot, dash, underscore only."""
    resp = await client.put(
        "/api/v1/codex-daemon/preferences", json={"codex_model": bad}
    )
    assert resp.status_code == 422


# ── the choice reaches the daemon payload ──────────────────────────────────


@pytest.mark.asyncio
async def test_local_engine_prefers_the_users_choice_over_the_catalog(
    monkeypatch, store
):
    store["local_cli"] = {"codex_model": "gpt-5.6-sol"}
    from app.services.media.parsers.video_providers import db_registry
    from app.workflows import canvas_generation as cg

    monkeypatch.setattr(
        db_registry,
        "_enabled_rows",
        AsyncMock(
            return_value=[
                {
                    "name": "codex-local-image",
                    "actual_provider": "codex-local",
                    "actual_model": "gpt-6-astra",
                },
            ]
        ),
    )
    monkeypatch.setattr(db_registry, "_visible_to", lambda row, uid: True)
    assert await cg._local_engine("codex-local-image", "image", user_id=USER) == (
        "codex",
        "gpt-5.6-sol",
    )


@pytest.mark.asyncio
async def test_local_engine_falls_back_to_the_catalog_without_a_choice(
    monkeypatch, store
):
    from app.services.media.parsers.video_providers import db_registry
    from app.workflows import canvas_generation as cg

    monkeypatch.setattr(
        db_registry,
        "_enabled_rows",
        AsyncMock(
            return_value=[
                {
                    "name": "codex-local-image",
                    "actual_provider": "codex-local",
                    "actual_model": "gpt-6-astra",
                },
            ]
        ),
    )
    monkeypatch.setattr(db_registry, "_visible_to", lambda row, uid: True)
    assert await cg._local_engine("codex-local-image", "image", user_id=USER) == (
        "codex",
        "gpt-6-astra",
    )


@pytest.mark.asyncio
async def test_the_choice_does_not_leak_into_the_dreamina_engine(monkeypatch, store):
    store["local_cli"] = {"codex_model": "gpt-5.6-sol"}
    from app.services.media.parsers.video_providers import db_registry
    from app.workflows import canvas_generation as cg

    monkeypatch.setattr(
        db_registry,
        "_enabled_rows",
        AsyncMock(
            return_value=[
                {
                    "name": "jimeng-local-image",
                    "actual_provider": "jimeng-local",
                    "actual_model": "",
                },
            ]
        ),
    )
    monkeypatch.setattr(db_registry, "_visible_to", lambda row, uid: True)
    assert await cg._local_engine("jimeng-local-image", "image", user_id=USER) == (
        "dreamina",
        "",
    )
