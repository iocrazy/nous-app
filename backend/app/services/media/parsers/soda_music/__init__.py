"""Soda (汽水音乐 / Luna) music parser — phase 1 core (parse + decrypt, no DB).

Public surface:
  - soda_api:     SodaApiClient + signable param/header builders (§A.2/A.3)
  - soda_decrypt: extract_spade_key / decrypt_audio (§A.5)
  - soda_quality: select_play_info / is_preview / quality_rank (§A.4)

See ``docs/soda-music-integration.md``.
"""

from app.services.media.parsers.soda_music.soda_api import (
    BASE_URL,
    SodaApiClient,
    SodaApiError,
    SodaContent,
    SodaPreviewError,
    build_pc_headers,
    build_pc_params,
    classify_landing_url,
    cover_url,
)
from app.services.media.parsers.soda_music.soda_decrypt import (
    SodaDecryptError,
    decrypt_audio,
    extract_spade_key,
)
from app.services.media.parsers.soda_music.soda_quality import (
    is_preview,
    normalize_duration_seconds,
    play_url,
    quality_rank,
    select_play_info,
)

__all__ = [
    # api
    "BASE_URL",
    "SodaApiClient",
    "SodaApiError",
    "SodaContent",
    "SodaPreviewError",
    "build_pc_headers",
    "build_pc_params",
    "classify_landing_url",
    "cover_url",
    # decrypt
    "SodaDecryptError",
    "decrypt_audio",
    "extract_spade_key",
    # quality
    "is_preview",
    "normalize_duration_seconds",
    "play_url",
    "quality_rank",
    "select_play_info",
]
