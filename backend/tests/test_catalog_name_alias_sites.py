"""Every by-name catalog lookup resolves across the ``mediahub-`` ↔ ``nous-``
rename, in both directions, with the exact name always winning.

One test group per lookup site. The helper's own rules are in
``test_catalog_names.py``; this file proves each site actually routes through it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.repositories.nous_model_repository as repo_mod
from app.models import NousModels
from app.repositories.nous_model_repository import NousModelRepository

# ─── NousModelRepository.get_by_name (the SQL choke point) ─────────────────


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _Session:
    """Filters the configured rows by the statement's ``name IN (...)`` binds,
    so the test sees what Postgres would return — both spellings when both
    exist, in arbitrary order."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.binds: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        binds = stmt.compile().params
        self.binds.append(binds)
        wanted: set[str] = set()
        for v in binds.values():
            if isinstance(v, (list, tuple)):
                wanted.update(v)
            elif isinstance(v, str):
                wanted.add(v)
        return _Result([r for r in self.rows if r.name in wanted])


class _CM:
    def __init__(self, session: _Session) -> None:
        self._s = session

    async def __aenter__(self) -> _Session:
        return self._s

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _catalog(monkeypatch: pytest.MonkeyPatch, *names: str) -> _Session:
    rows = [
        NousModels(
            id=i + 1,
            name=n,
            is_enabled=True,
            actual_provider="doubao",
            actual_model=f"actual-{n}",
            api_key="k",
            base_url="https://ark.example/v1",
        )
        for i, n in enumerate(names)
    ]
    session = _Session(rows)
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _CM(session))
    return session


@pytest.mark.asyncio
async def test_repo_old_name_finds_renamed_row(monkeypatch):
    _catalog(monkeypatch, "nous-moss-asr")
    row = await NousModelRepository().get_by_name("mediahub-moss-asr")
    assert row is not None and row["name"] == "nous-moss-asr"


@pytest.mark.asyncio
async def test_repo_new_name_finds_unrenamed_row(monkeypatch):
    _catalog(monkeypatch, "mediahub-doubao-embedding-vision")
    row = await NousModelRepository().get_by_name("nous-doubao-embedding-vision")
    assert row is not None and row["name"] == "mediahub-doubao-embedding-vision"


@pytest.mark.asyncio
async def test_repo_exact_wins_when_both_spellings_exist(monkeypatch):
    # Alias row first in "result order" — exact must still win.
    _catalog(monkeypatch, "nous-x", "mediahub-x")
    repo = NousModelRepository()
    assert (await repo.get_by_name("mediahub-x"))["name"] == "mediahub-x"
    assert (await repo.get_by_name("nous-x"))["name"] == "nous-x"


@pytest.mark.asyncio
async def test_repo_unprefixed_name_queries_only_itself(monkeypatch):
    session = _catalog(monkeypatch, "jimeng-cli-image")
    row = await NousModelRepository().get_by_name("jimeng-cli-image")
    assert row is not None
    names: list[str] = []
    for b in session.binds:
        for v in b.values():
            if isinstance(v, (list, tuple)):
                names.extend(v)
            elif isinstance(v, str):
                names.append(v)
    assert names == ["jimeng-cli-image"]


@pytest.mark.asyncio
async def test_repo_miss_stays_none(monkeypatch):
    _catalog(monkeypatch, "nous-other")
    assert await NousModelRepository().get_by_name("mediahub-moss-asr") is None


# ─── resolve_nous_model / resolve_platform_model / maintenance default ─────


@pytest.mark.asyncio
async def test_default_maintenance_model_is_the_new_spelling():
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_MAINTENANCE_MODEL,
    )

    assert DEFAULT_MAINTENANCE_MODEL == "nous-doubao-seed-2-0-lite"


@pytest.mark.asyncio
async def test_default_maintenance_model_resolves_against_old_catalog(monkeypatch):
    """Today's production catalog only has the ``mediahub-`` row."""
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_MAINTENANCE_MODEL,
        resolve_platform_model,
    )

    _catalog(monkeypatch, "mediahub-doubao-seed-2-0-lite")
    hit = await resolve_platform_model(DEFAULT_MAINTENANCE_MODEL)
    assert hit is not None
    assert hit[2] == "actual-mediahub-doubao-seed-2-0-lite"


