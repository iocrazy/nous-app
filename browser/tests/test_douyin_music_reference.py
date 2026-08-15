"""Picking the track the user actually pointed at, not one that shares its name.

Why this file exists next to `test_douyin_music.py`
==================================================
That file pins the **typed-name** policy: the user knows a title, the platform's
search is fuzzy, and taking the closest row is a benign completion of an
under-specified request.

This one pins the opposite policy for the opposite request. When the user picks
a card out of the catalogue browser, he picked *that* upload — the one with that
cover, that uploader, that length and that usage count. Two measurements
(2026-08-15) say a title cannot address it:

  * one search for 「起风了」 returned **five rows with character-identical
    titles**, different ids, usage counts from 0 to 30023;
  * a track taken off a category chart came back from a search as a
    **same-titled different upload** — which the title-only matcher calls
    `exact`, clicks, and confirms on read-back. Every gate green, wrong song
    published, and a published post cannot swap its music.

So each test below is written to go RED against the title-only matcher:
`test_the_row_that_matches_the_fingerprint_wins_over_the_first_same_titled_row`
is the direct one (the old code clicks row 0; the right row is row 3), and the
refusal tests assert raises where the old path returned a confident click.

⚠️ None of this proves the DOM selectors are right — the dialog only exists
part-way through a real publish, and the row probe is JavaScript this shim
cannot run. What it proves is that every way of being *unsure* which row is the
user's ends in a typed refusal instead of a published guess.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.platforms import douyin_publish as dp
from app.publish import Deadline, PublishJob
from app.schemas import MediaItem, PublishIntent, SessionStatus
from tests.fakes import FakePage

pytestmark = pytest.mark.unit

EDITOR_URL = "https://creator.douyin.com/creator-micro/content/post/video"
MUSIC_ENTRY = f"text={dp.MUSIC_ENTRY_TEXT}"
MUSIC_SEARCH = dp.MUSIC_SEARCH_INPUT_SELECTORS[0]

# The wire shape the backend sends (mig 429 / `music_platform_options`).
# `music_id` is a STRING on purpose: the platform's search returns both `id`
# (a JSON number past 2^53 — 6953836671917951012 was measured) and `id_str`,
# and only the second survives a trip through JSON. Writing it as an int here
# would make the fixture prove something the real payload never does.
REF = {
    "music_id": "6953836671917951012",
    "music_name": "起风了",
    "music_author": "吴青峰",
    "duration": 325,
    "user_count": 30023,
}


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setenv("BROWSER_PUBLISH_POLL_INTERVAL_S", "0.2")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def rows(*entries: tuple[str, str]) -> list[dp.MusicRow]:
    """`(title, second line)` pairs → parsed rows, as the probe would deliver."""
    return [
        dp.parse_music_row(index, name, meta)
        for index, (name, meta) in enumerate(entries)
    ]


def ref(**overrides) -> dp.MusicReference:
    payload = {**REF, **overrides}
    parsed = dp.read_music_reference(payload)
    assert parsed is not None
    return parsed


# --- pure: reading a row off the dialog --------------------------------------


def test_a_rows_second_line_splits_into_an_uploader_and_a_running_time():
    row = dp.parse_music_row(0, "起风了", "吴青峰·05:25")
    assert (row.author, row.duration_s) == ("吴青峰", 325)


def test_an_uploader_whose_own_name_contains_the_separator_still_parses():
    """The split is on the LAST separator, because the tail is the one part
    whose shape we can verify."""
    row = dp.parse_music_row(0, "Track", "DJ·Kay·03:41")
    assert (row.author, row.duration_s) == ("DJ·Kay", 221)


def test_a_second_line_that_is_not_a_running_time_claims_nothing():
    """Half a parse is worse than none: an author guessed off an unknown layout
    would narrow the candidates on evidence nobody measured."""
    row = dp.parse_music_row(0, "Track", "热门推荐")
    assert (row.author, row.duration_s) == (None, None)


def test_a_row_whose_second_line_is_missing_is_readable_but_unhelpful():
    row = dp.parse_music_row(0, "Track", "")
    assert (row.name, row.author, row.duration_s) == ("Track", None, None)


@pytest.mark.parametrize(
    "text,expected",
    [("03:41", 221), ("1:02:03", 3723), ("", None), ("3m41s", None), ("12", None)],
)
def test_running_times_parse_or_say_they_could_not(text, expected):
    assert dp.parse_music_duration(text) == expected


# --- pure: which row IS the picked track -------------------------------------


def test_the_row_that_matches_the_fingerprint_wins_over_the_first_same_titled_row():
    """**The guard.** [实测 2026-08-15] one search returns five rows titled
    「起风了」, ids all different. The user picked the one with 30023 uses —
    that is *why* he picked it.

    The title-only matcher returns index 0 here and calls it `exact`. Revert to
    it and this test goes red on both the index and the match kind.
    """
    choice = dp.judge_music_reference(
        ref(),
        rows(
            ("起风了", "买辣椒也用券·05:11"),
            ("起风了", "小蓝背奶·04:58"),
            ("起风了", "翻唱君·05:25"),
            ("起风了", "吴青峰·05:25"),
            ("起风了", "DJ版·03:20"),
        ),
    )
    assert (choice.match, choice.index) == ("exact", 3)


def test_five_indistinguishable_rows_refuse_rather_than_pick_one():
    """The dialog did not expose second lines, so nothing separates the five.
    Guessing here is exactly the failure this path exists to prevent — and it
    is invisible afterwards, because the read-back only proves the *title* is
    on the page."""
    choice = dp.judge_music_reference(
        ref(), rows(*[("起风了", "")] * 5)
    )
    assert (choice.match, choice.index, choice.name) == ("ambiguous", None, None)
    assert "5" in choice.reason


def test_a_same_titled_upload_by_someone_else_is_not_the_track_that_was_picked():
    """The 「电子布洛芬（Live）」 case, and the worst one: title-only matching
    reports `exact`, the click lands, the read-back passes (the page really
    does show that title) — and the post carries a different song."""
    choice = dp.judge_music_reference(
        ref(music_name="电子布洛芬 (Live)", music_author="陈婧霏", duration=214),
        rows(("电子布洛芬 (Live)", "另一个上传者·03:34")),
    )
    assert choice.match == "none"
    assert choice.index is None


def test_a_matching_title_and_author_of_the_wrong_length_is_a_different_upload():
    choice = dp.judge_music_reference(
        ref(duration=325), rows(("起风了", "吴青峰·03:20"))
    )
    assert choice.match == "none"


def test_a_second_of_rounding_is_not_a_different_track():
    """The dialog renders `mm:ss` while the catalogue reports seconds, so a
    one-second disagreement is arithmetic, not a different upload."""
    choice = dp.judge_music_reference(
        ref(duration=326), rows(("起风了", "吴青峰·05:25"))
    )
    assert choice.match == "exact"


def test_a_dimension_only_some_rows_expose_is_not_used_to_narrow():
    """Filtering on a field half the rows lack would drop the real row for
    missing data rather than for being wrong. Refusing is the honest outcome:
    we cannot tell these two apart."""
    choice = dp.judge_music_reference(
        ref(), rows(("起风了", "吴青峰·05:25"), ("起风了", ""))
    )
    assert choice.match == "ambiguous"


def test_one_row_with_no_second_line_is_still_a_match():
    """A single same-titled result with nothing to contradict it is the best
    evidence available, and refusing it would make the picker unusable for
    every track with a unique name."""
    choice = dp.judge_music_reference(ref(), rows(("起风了", "")))
    assert (choice.match, choice.index) == ("exact", 0)


def test_a_search_with_no_matching_title_selects_nothing():
    choice = dp.judge_music_reference(ref(), rows(("something else", "x·01:00")))
    assert (choice.match, choice.index) == ("none", None)


def test_case_and_whitespace_still_are_not_a_disagreement_about_which_song():
    choice = dp.judge_music_reference(
        ref(music_name="Dream It Possible", music_author="Delacey", duration=221),
        rows(("dream  it possible", "delacey·03:41")),
    )
    assert choice.match == "exact"


# --- pure: reading the reference off the intent ------------------------------


def test_the_reference_survives_the_options_parse_without_being_called_a_bad_type():
    options = dp.read_platform_options({"music": "起风了", "music_ref": REF})
    assert options.music == "起风了"
    assert options.music_ref is not None
    assert options.music_ref.music_id == "6953836671917951012"
    assert options.bad_types == ()
    assert options.music_ref_broken is False


def test_a_reference_without_an_id_is_refused_rather_than_half_used():
    """Half a fingerprint aligns rows no better than a title does. Falling back
    quietly to the loose match is the one response ruled out — it looks like it
    worked."""
    options = dp.read_platform_options(
        {"music": "起风了", "music_ref": {"music_name": "起风了"}}
    )
    assert options.music_ref is None
    assert options.music_ref_broken is True


def test_a_broken_reference_stops_the_publish_before_a_browser_exists():
    from datetime import datetime, timezone

    intent = PublishIntent(
        content_type="video",
        media=[MediaItem(kind="video", url="https://x/clip.mp4", filename="clip.mp4")],
        title="Launch Day Recap",
        platform_options={"music": "起风了", "music_ref": {"music_id": ""}},
    )
    problem = dp.check_intent(intent, datetime.now(timezone.utc))
    assert problem is not None
    assert problem.reason == "unusable_music_reference"


def test_a_duration_the_upstream_did_not_give_costs_a_dimension_not_the_ref():
    parsed = dp.read_music_reference({**REF, "duration": None})
    assert parsed is not None
    assert parsed.duration_s == 0


# --- the step: refusals are visible, the right row is the one clicked --------


def job(*, music: str, ref_payload: dict | None) -> PublishJob:
    from app.assets import StagedAsset

    options: dict = {"music": music}
    if ref_payload is not None:
        options["music_ref"] = ref_payload
    intent = PublishIntent(
        content_type="video",
        media=[
            MediaItem(
                kind="video",
                url="https://nous-backend:8080/signed/clip.mp4",
                filename="clip.mp4",
            )
        ],
        title="Launch Day Recap",
        platform_options=options,
    )
    return PublishJob(
        platform="douyin",
        storage_state={"cookies": []},
        environment=None,
        intent=intent,
        assets={
            "video": StagedAsset(
                role="video",
                path="/tmp/scratch/clip.mp4",
                filename="clip.mp4",
                size_bytes=99,
            )
        },
    )


def music_page(result_rows: tuple[tuple[str, str], ...]) -> FakePage:
    """A dialog that opens on the second 「选择音乐」 node and lists `result_rows`."""

    def visible(page: FakePage) -> set[str]:
        state = {dp.TITLE_INPUT_SELECTOR}
        opened = (MUSIC_ENTRY, 0) in page.click_targets
        row_clicked = any(
            selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in page.clicks
        )
        if opened and not row_clicked:
            state.add(MUSIC_SEARCH)
        return state

    page = FakePage(url=EDITOR_URL, visible=visible)
    page.counts[MUSIC_ENTRY] = 2
    page.music_rows = result_rows

    def mentions(pg: FakePage, needle: str) -> int:
        row_clicked = any(
            selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in pg.clicks
        )
        titles = {name for name, _ in result_rows}
        return 1 if row_clicked and needle in titles else 0

    page.music_mentions = mentions
    return page


async def test_the_publish_clicks_the_fingerprinted_row_not_the_first_one():
    """End to end through the step: five same-titled rows, and the one that
    gets clicked is the user's. The title-only matcher clicks row 0."""
    page = music_page(
        (
            ("起风了", "买辣椒也用券·05:11"),
            ("起风了", "翻唱君·05:25"),
            ("起风了", "吴青峰·05:25"),
        )
    )
    result = await dp._set_music(
        page, job(music="起风了", ref_payload=REF), Deadline(10)
    )

    assert result["music"] == "applied"
    assert result["music_match"] == "exact"
    # Which policy ran. Without it, this `exact` is indistinguishable from the
    # one the loose matcher produces — and those are the two different claims.
    assert result["music_match_by"] == "reference"
    assert result["music_id"] == REF["music_id"]
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="2"]' in page.clicks
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="0"]' not in page.clicks


