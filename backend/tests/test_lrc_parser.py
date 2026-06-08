from app.services.lrc_parser import parse_lrc


def test_parses_timestamped_lines():
    lrc = "[00:12.50]Hello world\n[01:05.00]Second line"
    out = parse_lrc(lrc)
    assert out["lrc"] == lrc
    assert out["lines"] == [
        {"text": "Hello world", "line_start_ms": 12500},
        {"text": "Second line", "line_start_ms": 65000},
    ]


def test_skips_metadata_tags_and_blank_lines():
    lrc = "[ar:Artist]\n[ti:Title]\n\n[00:01.00]Only line"
    out = parse_lrc(lrc)
    assert out["lines"] == [{"text": "Only line", "line_start_ms": 1000}]


def test_multi_timestamp_line_expands():
    lrc = "[00:01.00][00:10.00]Repeat"
    out = parse_lrc(lrc)
    assert out["lines"] == [
        {"text": "Repeat", "line_start_ms": 1000},
        {"text": "Repeat", "line_start_ms": 10000},
    ]


def test_sorts_by_time_and_handles_3digit_ms():
    lrc = "[00:10.000]B\n[00:02.500]A"
    out = parse_lrc(lrc)
    assert [ln["line_start_ms"] for ln in out["lines"]] == [2500, 10000]


def test_plain_text_without_timestamps_becomes_untimed_lines():
    lrc = "line one\nline two"
    out = parse_lrc(lrc)
    assert out["lines"] == [
        {"text": "line one", "line_start_ms": None},
        {"text": "line two", "line_start_ms": None},
    ]


def test_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_lrc("   \n  ")
