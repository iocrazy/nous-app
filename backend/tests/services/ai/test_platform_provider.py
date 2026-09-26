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


def _governance_state(value) -> str:
    if isinstance(value, str):
        return value
    return "on" if value else "off"


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
        # ``governance``: True / False as read, or "unknown" (the read failed).
        monkeypatch.setattr(
            "app.services.ai.governance.ai_governance.nous_global_state",
            AsyncMock(side_effect=lambda: _governance_state(self.governance)),
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
        "generatable": False,
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


# ─── platform_rows: the one entry every backend decision reads (P4) ─────────


def _stored(monkeypatch, nous: dict) -> None:
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value=nous),
    )


def row_names(rows) -> list[str]:
    return [r.model.name for r in rows]


@pytest.mark.asyncio
async def test_platform_rows_normal_row_is_selected_with_status(env, monkeypatch):
    _stored(monkeypatch, {})
    env.rows = [catalog_row("pic", type="image", actual_provider="ark")]
    rows = await pp.platform_rows(USER, type="image", purpose="dispatch")
    assert row_names(rows) == ["pic"]
    assert rows[0].dispatch_row()["api_key"] == "sk-platform"
    assert rows[0].dispatch_row()["status"] == "ok"
    env.repo.list_enabled_private.assert_awaited_with(USER)


@pytest.mark.asyncio
async def test_platform_rows_user_blacklist_hides_the_row(env, monkeypatch):
    _stored(monkeypatch, {"disabled_models": ["pic-a"]})
    env.rows = [
        catalog_row("pic-a", type="image"),
        catalog_row("pic-b", type="image"),
    ]
    rows = await pp.platform_rows(USER, type="image", purpose="dispatch")
    assert row_names(rows) == ["pic-b"]


@pytest.mark.asyncio
async def test_platform_rows_blacklist_follows_the_rename_alias(env, monkeypatch):
    _stored(monkeypatch, {"disabled_models": ["mediahub-pic"]})
    env.rows = [catalog_row("nous-pic", type="image")]
    assert await pp.platform_rows(USER, purpose="picker") == []


@pytest.mark.asyncio
async def test_platform_rows_master_switch_off_is_empty(env, monkeypatch):
    _stored(monkeypatch, {"enabled": False})
    env.rows = [catalog_row("pic", type="image")]
    assert await pp.platform_rows(USER, purpose="dispatch") == []


@pytest.mark.asyncio
async def test_platform_rows_governance_off_is_empty(env, monkeypatch):
    _stored(monkeypatch, {})
    env.governance = False
    env.rows = [catalog_row("pic", type="image")]
    assert await pp.platform_rows(USER, purpose="dispatch") == []
    assert await pp.platform_rows(None, purpose="system") == []


@pytest.mark.asyncio
async def test_platform_rows_engine_not_listing_the_service_drops_it(env, monkeypatch):
    _stored(monkeypatch, {})
    env.rows = [
        engine_row("nous-gone", "gone-svc", type="image"),
        engine_row("nous-here", "here-svc", type="image"),
    ]
    env.engine_answers = [listed(("here-svc", True))]
    rows = await pp.platform_rows(USER, type="image", purpose="dispatch")
    assert row_names(rows) == ["nous-here"]
    assert rows[0].model.status == "ok"


@pytest.mark.asyncio
async def test_platform_rows_drops_failed_rows(env, monkeypatch):
    _stored(monkeypatch, {})
    env.rows = [
        catalog_row("bad", last_test_status="fail"),
        catalog_row("good"),
    ]
    assert row_names(await pp.platform_rows(USER, purpose="picker")) == ["good"]


@pytest.mark.asyncio
async def test_platform_rows_picker_leaves_out_upscale_only_rows(env, monkeypatch):
    _stored(monkeypatch, {})
    env.rows = [
        engine_row("nous-upscale", "studio-upscale", type="image"),
        catalog_row("pic", type="image"),
    ]
    env.engine_answers = [listed(("studio-upscale", True))]
    assert row_names(await pp.platform_rows(USER, purpose="picker")) == ["pic"]
    dispatch = await pp.platform_rows(USER, purpose="dispatch")
    assert row_names(dispatch) == ["nous-upscale", "pic"]


@pytest.mark.asyncio
async def test_platform_rows_read_failure_raises_for_dispatch_only(env, monkeypatch):
    _stored(monkeypatch, {})
    env.repo.list_enabled_private = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        await pp.platform_rows(USER, purpose="dispatch")
    assert await pp.platform_rows(USER, purpose="picker") == []
    assert await pp.platform_rows(None, purpose="system") == []


@pytest.mark.asyncio
async def test_platform_rows_system_takes_no_user(env):
    with pytest.raises(ValueError):
        await pp.platform_rows(USER, purpose="system")