@pytest.mark.asyncio
async def test_default_maintenance_model_resolves_after_rename(monkeypatch):
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_MAINTENANCE_MODEL,
        resolve_platform_model,
    )

    _catalog(monkeypatch, "nous-doubao-seed-2-0-lite")
    hit = await resolve_platform_model(DEFAULT_MAINTENANCE_MODEL)
    assert hit is not None


@pytest.mark.asyncio
async def test_gated_resolver_old_assignment_hits_renamed_row(monkeypatch):
    """``nous:mediahub-moss-asr`` in a user's task_assignment, row renamed."""
    from app.services.ai.providers import ai_provider_helpers as h

    _catalog(monkeypatch, "nous-moss-asr")
    with patch(
        "app.services.ai.governance.ai_governance.is_nous_allowed",
        AsyncMock(return_value=True),
    ):
        hit = await h.resolve_nous_model("mediahub-moss-asr", "transcription")
    assert hit is not None and hit[2] == "actual-nous-moss-asr"


# ─── db_registry (image/video row selection + visibility) ──────────────────


def test_db_registry_explicit_match_both_directions():
    from app.services.media.parsers.video_providers import db_registry

    old = [{"name": "mediahub-seedream", "actual_provider": "doubao"}]
    new = [{"name": "nous-seedream", "actual_provider": "doubao"}]
    assert db_registry._explicit_match(new, "mediahub-seedream") is new[0]
    assert db_registry._explicit_match(old, "nous-seedream") is old[0]


def test_db_registry_exact_wins_over_alias_and_list_order():
    from app.services.media.parsers.video_providers import db_registry

    rows = [
        {"name": "nous-seedream", "id": 1},
        {"name": "mediahub-seedream", "id": 2},
    ]
    assert db_registry._explicit_match(rows, "mediahub-seedream")["id"] == 2


def test_db_registry_actual_model_match_still_beats_alias():
    """An exact hit on any existing field outranks an alias hit on name."""
    from app.services.media.parsers.video_providers import db_registry

    rows = [
        {"name": "nous-a", "actual_model": "x", "id": 1},
        {"name": "other", "actual_model": "mediahub-a", "id": 2},
    ]
    assert db_registry._explicit_match(rows, "mediahub-a")["id"] == 2


def test_db_registry_pick_row_honours_alias_over_jimeng_preference():
    from app.services.media.parsers.video_providers import db_registry

    rows = [
        {"name": "jimeng-cli-image", "actual_provider": "jimeng-cli"},
        {"name": "nous-seedream", "actual_provider": "doubao"},
    ]
    assert db_registry._pick_row(rows, "mediahub-seedream")["name"] == "nous-seedream"


def test_db_registry_private_alias_row_still_raises_for_non_owner():
    from app.services.media.parsers.video_providers import db_registry

    rows = [{"name": "nous-private", "owner_user_id": "owner"}]
    with pytest.raises(RuntimeError, match="private"):
        db_registry._visible_rows(rows, "mediahub-private", "someone-else", "image")


# ─── local_dispatch.resolve_local_engine ───────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_local_engine_old_name_hits_renamed_local_row():
    from app.services.generation import local_dispatch

    rows = [
        {
            "name": "nous-dreamina",
            "actual_provider": "jimeng-local",
            "actual_model": "seedream-4",
        }
    ]
    with patch(
        "app.services.media.parsers.video_providers.db_registry._enabled_rows",
        AsyncMock(return_value=rows),
    ):
        got = await local_dispatch.resolve_local_engine(
            "mediahub-dreamina", "image", user_id="u"
        )
    assert got is not None and got[1] == "seedream-4"


