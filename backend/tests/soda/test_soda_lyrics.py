from app.services.media.parsers.soda_music.lyrics import parse_timed_lyrics, to_lrc

SAMPLE = "[1000,2000]<0,500,0>Hello<500,500,0> World\n[3000,1000]<0,1000,0>Bye"


def test_parse_timed_lyrics_lines():
    lines = parse_timed_lyrics(SAMPLE)
    assert len(lines) == 2
    assert lines[0]["text"] == "Hello World"
    assert lines[0]["line_start_ms"] == 1000
    assert lines[1]["text"] == "Bye"


def test_to_lrc_format():
    lrc = to_lrc(parse_timed_lyrics(SAMPLE))
    assert lrc.splitlines()[0].startswith("[00:01.00]Hello World")
    assert lrc.splitlines()[1].startswith("[00:03.00]Bye")


def test_parse_empty_or_null():
    assert parse_timed_lyrics("") == []
    assert parse_timed_lyrics("NULL") == []
    assert parse_timed_lyrics("null") == []
    assert to_lrc([]) == ""
