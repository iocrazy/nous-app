"""Tests for soda_music.soda_quality — quality selection + preview detection."""

from __future__ import annotations

import pytest

from app.services.media.parsers.soda_music.soda_quality import (
    PREVIEW_TOLERANCE_SECONDS,
    is_preview,
    normalize_duration_seconds,
    play_url,
    quality_rank,
    select_play_info,
)

LOSSLESS = {
    "Quality": "lossless",
    "Format": "flac",
    "Bitrate": 729,
    "Duration": 200,
    "MainPlayUrl": "https://cdn/flac",
    "PlayAuth": "auth-flac",
}
HI_RES = {
    "Quality": "hi_res",
    "Format": "mp4",
    "Bitrate": 325,
    "Duration": 200,
    "MainPlayUrl": "https://cdn/hires",
    "PlayAuth": "auth-hires",
}
MEDIUM = {
    "Quality": "medium",
    "Format": "mp4",
    "Bitrate": 68,
    "Duration": 200,
    "MainPlayUrl": "https://cdn/medium",
    "PlayAuth": "auth-medium",
}


# --- select_play_info --------------------------------------------------------


def test_select_play_info_exact_quality_match():
    chosen = select_play_info([MEDIUM, HI_RES, LOSSLESS], want_quality="hi_res")
    assert chosen is HI_RES


def test_select_play_info_match_is_case_and_separator_insensitive():
    chosen = select_play_info([MEDIUM, HI_RES], want_quality="HI-RES")
    assert chosen is HI_RES


def test_select_play_info_falls_back_to_max_bitrate_when_no_match():
    chosen = select_play_info([MEDIUM, HI_RES, LOSSLESS], want_quality="spatial")
    assert chosen is LOSSLESS  # highest bitrate (729)


def test_select_play_info_empty_returns_none():
    assert select_play_info([], want_quality="hi_res") is None


# --- is_preview --------------------------------------------------------------


def test_is_preview_true_when_stream_much_shorter_than_track():
    # 30s preview of a 200s track
    assert is_preview(stream_duration_seconds=30, full_duration_seconds=200) is True


def test_is_preview_false_within_tolerance():
    assert is_preview(stream_duration_seconds=198, full_duration_seconds=200) is False


def test_is_preview_boundary_exactly_tolerance_is_not_preview():
    # stream + 5 == full  →  not strictly less  →  not a preview
    assert is_preview(195, 200, tolerance=PREVIEW_TOLERANCE_SECONDS) is False


def test_is_preview_false_when_durations_unknown():
    assert is_preview(0, 200) is False
    assert is_preview(30, 0) is False


# --- normalize_duration_seconds ---------------------------------------------


def test_normalize_duration_converts_milliseconds():
    assert normalize_duration_seconds(200000) == 200  # ms -> s


def test_normalize_duration_keeps_seconds():
    assert normalize_duration_seconds(200) == 200


# --- quality_rank ------------------------------------------------------------


def test_quality_rank_lossless_outranks_aac():
    assert quality_rank("lossless", "flac", 729) > quality_rank("highest", "mp4", 260)


def test_quality_rank_falls_back_to_bitrate_for_unknown_label():
    assert quality_rank("", "", 320) > quality_rank("", "", 128)


# --- play_url ----------------------------------------------------------------


def test_play_url_prefers_main():
    assert play_url(LOSSLESS) == "https://cdn/flac"


def test_play_url_falls_back_to_backup():
    info = {"MainPlayUrl": "", "BackupPlayUrl": "https://cdn/backup"}
    assert play_url(info) == "https://cdn/backup"


def test_play_url_supports_snake_case_keys():
    info = {"main_play_url": "https://cdn/snake"}
    assert play_url(info) == "https://cdn/snake"


def test_play_url_raises_when_no_url():
    with pytest.raises(ValueError):
        play_url({"Quality": "medium"})
