from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


def _audio_resource(**over) -> dict:
    base = {"id": "1", "source_type": "upload", "mime_type": "audio/mpeg"}
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_chorus_set_persists():
    from app.api import resources_crud_router as r
    from app.schemas.resources import ChorusUpdate

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access", AsyncMock(return_value=True)
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(return_value=_audio_resource())
        Repo.return_value.update_resource = AsyncMock(return_value={"id": "1"})
        auth = MagicMock(user_id="u")
        out = await r.set_resource_chorus(
            "1", ChorusUpdate(chorus_start_ms=42000), auth, None
        )
        assert out["success"] is True
        Repo.return_value.update_resource.assert_awaited_once_with(
            "1", {"chorus_start_ms": 42000}
        )


@pytest.mark.asyncio
async def test_chorus_clear_persists_none():
    from app.api import resources_crud_router as r
    from app.schemas.resources import ChorusUpdate

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access", AsyncMock(return_value=True)
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(return_value=_audio_resource())
        Repo.return_value.update_resource = AsyncMock(return_value={"id": "1"})
        auth = MagicMock(user_id="u")
        out = await r.set_resource_chorus(
            "1", ChorusUpdate(chorus_start_ms=None), auth, None
        )
        assert out["success"] is True
        Repo.return_value.update_resource.assert_awaited_once_with(
            "1", {"chorus_start_ms": None}
        )


@pytest.mark.asyncio
async def test_chorus_denies_non_owner():
    from app.api import resources_crud_router as r
    from app.schemas.resources import ChorusUpdate

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access",
            AsyncMock(return_value=False),
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(return_value=_audio_resource())
        Repo.return_value.update_resource = AsyncMock()
        auth = MagicMock(user_id="u")
        with pytest.raises(HTTPException) as ei:
            await r.set_resource_chorus(
                "1", ChorusUpdate(chorus_start_ms=1000), auth, None
            )
        assert ei.value.status_code == 403
        Repo.return_value.update_resource.assert_not_called()


@pytest.mark.asyncio
async def test_chorus_rejects_non_upload():
    from app.api import resources_crud_router as r
    from app.schemas.resources import ChorusUpdate

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access", AsyncMock(return_value=True)
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(
            return_value=_audio_resource(source_type="web")
        )
        Repo.return_value.update_resource = AsyncMock()
        auth = MagicMock(user_id="u")
        with pytest.raises(HTTPException) as ei:
            await r.set_resource_chorus(
                "1", ChorusUpdate(chorus_start_ms=1000), auth, None
            )
        assert ei.value.status_code == 400
        Repo.return_value.update_resource.assert_not_called()


@pytest.mark.asyncio
async def test_chorus_rejects_non_audio():
    from app.api import resources_crud_router as r
    from app.schemas.resources import ChorusUpdate

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access", AsyncMock(return_value=True)
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(
            return_value=_audio_resource(mime_type="video/mp4")
        )
        Repo.return_value.update_resource = AsyncMock()
        auth = MagicMock(user_id="u")
        with pytest.raises(HTTPException) as ei:
            await r.set_resource_chorus(
                "1", ChorusUpdate(chorus_start_ms=1000), auth, None
            )
        assert ei.value.status_code == 400
        Repo.return_value.update_resource.assert_not_called()


@pytest.mark.asyncio
async def test_chorus_rejects_negative_ms():
    """Negative ms fails Pydantic validation (ge=0 -> 422); endpoint/repo never run."""
    from app.api import resources_crud_router as r
    from app.schemas.resources import ChorusUpdate

    with patch.object(r, "ResourcesRepository") as Repo:
        Repo.return_value.update_resource = AsyncMock()
        with pytest.raises(ValidationError):
            ChorusUpdate(chorus_start_ms=-5)
        Repo.return_value.update_resource.assert_not_called()
