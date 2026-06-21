# backend/tests/test_admin_nous_create_inherit.py
"""Provider-card UX: creating a model with a blank API key inherits the key
from an existing sibling on the same provider+base_url."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.schemas.nous import NousModelCreate


def _body(**over):
    base = dict(
        name="nous-qwen3-embed",
        display_name="Embed",
        type="embedding",
        actual_provider="openai",
        actual_model="qwen3-embedding-8b",
        api_key="",  # blank → inherit
        base_url="http://10.0.0.10:8000/v1",
    )
    base.update(over)
    return NousModelCreate(**base)


@pytest.mark.asyncio
async def test_create_inherits_sibling_key_when_blank():
    from app.api.admin.nous_router import create_nous_model

    repo = MagicMock()
    repo.list_all = AsyncMock(
        return_value=[
            {
                "actual_provider": "openai",
                "base_url": "http://10.0.0.10:8000/v1",
                "api_key": "stored-key",
                "app_id": "sib-app",
            }
        ]
    )
    created = {}

    async def _create(data):
        created.update(data)
        return {"id": 1, "pricing_value": 0, **data}

    repo.create = AsyncMock(side_effect=_create)
    fake_auth = MagicMock()
    fake_auth.user_id = "a"

    with patch("app.api.admin.nous_router.get_nous_repository", return_value=repo):
        await create_nous_model(_body(), fake_auth)

    assert created["api_key"] == "stored-key"
    assert created["app_id"] == "sib-app"


@pytest.mark.asyncio
async def test_create_400_when_blank_key_and_no_sibling():
    from app.api.admin.nous_router import create_nous_model

    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[])  # no sibling to inherit from
    repo.create = AsyncMock()
    fake_auth = MagicMock()
    fake_auth.user_id = "a"

    with patch("app.api.admin.nous_router.get_nous_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await create_nous_model(_body(), fake_auth)

    assert exc.value.status_code == 400
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_create_uses_explicit_key_without_lookup():
    from app.api.admin.nous_router import create_nous_model

    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[])
    created = {}

    async def _create(data):
        created.update(data)
        return {"id": 1, "pricing_value": 0, **data}

    repo.create = AsyncMock(side_effect=_create)
    fake_auth = MagicMock()
    fake_auth.user_id = "a"

    with patch("app.api.admin.nous_router.get_nous_repository", return_value=repo):
        await create_nous_model(_body(api_key="explicit-key"), fake_auth)

    assert created["api_key"] == "explicit-key"
    repo.list_all.assert_not_called()  # explicit key → no sibling lookup
