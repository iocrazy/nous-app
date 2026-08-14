# backend/tests/test_admin_nous_probe.py
"""Admin nous probe-models endpoint: self-check a provider key → list models."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.mediahub_model import MediahubModelProbeRequest


@pytest.mark.asyncio
async def test_probe_models_returns_provider_models():
    """probe_mediahub_models forwards provider/key/base_url to test_connection and
    returns the fetched model list (so admin selects instead of hand-typing)."""
    from app.api.admin.mediahub_model_router import probe_mediahub_models

    body = MediahubModelProbeRequest(
        provider_key="deepseek",
        api_key="sk-x",
        base_url="https://api.deepseek.com",
    )
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    captured = {}

    async def _fake_test_connection(*, provider_key, config):
        captured["provider_key"] = provider_key
        captured["config"] = config
        return {
            "success": True,
            "models": ["deepseek-chat", "deepseek-reasoner"],
            "error": None,
        }

    with patch(
        "app.api.admin.mediahub_model_router.AIProviderFactory.test_connection",
        new=AsyncMock(side_effect=_fake_test_connection),
    ):
        resp = await probe_mediahub_models(body, fake_auth)

    assert resp.success is True
    assert "deepseek-chat" in resp.models
    assert captured["provider_key"] == "deepseek"
    assert captured["config"]["api_key"] == "sk-x"
    assert captured["config"]["base_url"] == "https://api.deepseek.com"


@pytest.mark.asyncio
async def test_probe_models_surfaces_failure():
    """A provider that can't list models (e.g. volcengine ASR) returns
    success=False + error; the endpoint passes it through (UI falls back to
    manual entry)."""
    from app.api.admin.mediahub_model_router import probe_mediahub_models

    body = MediahubModelProbeRequest(provider_key="volcengine", api_key="k", app_id="a")
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    with patch(
        "app.api.admin.mediahub_model_router.AIProviderFactory.test_connection",
        new=AsyncMock(
            return_value={"success": False, "models": None, "error": "no /models"}
        ),
    ):
        resp = await probe_mediahub_models(body, fake_auth)

    assert resp.success is False
    assert resp.models is None
    assert resp.error == "no /models"


@pytest.mark.asyncio
async def test_probe_blank_key_falls_back_to_stored_key():
    """Edit form sends a blank api_key (stored key never leaves the server).
    When ``name`` references an existing model, the probe reuses its stored
    key + app_id — so 'Test & Load Models' works without re-typing the key."""
    from app.api.admin.mediahub_model_router import probe_mediahub_models

    body = MediahubModelProbeRequest(
        provider_key="openai",
        api_key="",  # blank — edit form doesn't carry the stored key
        base_url="http://10.0.0.10:8000/v1",
        name="nous-qwen3-llm",
    )
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    repo = MagicMock()
    repo.get_by_name = AsyncMock(
        return_value={"api_key": "stored-secret", "app_id": "stored-app"}
    )

    captured = {}

    async def _fake_test_connection(*, provider_key, config):
        captured["config"] = config
        return {"success": True, "models": ["qwen3-6-35b"], "error": None}

    with patch(
        "app.api.admin.mediahub_model_router.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.mediahub_model_router.AIProviderFactory.test_connection",
            new=AsyncMock(side_effect=_fake_test_connection),
        ):
            resp = await probe_mediahub_models(body, fake_auth)

    assert resp.success is True
    assert captured["config"]["api_key"] == "stored-secret"
    assert captured["config"]["app_id"] == "stored-app"
    repo.get_by_name.assert_awaited_once_with("nous-qwen3-llm")


@pytest.mark.asyncio
async def test_test_endpoint_persists_result():
    """POST /{id}/test persists the probe result (status/detail/tested_at) so the
    admin's status dot survives navigation, and echoes tested_at back."""
    from app.api.admin.mediahub_model_router import test_mediahub_model

    row = {"id": "42", "type": "llm", "actual_model": "deepseek-chat"}
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[row])
    repo.record_test_result = AsyncMock(
        return_value={"last_tested_at": "2026-06-25T03:00:00+00:00"}
    )

    with patch(
        "app.api.admin.mediahub_model_router.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.mediahub_model_router._probe_mediahub_model",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "detail": "chat ok",
                    "error": None,
                    "dims": None,
                    "code": None,
                }
            ),
        ):
            resp = await test_mediahub_model("42", MagicMock())

    assert resp.ok is True
    assert resp.tested_at == "2026-06-25T03:00:00+00:00"
    # code=None on a pass is not a formality: it is what erases a previous
    # failure's reason code from the row (migration 427).
    repo.record_test_result.assert_awaited_once_with("42", "ok", "chat ok", None)