@pytest.mark.asyncio
async def test_view_and_platform_rows_agree(env, monkeypatch):
    """The settings card and every backend decision are one computation."""
    stored = {"disabled_models": ["b"]}
    _stored(monkeypatch, stored)
    env.rows = [catalog_row("a"), catalog_row("b"), catalog_row("c", type="image")]
    view = await pp.platform_provider_view(USER, stored_nous=stored)
    rows = await pp.platform_rows(USER, purpose="dispatch")
    assert list(view.enabled_models) == row_names(rows) == ["a", "c"]


# ─── user_may_use: the user gates on one resolved row ───────────────────────


@pytest.mark.asyncio
async def test_user_may_use_admits_a_normal_row(monkeypatch):
    _stored(monkeypatch, {})
    await pp.user_may_use(USER, {"name": "nous-a", "owner_user_id": None})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nous, row, reason",
    [
        ({}, {"name": "x", "owner_user_id": "someone-else"}, "owner_scope"),
        ({"enabled": False}, {"name": "x"}, "platform_card_disabled"),
        ({"disabled_models": ["x"]}, {"name": "x"}, "user_disabled"),
        ({"disabled_models": ["mediahub-x"]}, {"name": "nous-x"}, "user_disabled"),
    ],
)
async def test_user_may_use_refuses_typed(monkeypatch, nous, row, reason):
    _stored(monkeypatch, nous)
    with pytest.raises(pp.PlatformModelNotAvailableError) as info:
        await pp.user_may_use(USER, row)
    err = info.value
    assert isinstance(err, RuntimeError)
    assert err.status_code == 409
    assert err.details == {
        "code": "platform_model_not_available",
        "model": row["name"],
        "reason": reason,
    }


# ─── residue (P4 G) ─────────────────────────────────────────────────────────


def test_nous_engine_provider_has_one_definition():
    import app.repositories.nous_engine_sync_repository as sync_repo
    import app.repositories.nous_model_repository as repo
    import app.services.ai.nous_model_health as health

    assert (
        ec.NOUS_ENGINE_PROVIDER
        is repo.NOUS_ENGINE_PROVIDER
        is health.NOUS_ENGINE_PROVIDER
        is sync_repo.NOUS_ENGINE_PROVIDER
        == "nous"
    )


def test_last_good_snapshots_are_bounded(monkeypatch):
    ec.reset_engine_cache()
    snap = ec.EngineSnapshot(services={}, fetched_at=None, reachable=True, error=None)
    for i in range(ec._CACHE_MAXSIZE + 10):
        ec._remember(f"k{i}", (float(i), snap))
    assert len(ec._last_good) == ec._CACHE_MAXSIZE
    assert "k0" not in ec._last_good and f"k{ec._CACHE_MAXSIZE + 9}" in ec._last_good
    ec._remember("k20", (99.0, snap))  # re-stored → newest
    assert list(ec._last_good)[-1] == "k20"
    ec.reset_engine_cache()


# ─── governance read failure is "unknown", not "off" ────────────────────────


@pytest.mark.asyncio
async def test_governance_read_failure_lists_rows_and_says_unknown(monkeypatch, caplog):
    """A failed ``nous.user_enabled`` read is not a negative answer: the view
    lists rows as if on, marks ``governance="unknown"`` and logs a WARNING —
    dispatch, defaults, vectors and bundles must not go empty on a DB blip."""
    import logging

    from app.services.ai.governance import ai_governance as gov

    real_reader = gov.nous_global_state
    env = Env(monkeypatch)
    # Undo Env's stub: exercise the real three-state reader over a failing read.
    monkeypatch.setattr(gov, "nous_global_state", real_reader)
    monkeypatch.setattr(
        gov, "_read_raw_strict", AsyncMock(side_effect=RuntimeError("db down"))
    )
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value={}),
    )
    env.rows = [catalog_row("nous-a"), catalog_row("pic", type="image")]
    with caplog.at_level(logging.WARNING, logger=gov.logger.name):
        view = await pp.platform_provider_view(USER, stored_nous={})
        rows = await pp.platform_rows(USER, purpose="dispatch")
        system = await pp.platform_rows_and_engine(None, purpose="system")
    assert view.governance == "unknown"
    assert list(view.enabled_models) == ["nous-a", "pic"]
    assert row_names(rows) == ["nous-a", "pic"]
    assert system.governance == "unknown" and row_names(system.rows) == [
        "nous-a",
        "pic",
    ]
    assert any("nous.user_enabled" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_governance_read_as_false_empties_the_view(monkeypatch):
    """Only a read that ANSWERED off closes the view."""
    from app.services.ai.governance import ai_governance as gov

    real_reader = gov.nous_global_state
    env = Env(monkeypatch)
    monkeypatch.setattr(gov, "nous_global_state", real_reader)
    monkeypatch.setattr(gov, "_read_raw_strict", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value={}),
    )
    env.rows = [catalog_row("nous-a")]
    view = await pp.platform_provider_view(USER, stored_nous={})
    assert view.governance == "off" and view.models == ()
    assert await pp.platform_rows(USER, purpose="dispatch") == []
    env.repo.list_enabled_private.assert_not_awaited()