@pytest.mark.asyncio
async def test_resolve_local_engine_exact_wins():
    from app.services.generation import local_dispatch

    rows = [
        {"name": "nous-d", "actual_provider": "jimeng-local", "actual_model": "new"},
        {"name": "mediahub-d", "actual_provider": "doubao", "actual_model": "old"},
    ]
    with patch(
        "app.services.media.parsers.video_providers.db_registry._enabled_rows",
        AsyncMock(return_value=rows),
    ):
        # exact mediahub-d is a server-side row → not local, even though the
        # aliased nous-d row is local.
        assert (
            await local_dispatch.resolve_local_engine("mediahub-d", "image", "u")
            is None
        )


# ─── generation.model_capabilities.capabilities_for_model ──────────────────


@pytest.mark.asyncio
async def test_capabilities_for_model_resolves_alias():
    from app.services.generation import model_capabilities as gm

    rows = [{"name": "mediahub-seedream", "actual_provider": "doubao"}]
    with patch.object(gm, "visible_generation_rows", AsyncMock(return_value=rows)):
        caps = await gm.capabilities_for_model("nous-seedream", "u")
    assert caps is not None


@pytest.mark.asyncio
async def test_capabilities_for_model_unknown_stays_none():
    from app.services.generation import model_capabilities as gm

    rows = [{"name": "mediahub-seedream", "actual_provider": "doubao"}]
    with patch.object(gm, "visible_generation_rows", AsyncMock(return_value=rows)):
        assert await gm.capabilities_for_model("seedream", "u") is None


# ─── platform_model_visibility (user's persisted disabled_models) ──────────


@pytest.mark.asyncio
async def test_disabled_old_name_hides_renamed_row():
    from app.services.ai import platform_model_visibility as v

    rows = [{"name": "nous-moss-asr"}, {"name": "nous-other"}]
    with patch.object(
        v,
        "platform_model_gate",
        AsyncMock(return_value=(True, frozenset({"mediahub-moss-asr"}))),
    ):
        out = await v.filter_platform_models_for_user("u", rows)
    assert [r["name"] for r in out] == ["nous-other"]


@pytest.mark.asyncio
async def test_disabled_new_name_hides_unrenamed_row():
    from app.services.ai import platform_model_visibility as v

    rows = [{"name": "mediahub-moss-asr"}]
    with patch.object(
        v,
        "platform_model_gate",
        AsyncMock(return_value=(True, frozenset({"nous-moss-asr"}))),
    ):
        assert await v.filter_platform_models_for_user("u", rows) == []


@pytest.mark.asyncio
async def test_disabled_exact_name_hides_only_that_row_when_both_exist():
    from app.services.ai import platform_model_visibility as v

    rows = [{"name": "nous-x"}, {"name": "mediahub-x"}]
    with patch.object(
        v,
        "platform_model_gate",
        AsyncMock(return_value=(True, frozenset({"mediahub-x"}))),
    ):
        out = await v.filter_platform_models_for_user("u", rows)
    assert [r["name"] for r in out] == ["nous-x"]


# ─── ai.model_capabilities local-vision catalog rows ───────────────────────


@pytest.mark.asyncio
async def test_local_vision_catalog_name_resolves_alias(monkeypatch):
    from app.services.ai import model_capabilities as m

    m._cache.clear()
    m._local_vision_models.clear()
    m._cache_loaded = False
    monkeypatch.setattr(m, "_fetch_capabilities", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        m,
        "_fetch_local_vision_models",
        AsyncMock(return_value={"mediahub-codex-local"}),
    )
    try:
        assert await m.model_supports_vision("nous-codex-local") is True
    finally:
        m._cache.clear()
        m._local_vision_models.clear()
        m._cache_loaded = False


# ─── admin create / update: legacy-name collision → 409 ────────────────────


_FULL_ROW = {
    "name": "n",
    "display_name": "X",
    "type": "llm",
    "actual_provider": "doubao",
    "actual_model": "m",
    "pricing_type": "per_call",
    "pricing_value": 0,
    "is_enabled": True,
    "sort_order": 0,
}


