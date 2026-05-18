"""Load persisted transcode settings from `system_settings` at startup.

Mirrors the inline block that used to live at the top of `main.lifespan`.
Failure is non-fatal — keeps the in-memory defaults from `app.core.config`.
"""

from loguru import logger

from app.core.config import settings

_TRANSCODE_KEY_MAP = {
    "transcode_enabled": "TRANSCODE_ENABLED",
    "transcode_tiers": "TRANSCODE_TIERS",
    "transcode_encoder": "FFMPEG_ENCODER",
    "transcode_preset": "FFMPEG_PRESET",
    "transcode_parallel_tiers": "TRANSCODE_PARALLEL_TIERS",
    "transcode_min_size_mb": "TRANSCODE_MIN_SIZE_MB",
}


async def load_persisted_transcode_settings() -> None:
    try:
        from app.db import get_async_supabase_admin

        supabase = await get_async_supabase_admin()
        result = await (
            supabase.table("system_settings")
            .select("key, value")
            .like("key", "transcode_%")
            .execute()
        )
        db_map = {row["key"]: row["value"] for row in (result.data or [])}
        for db_key, settings_attr in _TRANSCODE_KEY_MAP.items():
            if db_key in db_map:
                setattr(settings, settings_attr, db_map[db_key])
        logger.info(
            f"Transcode config loaded from DB: enabled={settings.TRANSCODE_ENABLED}, "
            f"tiers={settings.TRANSCODE_TIERS}, encoder={settings.FFMPEG_ENCODER}, "
            f"min_size_mb={settings.TRANSCODE_MIN_SIZE_MB}"
        )
    except Exception as e:
        logger.warning(f"Failed to load transcode config from database: {e}")
