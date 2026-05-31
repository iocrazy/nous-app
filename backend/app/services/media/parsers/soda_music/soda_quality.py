"""Soda (汽水音乐) audio quality selection + preview (试听) detection.

Pure functions over the ``PlayInfoList`` entries returned by the player-info
endpoint (§A.3 / §A.4). Two response shapes exist in the wild — the player-info
GET returns PascalCase keys (``Quality``/``Bitrate``/``MainPlayUrl``) while the
embedded ``track_v2`` audio_info uses snake_case (``main_play_url``). Accessors
here tolerate both.

Selection rule (§A.4): exact ``Quality`` match, else the highest ``Bitrate``.
Preview rule (§A.4): a stream whose duration + 5s is still shorter than the
track is a 30s 试听 — callers must treat that as a failure, never silent success.
"""

from __future__ import annotations

from typing import Any

PREVIEW_TOLERANCE_SECONDS = 5.0
MILLISECONDS_THRESHOLD = 1000  # duration values above this are treated as ms


def _get(info: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """First present (non-None) value among ``keys`` — tolerates key casing."""
    for key in keys:
        if key in info and info[key] is not None:
            return info[key]
    return default


def _normalize_quality_label(quality: str) -> str:
    return quality.strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def play_url(info: dict[str, Any]) -> str:
    """Download URL for a PlayInfoList entry: MainPlayUrl, else BackupPlayUrl.

    Raises:
        ValueError: if neither a main nor backup URL is present.
    """
    main = _get(info, "MainPlayUrl", "main_play_url", default="")
    if main:
        return main
    backup = _get(info, "BackupPlayUrl", "backup_play_url", default="")
    if backup:
        return backup
    raise ValueError("PlayInfoList entry has neither MainPlayUrl nor BackupPlayUrl")


def normalize_duration_seconds(raw: float | int) -> int:
    """Coerce a duration to whole seconds (values > 1000 are treated as ms)."""
    value = float(raw)
    if value > MILLISECONDS_THRESHOLD:
        return int(value / 1000)
    return int(value)


def is_preview(
    stream_duration_seconds: float,
    full_duration_seconds: float,
    tolerance: float = PREVIEW_TOLERANCE_SECONDS,
) -> bool:
    """True when a stream is a truncated preview (试听) of the full track.

    Returns False when either duration is unknown (≤0) — absence of evidence is
    not evidence of a preview; the caller decides how to handle unknowns.
    """
    if stream_duration_seconds <= 0 or full_duration_seconds <= 0:
        return False
    return stream_duration_seconds + tolerance < full_duration_seconds


def quality_rank(quality: str, fmt: str, bitrate: int) -> int:
    """Rank a stream's quality (higher = better). Ported from sodaQualityRank.

    Label-based tiers first (lossless > hi-res > spatial > highest > higher >
    standard), falling back to bitrate buckets when the label is unrecognised.
    """
    q = _normalize_quality_label(quality)
    f = fmt.strip().lower()
    br = bitrate or 0
    is_lossless_format = any(t in f for t in ("flac", "alac", "wav"))
    is_lossless_label = any(t in q for t in ("lossless", "flac", "sq", "svip"))
    is_hires_label = "hires" in q or "master" in q

    if is_hires_label and (is_lossless_format or br >= 900):
        return 110
    if is_lossless_label or is_lossless_format or br >= 900:
        return 100
    if is_hires_label:
        return 90
    if any(t in q for t in ("atmos", "dolby", "spatial")):
        return 88
    if any(t in q for t in ("highest", "excellent", "superhigh", "hq")):
        return 80
    if "higher" in q or q == "high" or "320" in q:
        return 70
    if any(t in q for t in ("standard", "medium", "normal", "128")):
        return 50
    if "low" in q or "preview" in q:
        return 10

    # No usable label — bucket by bitrate.
    if br >= 900:
        return 100
    if br >= 320:
        return 70
    if br >= 256:
        return 65
    if br >= 192:
        return 55
    if br >= 128:
        return 50
    if br > 0:
        return 20
    return 0


def select_play_info(
    play_info_list: list[dict[str, Any]],
    want_quality: str,
) -> dict[str, Any] | None:
    """Pick the desired quality stream (§A.4).

    Exact ``Quality`` match (case/separator-insensitive); otherwise the entry
    with the highest ``Bitrate``. Returns None for an empty list.
    """
    if not play_info_list:
        return None
    target = _normalize_quality_label(want_quality)
    for info in play_info_list:
        label = _normalize_quality_label(
            str(_get(info, "Quality", "quality", default=""))
        )
        if label and label == target:
            return info
    return max(
        play_info_list,
        key=lambda info: int(_get(info, "Bitrate", "bitrate", default=0) or 0),
    )