def _admin_repo(existing: dict | None) -> MagicMock:
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=existing)
    repo.list_all = AsyncMock(return_value=[])
    repo.create = AsyncMock(side_effect=lambda data: {**_FULL_ROW, "id": 99, **data})
    repo.update = AsyncMock(
        side_effect=lambda mid, data: {**_FULL_ROW, "id": int(mid), **data}
    )
    return repo


def _create_body(name: str):
    from app.schemas.nous_model import NousModelCreate

    return NousModelCreate(
        name=name,
        display_name="X",
        type="llm",
        actual_provider="doubao",
        actual_model="m",
        api_key="k",
        base_url="https://ark.example/v1",
    )


@pytest.mark.asyncio
async def test_admin_create_new_spelling_while_legacy_exists_is_409():
    from app.api.admin.nous_model_router import create_nous_model

    repo = _admin_repo({"id": 5, "name": "mediahub-x"})
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        with pytest.raises(HTTPException) as exc:
            await create_nous_model(_create_body("nous-x"), MagicMock())
    assert exc.value.status_code == 409
    assert "mediahub-x" in str(exc.value.detail)
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_admin_create_exact_duplicate_is_409():
    from app.api.admin.nous_model_router import create_nous_model

    repo = _admin_repo({"id": 5, "name": "nous-x"})
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        with pytest.raises(HTTPException) as exc:
            await create_nous_model(_create_body("nous-x"), MagicMock())
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_admin_create_free_name_proceeds():
    from app.api.admin.nous_model_router import create_nous_model

    repo = _admin_repo(None)
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        await create_nous_model(_create_body("nous-y"), MagicMock())
    repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_update_renaming_to_another_rows_alias_is_409():
    from app.api.admin.nous_model_router import update_nous_model
    from app.schemas.nous_model import NousModelUpdate

    repo = _admin_repo({"id": 5, "name": "mediahub-x"})
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        with pytest.raises(HTTPException) as exc:
            await update_nous_model("6", NousModelUpdate(name="nous-x"), MagicMock())
    assert exc.value.status_code == 409
    repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_admin_update_renaming_own_legacy_row_is_allowed():
    """Renaming mediahub-x → nous-x on THE SAME row is the rename itself."""
    from app.api.admin.nous_model_router import update_nous_model
    from app.schemas.nous_model import NousModelUpdate

    repo = _admin_repo({"id": 5, "name": "mediahub-x"})
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        await update_nous_model("5", NousModelUpdate(name="nous-x"), MagicMock())
    repo.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_update_without_name_skips_collision_lookup():
    from app.api.admin.nous_model_router import update_nous_model
    from app.schemas.nous_model import NousModelUpdate

    repo = _admin_repo({"id": 5, "name": "whatever"})
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        await update_nous_model("6", NousModelUpdate(display_name="Y"), MagicMock())
    repo.get_by_name.assert_not_called()
    repo.update.assert_awaited_once()


# ─── explicit rename nous-qwen3-llm ↔ nous-qwen3-8-27b, per lookup site ────
#
# Every site above is proven for the prefix swap; these prove the explicit
# rename table reaches them too (old name → renamed row, and the reverse for
# the window before the data migration lands).

_OLD_Q = "nous-qwen3-llm"
_NEW_Q = "nous-qwen3-8-27b"


@pytest.mark.asyncio
async def test_repo_rename_old_name_finds_renamed_row(monkeypatch):
    _catalog(monkeypatch, _NEW_Q)
    row = await NousModelRepository().get_by_name(_OLD_Q)
    assert row is not None and row["name"] == _NEW_Q


@pytest.mark.asyncio
async def test_repo_rename_new_name_finds_unrenamed_row(monkeypatch):
    _catalog(monkeypatch, _OLD_Q)
    row = await NousModelRepository().get_by_name(_NEW_Q)
    assert row is not None and row["name"] == _OLD_Q


@pytest.mark.asyncio
async def test_repo_rename_exact_wins_when_both_exist(monkeypatch):
    _catalog(monkeypatch, _NEW_Q, _OLD_Q)
    repo = NousModelRepository()
    assert (await repo.get_by_name(_OLD_Q))["name"] == _OLD_Q
    assert (await repo.get_by_name(_NEW_Q))["name"] == _NEW_Q