@pytest.mark.asyncio
async def test_test_endpoint_persists_failure_detail():
    """On failure the error text is persisted as the detail (status='fail'),
    together with the probe's closed-enum reason code — the manual Test path
    must reach the DB with the same shape the hourly poll does, or a model
    someone just tested by hand would show a reason-less red light."""
    from app.api.admin.mediahub_model_router import test_mediahub_model

    row = {"id": "7", "type": "embedding", "actual_model": "bad-embed"}
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[row])
    repo.record_test_result = AsyncMock(
        return_value={"last_tested_at": "2026-06-25T03:01:00+00:00"}
    )

    with patch(
        "app.api.admin.mediahub_model_router.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.mediahub_model_router._probe_mediahub_model",
            new=AsyncMock(
                return_value={
                    "ok": False,
                    "detail": "",
                    "error": "HTTP 401: bad key",
                    "dims": None,
                    "code": "auth",
                }
            ),
        ):
            resp = await test_mediahub_model("7", MagicMock())

    assert resp.ok is False
    repo.record_test_result.assert_awaited_once_with(
        "7", "fail", "HTTP 401: bad key", "auth"
    )


@pytest.mark.asyncio
async def test_test_endpoint_persists_not_probed_not_fail():
    """Clicking Test on an image/video model must not repaint it red.

    The hourly poll and this endpoint are two writers of the same column, and
    the whole point of ``not_probed`` is lost if one of them still writes
    ``fail`` — an admin pressing "Test" on a provider card would undo the fix
    for every unprobeable model on it. The flag is echoed back too, so the page
    can show the neutral badge without waiting for a list refetch.
    """
    from app.api.admin.mediahub_model_router import test_mediahub_model

    row = {"id": "11", "type": "image", "actual_model": "jimeng-4.0"}
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[row])
    repo.record_test_result = AsyncMock(
        return_value={"last_tested_at": "2026-08-14T03:00:00+00:00"}
    )

    with patch(
        "app.api.admin.mediahub_model_router.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.mediahub_model_router._probe_mediahub_model",
            new=AsyncMock(
                return_value={
                    "ok": False,
                    "not_probed": True,
                    "detail": "no protocol probe for type=image",
                    "error": None,
                    "dims": None,
                    "code": None,
                }
            ),
        ):
            resp = await test_mediahub_model("11", MagicMock())

    assert resp.ok is False
    assert resp.not_probed is True
    repo.record_test_result.assert_awaited_once_with(
        "11", "not_probed", "no protocol probe for type=image", None
    )


@pytest.mark.asyncio
async def test_test_endpoint_defaults_not_probed_false():
    """An ordinary probe result carries no such flag; the response must still
    answer the question, with False rather than a missing key."""
    from app.api.admin.mediahub_model_router import test_mediahub_model

    repo = MagicMock()
    repo.list_all = AsyncMock(
        return_value=[{"id": "3", "type": "llm", "actual_model": "deepseek-chat"}]
    )
    repo.record_test_result = AsyncMock(return_value={})

    with patch(
        "app.api.admin.mediahub_model_router.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.mediahub_model_router._probe_mediahub_model",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "detail": "chat ok",
                    "error": None,
                    "dims": None,
                    "code": None,
                }
            ),
        ):
            resp = await test_mediahub_model("3", MagicMock())

    assert resp.not_probed is False
