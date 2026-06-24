"""ai_transcription steps hit the SQLAlchemy engine with correct SQL/params after
the psycopg→engine migration."""

from __future__ import annotations

from unittest.mock import patch

import pytest


async def test_load_transcribe_inputs_raises_when_no_media():
    import app.workflows.ai_transcription as m

    async def fake_fetch_one(sql, params=None):
        return None

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        with pytest.raises(RuntimeError, match="no parsed_media"):
            await m.load_transcribe_inputs(1, "u")


async def test_load_transcribe_inputs_raises_when_no_audio_path():
    import app.workflows.ai_transcription as m

    async def fake_fetch_one(sql, params=None):
        # media row exists but has neither extract_audio_path nor download_path
        return {
            "id": 1,
            "download_path": None,
            "extract_audio_path": None,
            "platform_id": "p",
            "resource_id": 5,
        }

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        with pytest.raises(RuntimeError, match="no audio_path"):
            await m.load_transcribe_inputs(1, "u")


async def test_mark_transcript_completed_uses_media_id_column():
    import app.workflows.ai_transcription as m

    cap = {}

    async def fake_execute(sql, params=None):
        cap["sql"] = sql
        cap["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await m.mark_transcript_completed(42)

    # Must key on media_id, NOT id — wrong column silently updates 0 rows.
    assert "WHERE media_id = :pid" in cap["sql"]
    assert "transcript_status = 'completed'" in cap["sql"]
    assert cap["params"] == {"pid": 42}


@pytest.mark.asyncio
async def test_load_transcribe_inputs_locked_to_catalog_model():
    """Locked TO a platform-catalog model → provider/key/app_id come from the
    catalog (ungated); blank manual api_key must NOT fail-closed."""
    from unittest.mock import AsyncMock

    import app.workflows.ai_transcription as m
    from app.services.ai.governance.ai_governance import AIModuleGovernance

    media_row = {
        "id": 1,
        "download_path": "/tmp/audio.mp3",
        "extract_audio_path": None,
        "platform_id": "p",
        "resource_id": 5,
    }
    gov = AIModuleGovernance(
        allowed=False, base_url="", model="mediahub-volc-asr", api_key=""
    )
    with (
        patch("app.db.engine.fetch_one", AsyncMock(return_value=media_row)),
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=gov),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(
                return_value=(
                    "volcengine",
                    {
                        "api_key": "cat-key",
                        "base_url": "",
                        "model": "seed-asr",
                        "app_id": "the-app-id",
                    },
                    "seed-asr",
                )
            ),
        ),
    ):
        out = await m.load_transcribe_inputs(1, "u")

    assert out["provider_key"] == "volcengine"
    assert out["provider_config"]["api_key"] == "cat-key"
    assert out["provider_config"]["app_id"] == "the-app-id"