async def test_an_ambiguous_result_publishes_nothing_and_says_why():
    """**The guard.** Delete the `music_ambiguous` raise and this goes red:
    without it the step clicks one of five identical rows and reports success.

    Nothing is clicked at all — the refusal happens before any row is touched,
    so no draft is left carrying a track the user never chose.
    """
    page = music_page((("起风了", ""), ("起风了", ""), ("起风了", "")))
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_ambiguous"
    assert excinfo.value.detail["music_id"] == REF["music_id"]
    assert excinfo.value.status is SessionStatus.FAILED
    assert not any(
        selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in page.clicks
    )


async def test_a_same_titled_stranger_fails_instead_of_publishing_the_wrong_song():
    """The measured worst case: the search answers with a row whose title is
    character-identical and whose upload is not the user's. Title-only matching
    publishes it and every check downstream agrees."""
    page = music_page((("起风了", "另一个上传者·03:20"),))
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_not_found"
    assert not any(
        selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in page.clicks
    )


async def test_the_typed_name_path_is_untouched_by_any_of_this():
    """No reference = the old, deliberately looser policy. A user who typed a
    name still gets the closest row and is told it was approximate — changing
    that would break the field's original reason for existing."""
    page = music_page((("起风了 (Cover)", "someone·03:20"),))
    result = await dp._set_music(page, job(music="起风了", ref_payload=None), Deadline(10))

    assert result["music_match"] == "approximate"
    assert result["music_selected"] == "起风了 (Cover)"
    assert "music_match_by" not in result
