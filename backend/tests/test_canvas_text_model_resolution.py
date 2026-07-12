"""P0-1 regression net: the smart-canvas text prompt MUST resolve its model
through the platform ``mediahub_models`` DB catalog — never a hardcoded slug.

The whole class of bug this guards against (user real-machine walkthrough
2026-07-12) was invisible to the existing suite because
``test_canvas_run_service.py`` mocks ``_get_adapter``/``resolve_db_adapter``
away. These tests deliberately do the OPPOSITE: they drive the REAL resolve
chain (``CanvasRunService._get_adapter`` → ``resolve_db_adapter`` →
``resolve_mediahub_model`` → adapter factory) and only stub the repository
(the DB seam) with a fixture catalog. That is the only setup that can catch a
default that names a model the catalog doesn't have.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.adapters.factory import ProviderNotConfiguredError
from app.services.canvas.canvas_run_service import CanvasRunService


def _enabled_llm_row() -> dict:
    """One enabled llm row shaped like a real ``mediahub_models`` catalog
    entry (doubao-backed, platform credentials present)."""
    return {
        "name": "mediahub-doubao-llm",
        "display_name": "Doubao LLM",
        "type": "llm",
        "is_enabled": True,
        "actual_provider": "doubao",
        "actual_model": "doubao-pro-32k",
        "api_key": "platform-key",
        "base_url": "https://ark.example.com/v1",
        "app_id": None,
        "sort_order": 0,
    }


def _make_service() -> CanvasRunService:
    # Real _get_adapter / resolve — NOT mocked. Only settings is a stub.
    return CanvasRunService(settings=SimpleNamespace())


def _repo(*, name_hit: dict | None, enabled_llm: list[dict]) -> MagicMock:
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=name_hit)
    repo.get_by_actual_model = AsyncMock(return_value=None)
    repo.list_enabled = AsyncMock(return_value=enabled_llm)
    return repo


@pytest.mark.asyncio
async def test_hardcoded_qwen_plus_default_is_unconfigured():
    """The PRE-FIX default (``qwen-plus``) is not in the catalog, so the real
    resolve chain raises ProviderNotConfiguredError — i.e. clicking Run on a
    default text prompt was broken. This test pins that fact so the fix's new
    default can't silently regress back to a non-catalog name."""
    svc = _make_service()
    repo = _repo(name_hit=None, enabled_llm=[])
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with pytest.raises(ProviderNotConfiguredError):
            await svc._get_adapter("qwen-plus")


@pytest.mark.asyncio
async def test_default_text_model_is_first_enabled_llm_from_catalog():
    """Empty provider_slug must resolve to a model the catalog actually has —
    the first enabled ``llm`` row's ``name`` — not a hardcoded constant."""
    svc = _make_service()
    repo = _repo(name_hit=_enabled_llm_row(), enabled_llm=[_enabled_llm_row()])
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        model = await svc._default_text_model()
    assert model == "mediahub-doubao-llm"
    repo.list_enabled.assert_awaited_with("llm")


@pytest.mark.asyncio
async def test_catalog_llm_name_builds_a_real_adapter():
    """A catalog llm ``name`` resolves end-to-end through the REAL factory to a
    built adapter (no ProviderNotConfiguredError) — proving the DB path works
    without mocking resolve away."""
    svc = _make_service()
    repo = _repo(name_hit=_enabled_llm_row(), enabled_llm=[_enabled_llm_row()])
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            adapter = await svc._get_adapter("mediahub-doubao-llm")
    assert adapter is not None
