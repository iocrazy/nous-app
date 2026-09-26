"""Admin catalog: nous-engine rows carry the engine's live answer (spec
2026-09-25 §3.5) — ``engine_status`` listed / missing / unreachable and
``engine_ready`` — read from the engine's own list with each row's credential.
Other rows carry ``None``; nothing is written or disabled."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.ai.engine_catalog as ec

pytestmark = pytest.mark.unit

ENGINE = "http://engine.test/v1"


def _row(id_: int, name: str, **over) -> dict:
    row = {
        "id": 7300000000000000000 + id_,
        "name": name,
        "display_name": name,
        "type": "llm",
        "actual_provider": "nous",
        "actual_model": name.removeprefix("nous-"),
        "api_key": "sk-engine",
        "base_url": ENGINE,
        "pricing_type": "per_hour",
        "pricing_value": 0,
        "is_enabled": True,
        "sort_order": id_,
        "last_test_status": "fail",
    }
    row.update(over)
    return row


async def _list(rows: list[dict], answers: list[ec._Read]) -> tuple[list, int]:
    from app.api.admin.nous_model_router import list_nous_models

    calls = []

    async def _fetch(base_url, api_key):
        calls.append((base_url, api_key))
        return answers.pop(0)

    ec.reset_engine_cache()
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=rows)
    try:
        with (
            patch.object(ec, "_fetch", _fetch),
            patch(
                "app.api.admin.nous_model_router.get_nous_model_repository",
                return_value=repo,
            ),
            patch(
                "app.api.admin.nous_model_router.load_priced_models",
                AsyncMock(return_value={"token": set(), "per_call": set()}),
            ),
        ):
            out = await list_nous_models(MagicMock())
    finally:
        ec.reset_engine_cache()
    # Read-only: the list endpoint never writes the catalog.
    assert not repo.update.called and not repo.create.called
    return out, len(calls)


def _svc(sid: str, ready: bool) -> ec.EngineService:
    return ec.EngineService(
        id=sid, type="llm", ready=ready, context_window=None, capabilities=None
    )


@pytest.mark.asyncio
async def test_listed_missing_and_other_rows():
    rows = [
        _row(1, "nous-qwen3-8b"),
        _row(2, "nous-wemm-2b", type="embedding"),
        _row(3, "nous-revoked", is_enabled=False),
        _row(4, "nous-doubao", actual_provider="ark", base_url="https://ark"),
    ]
    engine = ec._Read(
        services={"qwen3-8b": _svc("qwen3-8b", True), "wemm-2b": _svc("wemm-2b", False)}
    )
    out, calls = await _list(rows, [engine])
    assert calls == 1
    got = {o.name: (o.engine_status, o.engine_ready) for o in out}
    assert got == {
        "nous-qwen3-8b": ("listed", True),
        "nous-wemm-2b": ("listed", False),
        "nous-revoked": ("missing", None),
        "nous-doubao": (None, None),
    }
    # A disabled row stays disabled and a missing row stays enabled: the
    # overlay reports, the admin decides.
    assert [o.is_enabled for o in out] == [True, True, False, True]
    dumped = out[0].model_dump(mode="json")
    assert dumped["engine_status"] == "listed" and dumped["engine_ready"] is True


@pytest.mark.asyncio
async def test_unreachable_engine_is_not_missing():
    rows = [_row(1, "nous-qwen3-8b"), _row(2, "nous-other", api_key="sk-other")]
    out, calls = await _list(
        rows,
        [
            ec._Read(error="ConnectError: refused"),
            ec._Read(error=ec.UNAUTHORIZED_ERROR, unauthorized=True),
        ],
    )
    assert calls == 2  # two credentials, each read with its own key
    assert [(o.engine_status, o.engine_ready) for o in out] == [
        ("unreachable", None),
        ("unreachable", None),
    ]