@pytest.mark.asyncio
async def test_resolve_db_adapter_old_name_hits_renamed_row(monkeypatch):
    """A canvas node still carrying ``provider_slug: nous-qwen3-llm`` after the
    row is renamed: the real resolve chain (only the DB session is stubbed)
    must land on the renamed row's ``actual_model`` and platform key."""
    from app.services.ai.providers.ai_provider_helpers import resolve_db_adapter

    rows = [
        NousModels(
            id=1,
            name=_NEW_Q,
            is_enabled=True,
            actual_provider="nous",
            actual_model="qwen3-8-27b",
            api_key="platform-key",
            base_url="http://host.docker.internal:8000/v1",
        )
    ]
    session = _Session(rows)
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _CM(session))
    with patch(
        "app.services.ai.governance.ai_governance.is_nous_allowed",
        AsyncMock(return_value=True),
    ):
        adapter = await resolve_db_adapter(_OLD_Q, "canvas")
    assert type(adapter).__name__ == "NousAdapter"
    assert adapter.default_model == "qwen3-8-27b"
    assert adapter.api_key == "platform-key"


def test_db_registry_rename_explicit_match_both_directions():
    from app.services.media.parsers.video_providers import db_registry

    old = [{"name": _OLD_Q}]
    new = [{"name": _NEW_Q}]
    assert db_registry._explicit_match(new, _OLD_Q) is new[0]
    assert db_registry._explicit_match(old, _NEW_Q) is old[0]


@pytest.mark.asyncio
async def test_resolve_local_engine_rename_old_name_hits_renamed_row():
    from app.services.generation import local_dispatch

    rows = [{"name": _NEW_Q, "actual_provider": "jimeng-local", "actual_model": "m"}]
    with patch(
        "app.services.media.parsers.video_providers.db_registry._enabled_rows",
        AsyncMock(return_value=rows),
    ):
        got = await local_dispatch.resolve_local_engine(_OLD_Q, "image", "u")
    assert got is not None and got[1] == "m"


@pytest.mark.asyncio
async def test_capabilities_for_model_rename_resolves():
    from app.services.generation import model_capabilities as gm

    rows = [{"name": _OLD_Q, "actual_provider": "doubao"}]
    with patch.object(gm, "visible_generation_rows", AsyncMock(return_value=rows)):
        assert await gm.capabilities_for_model(_NEW_Q, "u") is not None


@pytest.mark.asyncio
async def test_disabled_old_rename_hides_renamed_row():
    from app.services.ai import platform_model_visibility as v

    rows = [{"name": _NEW_Q}, {"name": "nous-other"}]
    with patch.object(
        v,
        "platform_model_gate",
        AsyncMock(return_value=(True, frozenset({_OLD_Q}))),
    ):
        out = await v.filter_platform_models_for_user("u", rows)
    assert [r["name"] for r in out] == ["nous-other"]


@pytest.mark.asyncio
async def test_local_vision_catalog_name_resolves_rename(monkeypatch):
    from app.services.ai import model_capabilities as m

    m._cache.clear()
    m._local_vision_models.clear()
    m._cache_loaded = False
    monkeypatch.setattr(m, "_fetch_capabilities", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        m, "_fetch_local_vision_models", AsyncMock(return_value={_NEW_Q})
    )
    try:
        assert await m.model_supports_vision(_OLD_Q) is True
    finally:
        m._cache.clear()
        m._local_vision_models.clear()
        m._cache_loaded = False


def test_catalog_window_rename_both_directions(monkeypatch):
    from app.agent_framework import catalog_windows as cw

    monkeypatch.setattr(cw, "_windows", {_NEW_Q: 131072})
    assert cw.catalog_window(_OLD_Q) == 131072
    monkeypatch.setattr(cw, "_windows", {_OLD_Q: 65536})
    assert cw.catalog_window(_NEW_Q) == 65536
