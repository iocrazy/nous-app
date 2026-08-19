"""Pairing the dialog's own search response back onto the rows it rendered.

What these tests are worth, and what they are not
================================================
The payload shape below is not invented. It is the 2026-08-19 capture of

    GET tsearch.amemv.com/openapi/aweme/v1/music/search/?keyword=海阔天空

taken through the recon path against a live logged-in creator account, with
the author string replaced — the ids, the key names, the 19-digit `id_str`,
and the fact that `duration` is an integer of seconds are all as measured. Per
CLAUDE.md's 「边界 mock 必须用真实 JSON 形状」 rule, a fixture that "tidied"
`id_str` into an int would be testing a wire format that does not exist.

What these tests cannot prove is that row *i* on the live page really is
`music[i]` — that is a claim about rendering that only a browser can settle.
What they DO prove is that the claim is never taken on faith: every test whose
name mentions alignment is written to go RED against an implementation that
assumes the order and clicks anyway, which is the failure this module exists to
prevent (clicking the row *next to* the right one is indistinguishable from
clicking the right one until the post is live).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.platforms.douyin_music_catalog import (
    ALIGN_NOT_CAPTURED,
    ALIGN_NO_ROWS,
    ALIGN_OK,
    ALIGN_SHORT,
    ALIGN_TITLE_MISMATCH,
    CatalogSong,
    MusicCatalogRecorder,
    align_catalog,
    describe_catalog,
    find_song_index,
    read_search_catalog,
)
from app.platforms.douyin_publish import canonical_music


@dataclass(frozen=True)
class _Row:
    """Stands in for `MusicRow`; only `index` and `name` are read."""

    index: int
    name: str


def _payload(*entries: dict) -> dict:
    return {"status_code": 0, "cursor": 20, "has_more": True, "music": list(entries)}


def _entry(music_id: str, title: str, *, author: str = "A", duration: int = 30) -> dict:
    """One `music[]` entry in the measured wire shape.

    ⚠️ `id` and `id_str` BOTH ship, and they are not interchangeable: the
    numeric form is Snowflake-scale and loses precision in JSON consumers above
    2^53. The fixture keeps both, with `id` deliberately carrying the lossy
    value, so a reader that grabs the wrong key fails here rather than in
    production.
    """
    return {
        "id": int(music_id[:15]),
        "id_str": music_id,
        "title": title,
        "author": author,
        "album": "",
        "duration": duration,
    }


# ── read_search_catalog ─────────────────────────────────────────────────


def test_the_measured_payload_shape_yields_its_tracks_in_order():
    songs = read_search_catalog(
        _payload(
            _entry("5000000000201577412", "海阔天空", duration=30),
            _entry("7104847119983068964", "海阔天空 (Live)", duration=44),
        )
    )
    assert [s.index for s in songs] == [0, 1]
    assert [s.name for s in songs] == ["海阔天空", "海阔天空 (Live)"]
    assert [s.duration_s for s in songs] == [30, 44]


def test_the_id_is_taken_from_id_str_so_no_precision_is_lost():
    """The whole identity claim rests on this key being the right one."""
    songs = read_search_catalog(_payload(_entry("5000000000201577412", "海阔天空")))
    assert songs[0].music_id == "5000000000201577412"


def test_a_shape_we_never_measured_yields_nothing_rather_than_a_guess():
    # `music_info_list` also ships and was NOT inspected; reading it would be
    # inventing a contract. Absence here becomes `ids=none`, which a refusal
    # states outright.
    assert read_search_catalog({"status_code": 0, "music_info_list": [{}]}) == []
    assert read_search_catalog({"music": "not a list"}) == []
    assert read_search_catalog(None) == []


def test_an_entry_missing_its_id_is_dropped_not_filled_in():
    songs = read_search_catalog(
        _payload(
            _entry("111", "first"),
            {"id_str": "", "title": "no id"},
            _entry("333", "third"),
        )
    )
    assert [s.music_id for s in songs] == ["111", "333"]


# ── align_catalog ───────────────────────────────────────────────────────


def test_an_alignment_holds_only_when_every_row_was_checked():
    songs = read_search_catalog(_payload(_entry("1", "a"), _entry("2", "b")))
    rows = [_Row(0, "a"), _Row(1, "b")]
    out = align_catalog(rows, songs, canonical=canonical_music)
    assert out.ok is True
    assert out.reason == ALIGN_OK
    assert out.checked == 2


def test_a_shift_at_the_very_end_is_caught_because_every_row_is_checked():
    """RED against any head-only or sampled check.

    One inserted entry near the end shifts only the tail — a sample that looked
    at the first rows would bless it, and the click would land one row off.
    """
    songs = read_search_catalog(
        _payload(_entry("1", "a"), _entry("2", "b"), _entry("444", "inserted"))
    )
    rows = [_Row(0, "a"), _Row(1, "b"), _Row(2, "c")]
    out = align_catalog(rows, songs, canonical=canonical_music)
    assert out.ok is False
    assert out.reason == ALIGN_TITLE_MISMATCH
    assert out.mismatch_at == 2


def test_a_row_past_the_end_of_the_payload_is_its_own_verdict():
    songs = read_search_catalog(_payload(_entry("1", "a")))
    out = align_catalog([_Row(0, "a"), _Row(1, "b")], songs, canonical=canonical_music)
    assert out.ok is False
    assert out.reason == ALIGN_SHORT
    assert out.mismatch_at == 1


def test_no_payload_and_no_readable_rows_are_different_verdicts():
    """The two states that a single boolean would have merged.

    "we never captured a response" points at our listener; "we captured one and
    the page rendered nothing readable" points at the row parser. Reporting
    both as one value is how a broken mechanism gets read as a negative answer.
    """
    nothing = align_catalog([_Row(0, "a")], [], canonical=canonical_music)
    assert nothing.ok is False
    assert nothing.reason == ALIGN_NOT_CAPTURED

    songs = read_search_catalog(_payload(_entry("1", "a")))
    unreadable = align_catalog([_Row(0, "  ")], songs, canonical=canonical_music)
    assert unreadable.ok is False
    assert unreadable.reason == ALIGN_NO_ROWS


def test_rows_the_parser_could_not_name_do_not_by_themselves_break_alignment():
    """An unnamed row is not evidence of a shift.

    It is already reported through the fingerprint judge's `dims=`; withdrawing
    the id path over it would lose the one identity that works because of a
    defect somewhere else.
    """
    songs = read_search_catalog(_payload(_entry("1", "a"), _entry("2", "b")))
    out = align_catalog(
        [_Row(0, "a"), _Row(1, ""), _Row(1, "b")], songs, canonical=canonical_music
    )
    assert out.ok is True
    assert out.checked == 2


def test_alignment_uses_the_same_normaliser_the_fingerprint_judge_uses():
    """Whitespace-only differences must not fail an alignment.

    Two normalisers that disagree by a space would make the id path collapse
    for a reason no reader of the refusal could see.
    """
    songs = read_search_catalog(_payload(_entry("1", "海阔天空")))
    out = align_catalog([_Row(0, "  海阔天空 ")], songs, canonical=canonical_music)
    assert out.ok is True


# ── find_song_index ─────────────────────────────────────────────────────


def test_a_unique_id_addresses_its_row():
    songs = read_search_catalog(
        _payload(_entry("111", "同名"), _entry("222", "同名"), _entry("333", "同名"))
    )
    rows = [_Row(0, "同名"), _Row(1, "同名"), _Row(2, "同名")]
    out = align_catalog(rows, songs, canonical=canonical_music)
    assert find_song_index(out, "222") == 1


def test_a_broken_alignment_yields_no_index_however_clear_the_id_is():
    """The safety property, stated positively.

    The id is right there and unambiguous; the ONLY reason to refuse it is that
    the index would address a row we cannot prove is the right one. An
    implementation that returns 1 here publishes the wrong song.
    """
    songs = read_search_catalog(_payload(_entry("111", "a"), _entry("222", "b")))
    broken = align_catalog([_Row(0, "a"), _Row(1, "z")], songs, canonical=canonical_music)
    assert broken.ok is False
    assert find_song_index(broken, "222") is None


def test_an_id_that_appears_twice_is_refused_rather_than_taken_first():
    songs = [
        CatalogSong(index=0, music_id="dup", name="a"),
        CatalogSong(index=1, music_id="dup", name="b"),
    ]
    out = align_catalog([_Row(0, "a"), _Row(1, "b")], songs, canonical=canonical_music)
    assert out.ok is True
    assert find_song_index(out, "dup") is None


def test_an_id_that_is_absent_yields_no_index():
    songs = read_search_catalog(_payload(_entry("111", "a")))
    out = align_catalog([_Row(0, "a")], songs, canonical=canonical_music)
    assert find_song_index(out, "999") is None


# ── describe_catalog ────────────────────────────────────────────────────


def test_every_failure_state_renders_differently():
    """Five states, five strings.

    A diagnostic that renders two different causes identically sends the reader
    to the wrong place, which is the exact defect `attrs=`/`dims=`/`site=` were
    each added to fix.
    """
    songs = read_search_catalog(_payload(_entry("1", "a"), _entry("2", "b")))
    rendered = {
        describe_catalog(align_catalog([_Row(0, "a"), _Row(1, "b")], songs, canonical=canonical_music)),
        describe_catalog(align_catalog([_Row(0, "a")], [], canonical=canonical_music)),
        describe_catalog(align_catalog([_Row(0, " ")], songs, canonical=canonical_music)),
        describe_catalog(align_catalog([_Row(0, "a"), _Row(9, "x")], songs, canonical=canonical_music)),
        describe_catalog(align_catalog([_Row(0, "z")], songs, canonical=canonical_music)),
    }
    assert len(rendered) == 5
    assert "ids=2/2" in rendered
    assert "ids=none" in rendered
    assert "ids=norows" in rendered


def test_the_clause_carries_indices_never_titles():
    """A title is user-adjacent content and this string is stored and logged."""
    songs = read_search_catalog(_payload(_entry("1", "秘密歌名")))
    clause = describe_catalog(
        align_catalog([_Row(0, "另一个名字")], songs, canonical=canonical_music)
    )
    assert clause == "ids=misaligned@0"
    assert "秘密歌名" not in clause
    assert "另一个名字" not in clause


# ── MusicCatalogRecorder ────────────────────────────────────────────────


class _Response:
    def __init__(self, url: str, body: bytes, status: int = 200) -> None:
        self.url = url
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _Page:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        self.handlers.get(event, []).remove(handler)

    def emit(self, response) -> None:
        for handler in self.handlers.get("response", []):
            handler(response)


def _body(*entries: dict) -> bytes:
    import json

    return json.dumps(_payload(*entries)).encode()


SEARCH_URL = (
    "https://tsearch.amemv.com/openapi/aweme/v1/music/search/?keyword=x&aid=1128"
)


async def test_the_recorder_reads_the_search_response():
    page, rec = _Page(), MusicCatalogRecorder()
    rec.attach(page)
    page.emit(_Response(SEARCH_URL, _body(_entry("111", "a"))))
    await rec.settle()
    assert [s.music_id for s in rec.songs()] == ["111"]


async def test_the_recorder_ignores_every_other_request_on_the_page():
    page, rec = _Page(), MusicCatalogRecorder()
    rec.attach(page)
    page.emit(
        _Response(
            "https://creator.douyin.com/web/api/media/music/list?category_id=1",
            _body(_entry("999", "chart")),
        )
    )
    await rec.settle()
    assert rec.songs() == []


async def test_a_non_200_response_is_not_read_as_an_empty_result_list():
    page, rec = _Page(), MusicCatalogRecorder()
    rec.attach(page)
    page.emit(_Response(SEARCH_URL, b"", status=403))
    await rec.settle()
    # `songs() == []` here means "not captured", which `align_catalog` reports
    # as `ids=none` — never as "the platform listed nothing".
    assert align_catalog([_Row(0, "a")], rec.songs(), canonical=canonical_music).reason == (
        ALIGN_NOT_CAPTURED
    )


async def test_two_searches_are_not_concatenated_into_one_index_space():
    """RED against merging.

    Two responses are two different result lists. Concatenating them builds an
    index space that matches no rendering at all — the very error the alignment
    exists to catch, introduced by the function meant to feed it.
    """
    page, rec = _Page(), MusicCatalogRecorder()
    rec.attach(page)
    page.emit(_Response(SEARCH_URL, _body(_entry("111", "first"))))
    page.emit(_Response(SEARCH_URL, _body(_entry("222", "second"), _entry("333", "third"))))
    await rec.settle()
    assert [s.music_id for s in rec.songs()] == ["222", "333"]


async def test_detaching_stops_the_recorder_from_seeing_later_responses():
    page, rec = _Page(), MusicCatalogRecorder()
    rec.attach(page)
    rec.detach()
    page.emit(_Response(SEARCH_URL, _body(_entry("111", "a"))))
    await rec.settle()
    assert rec.songs() == []
