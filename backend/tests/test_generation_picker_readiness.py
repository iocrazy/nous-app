"""The model picker shows only what can actually generate right now (2026-09-05).

User report: the picker listed everything — a server codex row AND its local
twin (labelled "· local"), a Seedream row whose upstream model is Shutdown,
Dreamina server AND local. Rules, in one place (`visible_generation_rows`, so
picker / capabilities / asset bundle stay row-for-row equal):

* a LOCAL row (codex-local / jimeng-local) is shown only while the user's
  daemon is online and its env_report says that engine is ready;
* when a local twin is shown, the SERVER row of the same engine is hidden
  ("GPT 只显示 local" — it is the same ChatGPT account either way);
* a row whose last probe failed is hidden.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.generation import local_readiness as lr
from app.services.generation.local_readiness import (
    LocalReadiness,
    local_engine_readiness,
)

# ``from app.api import canvases_router`` hands back the APIRouter OBJECT
# (``app/api/__init__.py`` re-exports it), not the module.
canvases_router = sys.modules.get("app.api.canvases_router") or __import__(
    "app.api.canvases_router", fromlist=["x"]
)

ROWS = [
    {
        "name": "codex-image",
        "display_name": "GPT Image 2 (Codex)",
        "type": "image",
        "actual_provider": "codex",
        "is_local": False,
    },
    {
        "name": "codex-local-image",
        "display_name": "GPT Image 2 (Local)",
        "type": "image",
        "actual_provider": "codex-local",
        "is_local": True,
    },
    {
        "name": "jimeng-cli-image",
        "display_name": "Dreamina (即梦) Image",
        "type": "image",
        "actual_provider": "jimeng-cli",
        "is_local": False,
    },
    {
        "name": "jimeng-local-image",
        "display_name": "Dreamina (Local)",
        "type": "image",
        "actual_provider": "jimeng-local",
        "is_local": True,
    },
    {
        "name": "mediahub-doubao-seedream-t2i",
        "display_name": "Doubao Seedream (T2I)",
        "type": "image",
        "actual_provider": "doubao",
        "is_local": False,
    },
]


def _install(monkeypatch, rows, readiness: LocalReadiness):
    from app.repositories import mediahub_model_repository as repo_mod

    repo = SimpleNamespace(list_enabled=AsyncMock(return_value=[dict(r) for r in rows]))
    monkeypatch.setattr(repo_mod, "get_mediahub_model_repository", lambda: repo)
    from app.services.ai import platform_model_visibility as pmv

    monkeypatch.setattr(
        pmv, "filter_platform_models_for_user", AsyncMock(side_effect=lambda _u, r: r)
    )
    monkeypatch.setattr(lr, "local_engine_readiness", AsyncMock(return_value=readiness))
    return repo


async def _names(user="u1"):
    return [r["name"] for r in await canvases_router._visible_generation_rows(user)]


@pytest.mark.asyncio
async def test_no_daemon_online_hides_local_rows_and_keeps_server_rows(monkeypatch):
    _install(monkeypatch, ROWS, LocalReadiness(codex=False, dreamina=False))
    names = await _names()
    assert "codex-local-image" not in names and "jimeng-local-image" not in names
    assert "codex-image" in names and "jimeng-cli-image" in names


@pytest.mark.asyncio
async def test_ready_local_codex_replaces_its_server_twin(monkeypatch):
    _install(monkeypatch, ROWS, LocalReadiness(codex=True, dreamina=False))
    names = await _names()
    assert "codex-local-image" in names
    assert "codex-image" not in names, "GPT 只显示 local"
    # dreamina not ready locally → its server row stays, local hidden
    assert "jimeng-cli-image" in names and "jimeng-local-image" not in names


@pytest.mark.asyncio
async def test_both_ready_hides_both_server_twins(monkeypatch):
    _install(monkeypatch, ROWS, LocalReadiness(codex=True, dreamina=True))
    names = await _names()
    assert names.count("codex-local-image") == 1 and "codex-image" not in names
    assert names.count("jimeng-local-image") == 1 and "jimeng-cli-image" not in names


@pytest.mark.asyncio
async def test_a_row_whose_last_probe_failed_is_hidden(monkeypatch):
    rows = [dict(r) for r in ROWS]
    rows[4]["last_test_status"] = "failed"
    _install(monkeypatch, rows, LocalReadiness(codex=False, dreamina=False))
    assert "mediahub-doubao-seedream-t2i" not in await _names()


@pytest.mark.asyncio
async def test_actual_provider_is_still_stripped_unless_asked_for(monkeypatch):
    """The filter needs actual_provider; callers that did not ask must not
    start receiving it (the picker response is public fields only)."""
    _install(monkeypatch, ROWS, LocalReadiness(codex=False, dreamina=False))
    rows = await canvases_router._visible_generation_rows("u1")
    assert all("actual_provider" not in r for r in rows)
    rows2 = await canvases_router._visible_generation_rows(
        "u1", include_actual_provider=True
    )
    assert all("actual_provider" in r for r in rows2)


# ── readiness derivation from presence + env_report ────────────────────────


@pytest.mark.asyncio
async def test_readiness_reads_the_online_devices_env_report():
    devices = [
        {
            "id": "343307213001627",
            "env_report": {
                "auth_ok": True,
                "skill_ok": True,
                "dreamina_ok": True,
                "dreamina_auth_ok": False,
            },
        }
    ]
    r = await local_engine_readiness(
        "u1",
        online_device_id=AsyncMock(return_value="343307213001627"),
        list_devices=AsyncMock(return_value=devices),
        card_enabled=AsyncMock(return_value=True),
    )
    assert r == LocalReadiness(codex=True, dreamina=False)


@pytest.mark.asyncio
async def test_readiness_is_all_false_when_nothing_is_online():
    r = await local_engine_readiness(
        "u1",
        online_device_id=AsyncMock(return_value=None),
        list_devices=AsyncMock(return_value=[]),
    )
    assert r == LocalReadiness(codex=False, dreamina=False)


@pytest.mark.asyncio
async def test_readiness_never_raises_a_presence_outage_into_the_picker():
    """Redis down must degrade to 'nothing local', not a 500 on the picker."""
    r = await local_engine_readiness(
        "u1",
        online_device_id=AsyncMock(side_effect=RuntimeError("redis down")),
        list_devices=AsyncMock(return_value=[]),
    )
    assert r == LocalReadiness(codex=False, dreamina=False)


# ── the Providers page is the ONE management entry (user, 2026-09-06) ────────
# "画布属于应用，应用中都是要服务商中有对应可用模型才可以选" — a local engine is
# offered only when its provider CARD is switched on, not merely when the
# daemon happens to be online.


@pytest.mark.asyncio
async def test_codex_is_offered_only_when_its_provider_card_is_enabled():
    devices = [{"id": "d1", "env_report": {"auth_ok": True, "skill_ok": True}}]
    kw = dict(
        online_device_id=AsyncMock(return_value="d1"),
        list_devices=AsyncMock(return_value=devices),
    )
    off = await local_engine_readiness(
        "u1", card_enabled=AsyncMock(return_value=False), **kw
    )
    on = await local_engine_readiness(
        "u1", card_enabled=AsyncMock(return_value=True), **kw
    )
    assert off.codex is False and on.codex is True
