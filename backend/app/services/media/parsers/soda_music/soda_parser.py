# backend/app/services/media/parsers/soda_music/soda_parser.py
"""Orchestrate a single Soda track: resolve → parsed_data + download plan.

Pure orchestration over the injected SodaApiClient + formatter. No DB, no disk.
The returned SodaDownloadPlan carries everything the downloader needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.media.parsers.soda_music.formatter import format_track
from app.services.media.parsers.soda_music.soda_quality import play_url


@dataclass(frozen=True)
class SodaDownloadPlan:
    track_id: str
    url: str  # MainPlayUrl (or BackupPlayUrl)
    play_auth: str  # decryption-key carrier
    ext: str  # flac / m4a / mp3


async def parse_track(
    api: Any, *, track_id: str, want_quality: str, original_url: str
) -> tuple[dict[str, Any], SodaDownloadPlan]:
    """Resolve a track to (parsed_data, download_plan).

    Raises whatever SodaApiClient raises (SodaApiError / SodaPreviewError) —
    callers convert those into task failures.
    """
    track, chosen = await api.get_track_with_play_info(track_id, want_quality)
    parsed_data = format_track(track, chosen, original_url=original_url)
    plan = SodaDownloadPlan(
        track_id=str(track.get("id")),
        url=play_url(chosen),
        play_auth=chosen.get("PlayAuth") or chosen.get("play_auth") or "",
        ext=parsed_data["metadata"]["ext"],
    )
    return parsed_data, plan
