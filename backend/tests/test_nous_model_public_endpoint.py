# backend/tests/test_nous_public_endpoint.py
"""Public nous-models endpoint honors ?type=; governance exposes nous gates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


def _patched_view(repo):
    """Governance on, no stored card, the given catalog repo."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.repositories.nous_model_repository.get_nous_model_repository",
            return_value=repo,
        )
    )
    stack.enter_context(
        patch(
            "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
            new=AsyncMock(return_value=True),
        )
    )
    stack.enter_context(
        patch(
            "app.services.ai.platform_model_visibility.stored_nous_settings",
            new=AsyncMock(return_value={}),
        )
    )
    return stack


def _full_row(name: str, type_: str) -> dict:
    return {
        "id": 7300000000000000123,
        "name": name,
        "display_name": name,
        "actual_model": name,
        "actual_provider": "ark",
        "type": type_,
        "pricing_type": "per_token",
        "pricing_value": 0,
        "sort_order": 0,
        "last_test_status": "ok",
        "last_tested_at": None,
        "last_test_code": None,
        "context_window_tokens": None,
        "api_key": "sk-secret",
        "base_url": "https://ark.example",
    }


@pytest.mark.asyncio
async def test_list_nous_models_passes_type_filter():
    from types import SimpleNamespace

    from app.api.ai_settings_router import list_nous_models

    repo = MagicMock()
    repo.list_enabled_private = AsyncMock(
        return_value=[_full_row("nous-llm", "llm"), _full_row("nous-pic", "image")]
    )
    with _patched_view(repo):
        result = await list_nous_models(
            auth=SimpleNamespace(user_id="viewer-1"), type="llm"
        )
    repo.list_enabled_private.assert_awaited_once_with("viewer-1")
    assert [m["name"] for m in result["models"]] == ["nous-llm"]
    assert "api_key" not in result["models"][0]
    assert "base_url" not in result["models"][0]


def test_public_projection_excludes_admin_description():
    """The public list_enabled projection must never expose ``description`` —
    it carries admin-internal ops notes (private ZeroTier IPs, BYOK source refs)
    that would leak into the user-facing platform-models card."""
    from app.repositories.nous_model_repository import _PUBLIC_COLS

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
    from app.repositories.nous_model_repository import _PUBLIC_COLS

    col_names = {c.key for c in _PUBLIC_COLS}
    forbidden = {
        "api_key",
        "app_id",
        "base_url",
        "description",
        "last_test_detail",
        "actual_provider",
    }
    assert col_names & forbidden == set()


def test_public_projection_exposes_actual_model_for_admin_name_parity():
    """2026-09-24: the admin AI Models card names each row by its
    ``actual_model`` (``doubao-embedding-vision-251215``) while the user side
    showed ``display_name`` (``Doubao Embedding (Vision)``), so the two surfaces
    could not be matched by eye. ``actual_model`` is a model id — no host, key
    or account secret — and is now public so both sides print the same string.
    ``actual_provider`` (upstream identity) stays out; see the test above."""
    from app.repositories.nous_model_repository import _PUBLIC_COLS

    col_names = {c.key for c in _PUBLIC_COLS}
    assert "actual_model" in col_names
    assert "actual_provider" not in col_names


def test_public_projection_exposes_health_status_and_time():
    """F2: the user-facing list must carry the model's health, otherwise the
    frontend cannot warn anyone — before this the columns simply weren't in the
    payload, so 'the UI doesn't show it' was really 'the UI can't know'.
    ``last_tested_at`` rides along because a stale green light must not read as
    'currently fine'."""
    from app.repositories.nous_model_repository import _PUBLIC_COLS

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
    from app.repositories.nous_model_repository import _PUBLIC_COLS

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


@pytest.mark.asyncio
async def test_list_nous_models_passes_viewer_from_auth():
    """The catalog endpoint must scope the list to the calling user so
    owner-private rows (codex/jimeng, migration 431) never leak to others."""
    from types import SimpleNamespace

    from app.api.ai_settings_router import list_nous_models

    repo = MagicMock()
    repo.list_enabled_private = AsyncMock(return_value=[])
    with _patched_view(repo):
        await list_nous_models(auth=SimpleNamespace(user_id="viewer-1"), type="image")
    repo.list_enabled_private.assert_awaited_once_with("viewer-1")


@pytest.mark.parametrize("viewer", [None, "00000000-0000-0000-0000-000000000042"])
@pytest.mark.asyncio
async def test_list_enabled_private_keeps_owner_scoping(monkeypatch, viewer):
    """The credential-bearing read behind the platform view scopes rows the
    same way ``list_enabled`` does: another user's private row never enters
    anyone else's view (no viewer → platform rows only)."""
    from contextlib import asynccontextmanager

    import app.repositories.nous_model_repository as repo_mod

    seen: list = []

    class _Session:
        async def execute(self, stmt):
            seen.append(stmt)
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            return result

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    await repo_mod.NousModelRepository().list_enabled_private(viewer)
    compiled = seen[0].compile()
    sql = str(compiled)
    assert "nous_models.is_enabled IS true" in sql
    assert "nous_models.owner_user_id IS NULL" in sql
    if viewer:
        assert "nous_models.owner_user_id = :owner_user_id_1" in sql
        assert compiled.params["owner_user_id_1"] == viewer
    else:
        assert "owner_user_id =" not in sql
