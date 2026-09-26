"""platform_provider: the platform card computed from catalog + live engine.

Catalog rows are built by the real repository conversion (``_row`` over a
transient ORM object carrying every column), so value types match what
``list_enabled_private`` returns in production. The engine is scripted at the
``engine_catalog`` read seam.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.ai.engine_catalog as ec
import app.services.ai.platform_provider as pp
from app.models import NousModels
from app.repositories.nous_model_repository import _row as repo_row
from tests.api.wire_parity import sample_orm

pytestmark = pytest.mark.unit

ENGINE = "http://engine.test/v1"
USER = "00000000-0000-0000-0000-000000000042"


def catalog_row(name: str, **over) -> dict:
    values = {
        "name": name,
        "display_name": name.title(),
        "type": "llm",
        "actual_provider": "ark",
        "actual_model": f"{name}-upstream",
        "api_key": "sk-platform",
        "base_url": "https://ark.example/api/v3",
        "pricing_type": "per_token",
        "is_enabled": True,
        "owner_user_id": None,
        "last_test_status": "ok",
        "last_test_code": None,
    }
    values.update(over)
    return repo_row(sample_orm(NousModels, **values))


def engine_row(name: str, service: str, **over) -> dict:
    values = {
        "actual_provider": "nous",
        "actual_model": service,
        "base_url": ENGINE,
        "api_key": "sk-engine",
        "last_test_status": "fail",  # stale probe answer: the engine must win
    }
    values.update(over)
    return catalog_row(name, **values)


def listed(*services: tuple[str, bool]) -> ec._Read:
    return ec._Read(
        services={
            sid: ec.EngineService(
                id=sid,
                type="llm",
                ready=ready,
                context_window=None,
                capabilities=None,
            )
            for sid, ready in services
        }
    )


class Env:
    def __init__(self, monkeypatch):
        ec.reset_engine_cache()
        self.rows: list[dict] = []
        self.governance = True
        self.engine_answers: list[ec._Read] = []
        self.engine_calls: list[tuple[str, str]] = []
        self.now = 1000.0
        repo = MagicMock()
        repo.list_enabled_private = AsyncMock(side_effect=lambda _v=None: self.rows)
        self.repo = repo
        monkeypatch.setattr(
            "app.repositories.nous_model_repository.get_nous_model_repository",
            lambda: repo,
        )
        monkeypatch.setattr(
            "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
            AsyncMock(side_effect=lambda: self.governance),
        )
        monkeypatch.setattr(ec, "_fetch", self._fetch)
        monkeypatch.setattr(ec, "_clock", lambda: self.now)

    async def _fetch(self, base_url, api_key):
        self.engine_calls.append((base_url, api_key))
        return self.engine_answers.pop(0)


@pytest.fixture
def env(monkeypatch):
    e = Env(monkeypatch)
    yield e
    ec.reset_engine_cache()


def names(view) -> list[str]:
    return [m.name for m in view.models]


@pytest.mark.asyncio
async def test_governance_off_is_empty_and_keeps_stored_switch(env):
    env.governance = False
    env.rows = [catalog_row("nous-a")]
    view = await pp.platform_provider_view(
        USER, stored_nous={"enabled": True, "disabled_models": ["nous-a"]}
    )
    assert view.models == () and view.enabled_models == ()
    assert view.enabled is True
    assert view.disabled_models == ("nous-a",)
    assert view.engine is None
    env.repo.list_enabled_private.assert_not_awaited()


@pytest.mark.asyncio
async def test_blacklist_removes_only_from_enabled_models(env):
    env.rows = [catalog_row("nous-a"), catalog_row("nous-b"), catalog_row("nous-c")]
    view = await pp.platform_provider_view(
        USER, stored_nous={"disabled_models": ["nous-b"]}
    )
    assert names(view) == ["nous-a", "nous-b", "nous-c"]
    assert view.enabled_models == ("nous-a", "nous-c")
    entry = view.provider_entry()
    assert entry == {
        "enabled": True,
        "managed": True,
        "models": ["nous-a", "nous-b", "nous-c"],
        "enabled_models": ["nous-a", "nous-c"],
        "disabled_models": ["nous-b"],
    }


@pytest.mark.asyncio
async def test_blacklist_written_before_rename_still_hides_renamed_row(env):
    env.rows = [catalog_row("nous-deepseek")]
    view = await pp.platform_provider_view(
        USER, stored_nous={"disabled_models": ["mediahub-deepseek"]}
    )
    assert view.enabled_models == ()


@pytest.mark.asyncio
async def test_fail_row_is_not_listed(env):
    env.rows = [catalog_row("good"), catalog_row("broken", last_test_status="fail")]
    view = await pp.platform_provider_view(USER, stored_nous={})
    assert names(view) == ["good"]


@pytest.mark.asyncio
async def test_non_engine_rows_keep_stored_probe_status(env):
    env.rows = [
        catalog_row("ok-row", last_test_status="ok"),
        catalog_row("idle-row", last_test_status="idle"),
        catalog_row("never", last_test_status=None),
        catalog_row("local", actual_provider="codex-local", last_test_status=None),
    ]
    view = await pp.platform_provider_view(USER, stored_nous={})
    assert [m.status for m in view.models] == ["ok", "idle", "not_probed", "not_probed"]
    assert [m.is_local for m in view.models] == [False, False, False, True]
    assert env.engine_calls == []
    assert view.engine is None


@pytest.mark.asyncio
async def test_engine_rows_follow_ready_and_drop_unlisted(env):
    env.rows = [
        engine_row("nous-qwen", "qwen3-8b"),
        engine_row("nous-wemm", "wemm-2b"),
        engine_row("nous-revoked", "gone"),
    ]
    env.engine_answers = [listed(("qwen3-8b", True), ("wemm-2b", False))]
    view = await pp.platform_provider_view(USER, stored_nous={})
    assert names(view) == ["nous-qwen", "nous-wemm"]
    assert [m.status for m in view.models] == ["ok", "idle"]
    # One engine, one credential → one read for all three rows.
    assert env.engine_calls == [(ENGINE, "sk-engine")]
    assert view.engine.reachable is True and view.engine.stale is False
    assert view.engine.checked_at is not None


@pytest.mark.asyncio
async def test_unreachable_engine_without_snapshot_keeps_rows_not_probed(env):
    env.rows = [engine_row("nous-qwen", "qwen3-8b")]
    env.engine_answers = [ec._Read(error="ConnectError: refused")]
    view = await pp.platform_provider_view(USER, stored_nous={})
    assert names(view) == ["nous-qwen"]
    assert view.models[0].status == "not_probed"
    assert view.enabled_models == ("nous-qwen",)
    assert view.engine.reachable is False
    assert view.engine.checked_at is None


@pytest.mark.asyncio
async def test_stale_snapshot_is_used_and_flagged(env):
    env.rows = [engine_row("nous-qwen", "qwen3-8b"), engine_row("nous-x", "x")]
    env.engine_answers = [listed(("qwen3-8b", True)), ec._Read(error="HTTP 502")]
    await pp.platform_provider_view(USER, stored_nous={})
    ec._cache.clear()
    env.now += 60
    view = await pp.platform_provider_view(USER, stored_nous={})
    assert names(view) == ["nous-qwen"]
    assert view.models[0].status == "ok"
    assert view.engine.stale is True and view.engine.reachable is True


@pytest.mark.asyncio
async def test_rows_with_different_keys_are_read_with_their_own_key(env):
    env.rows = [
        engine_row("nous-a", "a"),
        engine_row("nous-b", "b", api_key="sk-other"),
    ]
    env.engine_answers = [
        listed(("a", True)),
        ec._Read(error=ec.UNAUTHORIZED_ERROR, unauthorized=True),
    ]
    view = await pp.platform_provider_view(USER, stored_nous={})
    assert env.engine_calls == [(ENGINE, "sk-engine"), (ENGINE, "sk-other")]
    # The refused key is not "revoked": its row stays, unknown.
    assert [(m.name, m.status) for m in view.models] == [
        ("nous-a", "ok"),
        ("nous-b", "not_probed"),
    ]
    assert view.engine.reachable is False


@pytest.mark.asyncio
async def test_mapping_entry_carries_no_credentials(env):
    env.rows = [engine_row("nous-qwen", "qwen3-8b", context_window_tokens=32768)]
    env.engine_answers = [listed(("qwen3-8b", True))]
    view = await pp.platform_provider_view(USER, stored_nous={})
    entry = view.platform_models()["nous-qwen"]
    assert entry == {
        "actual_model": "qwen3-8b",
        "type": "llm",
        "status": "ok",
        "is_local": False,
        "pricing_type": "per_token",
        "pricing_value": entry["pricing_value"],
        "context_window_tokens": 32768,
    }
    assert isinstance(entry["pricing_value"], float)
    public = view.models[0].public_row()
    assert not {"api_key", "base_url", "actual_provider", "app_id"} & set(public)


@pytest.mark.asyncio
async def test_rows_with_status_is_platform_only_and_filters_type(env):
    env.rows = [catalog_row("chat"), catalog_row("pic", type="image")]
    rows = await pp.platform_rows_with_status("llm")
    assert [(r["name"], r["status"]) for r in rows] == [("chat", "ok")]
    env.repo.list_enabled_private.assert_awaited_with(None)


@pytest.mark.asyncio
async def test_rows_with_status_degrades_to_empty_on_read_failure(env):
    env.repo.list_enabled_private = AsyncMock(side_effect=RuntimeError("db down"))
    assert await pp.platform_rows_with_status("llm") == []


@pytest.mark.asyncio
async def test_platform_status_reports_local_readiness(env, monkeypatch):
    from app.services.generation import local_readiness as lr

    env.rows = [
        catalog_row("codex", actual_provider="codex-local", last_test_status=None),
        catalog_row("dreamina-local", actual_provider="jimeng-local"),
        catalog_row("dreamina-server", actual_provider="jimeng-cli"),
        catalog_row("chat"),
    ]
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        lr,
        "local_engine_readiness",
        AsyncMock(return_value=lr.LocalReadiness(codex=False, dreamina=True)),
    )
    out = await pp.platform_status(USER)
    assert out["models"] == {
        "codex": {"status": "not_probed", "local_ready": False, "superseded": False},
        "dreamina-local": {"status": "ok", "local_ready": True, "superseded": False},
        "dreamina-server": {"status": "ok", "local_ready": None, "superseded": True},
        "chat": {"status": "ok", "local_ready": None, "superseded": False},
    }
    assert out["engine"] is None
