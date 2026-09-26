"""The model picker shows only what can actually generate right now (2026-09-05).

User report: the picker listed everything — a server codex row AND its local
twin (labelled "· local"), a Seedream row whose upstream model is Shutdown,
Dreamina server AND local. The daemon rule lives in ONE pure function,
``local_readiness.local_verdict``; ``GET /ai/platform-status`` publishes its
answer per row (``local_ready`` / ``superseded``) and every generation picker
applies it client side (spec 2026-09-25 §3.7, ``useGenerationModels``):

* a LOCAL row (codex-local / jimeng-local) is offered only while the user's
  daemon is online and its env_report says that engine is ready;
* when a local twin is offered, the SERVER row of the same engine is hidden
  (same account either way). Since 2026-09-23 only Dreamina has a server
  twin — the server codex row was retired (mig 498), GPT image is local-only.

Failed rows never reach a picker at all: the platform view drops them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.generation import local_readiness as lr
from app.services.generation.local_readiness import (
    LocalReadiness,
    local_engine_readiness,
    local_verdict,
)

PROVIDERS = {
    "codex-local-image": "codex-local",
    "jimeng-cli-image": "jimeng-cli",
    "jimeng-local-image": "jimeng-local",
    "mediahub-doubao-seedream-t2i": "doubao",
}


def _offered(readiness: LocalReadiness) -> set[str]:
    return {
        name
        for name, provider in PROVIDERS.items()
        if local_verdict(provider, readiness).offered
    }


def test_no_daemon_online_hides_local_rows_and_keeps_server_rows():
    names = _offered(LocalReadiness(codex=False, dreamina=False))
    assert "codex-local-image" not in names and "jimeng-local-image" not in names
    assert "jimeng-cli-image" in names


def test_ready_local_codex_is_shown_and_has_no_server_twin():
    # The server-side codex twin was retired 2026-09-23 (mig 498): GPT image
    # is local-only, so there is nothing left for the local row to replace.
    assert "codex" not in lr.SERVER_TWIN_OF
    names = _offered(LocalReadiness(codex=True, dreamina=False))
    assert "codex-local-image" in names
    # dreamina not ready locally → its server row stays, local hidden
    assert "jimeng-cli-image" in names and "jimeng-local-image" not in names


def test_both_ready_hides_the_server_twin():
    names = _offered(LocalReadiness(codex=True, dreamina=True))
    assert {"codex-local-image", "jimeng-local-image"} <= names
    assert "jimeng-cli-image" not in names
    verdict = local_verdict("jimeng-cli", LocalReadiness(codex=True, dreamina=True))
    assert verdict.superseded is True and verdict.local_ready is None


def test_server_rows_have_no_local_verdict():
    verdict = local_verdict("doubao", LocalReadiness(codex=False, dreamina=False))
    assert verdict.local_ready is None and verdict.superseded is False


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
