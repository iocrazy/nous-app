# backend/tests/test_nous_public_endpoint.py
"""Public mediahub-models endpoint honors ?type=; governance exposes nous gates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


@pytest.mark.asyncio
async def test_list_mediahub_models_passes_type_filter():
    from app.api.ai_settings_router import list_mediahub_models

    repo = MagicMock()
    repo.list_enabled = AsyncMock(return_value=[{"name": "nous-llm", "type": "llm"}])
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        result = await list_mediahub_models(type="llm")
    repo.list_enabled.assert_awaited_once_with("llm")
    assert result == {"models": [{"name": "nous-llm", "type": "llm"}]}


def test_public_projection_excludes_admin_description():
    """The public list_enabled projection must never expose ``description`` —
    it carries admin-internal ops notes (private ZeroTier IPs, BYOK source refs)
    that would leak into the user-facing platform-models card."""
    from app.repositories.mediahub_model_repository import _PUBLIC_COLS

    col_names = {c.key for c in _PUBLIC_COLS}
    assert "description" not in col_names
    assert "api_key" not in col_names
    assert "base_url" not in col_names


def test_public_projection_never_exposes_credentials():
    """Guard rail for the F2 additions (2026-08-14): the projection now carries
    health columns, and the temptation next time will be to 'just add' whatever
    the UI wants. This list is the tripwire — every credential-bearing column
    stays out, including ``last_test_detail`` (a probe failure text routinely
    embeds the private base_url / upstream host, the same leak class that got
    ``description`` excluded)."""
    from app.repositories.mediahub_model_repository import _PUBLIC_COLS

    col_names = {c.key for c in _PUBLIC_COLS}
    forbidden = {
        "api_key",
        "app_id",
        "base_url",
        "description",
        "last_test_detail",
        "actual_provider",
        "actual_model",
    }
    assert col_names & forbidden == set()


def test_public_projection_exposes_health_status_and_time():
    """F2: the user-facing list must carry the model's health, otherwise the
    frontend cannot warn anyone — before this the columns simply weren't in the
    payload, so 'the UI doesn't show it' was really 'the UI can't know'.
    ``last_tested_at`` rides along because a stale green light must not read as
    'currently fine'."""
    from app.repositories.mediahub_model_repository import _PUBLIC_COLS

    col_names = {c.key for c in _PUBLIC_COLS}
    assert "last_test_status" in col_names
    assert "last_tested_at" in col_names


def test_public_projection_exposes_the_reason_code_but_not_the_reason_text():
    """The whole point of the closed enum (migration 427): the failure REASON
    reaches users while the failure TEXT never does.

    ``last_test_code`` is derived from the exception type and HTTP status alone,
    so 'timed out' (wait) and 'rate limited' (go fix quota) become tellable
    apart — under #1838 both rendered as the same bare red light. Its sibling
    ``last_test_detail`` stays out for the reason asserted above: on 2026-08-14
    it held a URL fragment.
    """
    from app.repositories.mediahub_model_repository import _PUBLIC_COLS

    col_names = {c.key for c in _PUBLIC_COLS}
    assert "last_test_code" in col_names
    assert "last_test_detail" not in col_names


@pytest.mark.asyncio
async def test_governance_includes_nous_enabled_and_modules():
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
            new=AsyncMock(return_value=True),
        ):
            with patch(
                "app.services.ai.governance.ai_governance.is_nous_allowed",
                new=AsyncMock(return_value=True),
            ):
                fake_auth = MagicMock()
                result = await get_ai_governance(fake_auth)

    assert result["nous_enabled"] is True
    assert isinstance(result["nous_modules"], dict)
    assert result["nous_modules"]["transcription"] is True
