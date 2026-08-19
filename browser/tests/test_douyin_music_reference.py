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

import inspect
import re
from pathlib import Path

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

# Captured before `fast_polling` shrinks it, so the length budget below is
# charged the wait a real publish can print, not the 60 ms this file runs with.
SHIPPED_MUSIC_READY_TIMEOUT_MS = dp.MUSIC_READY_TIMEOUT_MS


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setenv("BROWSER_PUBLISH_POLL_INTERVAL_S", "0.2")
    # `wait_for_music_results` reads these at call time. Production values are
    # pinned in `test_douyin_music.py`, so shrinking here cannot ship.
    monkeypatch.setattr(dp, "MUSIC_READY_TIMEOUT_MS", 60)
    monkeypatch.setattr(dp, "MUSIC_READY_POLL_MS", 1)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def rows(*entries: tuple[str, ...]) -> list[dp.MusicRow]:
    """`(title, second line[, usage line])` → parsed rows, as the probe delivers.

    The usage line is the OPTIONAL third element and defaults to `""` — "this
    row showed no readable count" — never to a stand-in number. A default like
    `"0人使用"` would make every row of every fixture carry the same count, and
    "the counts are indistinguishable" would then be a property of this helper
    rather than of the code under test.
    """
    return [
        dp.parse_music_row(index, entry[0], entry[1], entry[2] if len(entry) > 2 else "")
        for index, entry in enumerate(entries)
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
        # Indexed, not unpacked: a row may carry a third element (its 「N人使用」
        # line). Unpacking raises inside `_music_mentions`, which swallows it
        # and returns 0 — i.e. the fixture would fail the read-back for a
        # reason that has nothing to do with the code under test.
        titles = {entry[0] for entry in result_rows}
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


# --- the diagnostic: which of three things happened -------------------------
#
# A real publish failed with `music_not_found (no result carried that title)`
# and the message could not say WHICH of these it was:
#
#   * the dialog listed rows, none titled that      → the two search faces differ
#   * the dialog listed nothing                     → the search itself found none
#   * the probe blew up and was swallowed into []   → we never saw the page
#
# All three produced identical text, and `detail` cannot carry the answer:
# `publish_distribution._finish_account` keeps only `reason` + `message` and
# writes `[reason] message` into the row. So the clause goes in the message,
# and these tests pin that it is there AND that the three stay distinguishable.


def test_rows_that_were_read_are_quoted_with_their_count():
    read = dp.MusicRowsRead(rows(("起风了", "吴青峰·05:25"), ("起风了", "翻唱君·04:58")))
    described = dp.describe_music_rows(read)
    assert "rows=2" in described
    assert "起风了" in described


def test_an_empty_dialog_and_a_broken_probe_do_not_look_alike():
    """**The guard**, and the rule this repo wrote down today: `?` is not `0`.

    Reporting a crashed probe as "0 rows" asserts something about the page that
    nobody observed — the same shape as a health check that reports `false` for
    "could not determine".
    """
    empty = dp.describe_music_rows(dp.MusicRowsRead([]))
    broken = dp.describe_music_rows(dp.MusicRowsRead([], error="TypeError"))

    assert empty != broken
    assert "rows=0" in empty
    assert "rows=?" in broken and "TypeError" in broken
    # And the crashed one must not claim a count at all.
    assert "rows=0" not in broken


def test_the_quoted_titles_are_bounded_in_number_and_length():
    """This string lands in `publish_task_accounts.error_message` (capped at
    500 chars, rendered in the UI, kept in logs). A diagnostic that crowds out
    the sentence it explains has made things worse."""
    long_title = "A" * 200
    read = dp.MusicRowsRead(rows(*[(long_title, "x·01:00")] * 10))
    described = dp.describe_music_rows(read)

    assert "rows=10" in described
    assert described.count("|") == dp.MUSIC_SAMPLE_ROWS - 1
    assert len(described) < 200


async def test_a_failed_publish_says_how_many_rows_it_saw():
    """**The guard.** Delete the clause from the message and this goes red —
    and with it goes the only way to tell, from a user's ordinary failed
    publish, whether the dialog disagrees with the search API or our own probe
    is broken. `detail` cannot do this job: the caller drops it.
    """
    page = music_page((("某首别的歌", "别人·03:20"), ("另一首", "别人·02:10")))
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_not_found"
    assert "rows=2" in excinfo.value.message
    assert excinfo.value.detail["music_rows_seen"] == 2


async def test_a_probe_that_blew_up_is_reported_as_unobserved_not_as_zero():
    """The state that made the real failure unattributable. `_music_rows` used
    to swallow this into `[]`."""

    def explode(_page):
        raise TypeError("probe blew up")

    page = music_page((("起风了", "吴青峰·05:25"),))
    page.music_rows = explode

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "rows=?" in excinfo.value.message
    assert "TypeError" in excinfo.value.message
    # `None`, never 0 — nobody saw the page.
    assert excinfo.value.detail["music_rows_seen"] is None
    assert excinfo.value.detail["music_rows_error"] == "TypeError"


async def test_an_empty_dialog_reports_zero_rows_and_that_it_waited():
    """`rows=0` still says what the reader read. What is new alongside it is
    `ready=` — because on its own, `rows=0` never distinguished "the dialog
    listed nothing" from "we asked before it had answered", and 2026-08-17's
    production failure was the second one wearing the first one's label."""
    page = music_page(())
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "rows=0" in excinfo.value.message
    assert "ready=timeout" in excinfo.value.message
    assert excinfo.value.detail["music_rows_seen"] == 0
    assert excinfo.value.detail["music_rows_error"] is None
    assert excinfo.value.detail["reason"] == "music_results_not_seen"


async def test_an_ambiguous_failure_also_says_what_it_saw():
    page = music_page((("起风了", ""), ("起风了", ""), ("起风了", "")))
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_ambiguous"
    assert "rows=3" in excinfo.value.message


async def test_the_diagnostic_changes_no_verdict():
    """A successful pick stays successful, and nothing about the decision moved
    — this batch only made the failures explain themselves."""
    page = music_page(
        (("起风了", "买辣椒也用券·05:11"), ("起风了", "吴青峰·05:25"))
    )
    result = await dp._set_music(
        page, job(music="起风了", ref_payload=REF), Deadline(10)
    )
    assert result["music"] == "applied"
    assert result["music_match"] == "exact"
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="1"]' in page.clicks


# --- the ambiguity's usage counts -------------------------------------------
#
# 2026-08-17, production: a refusal came back reading
#
#   [music_ambiguous] 4 results are indistinguishable … [rows=20, saw: 未来 | 未来
#   | 未来 ready=results/1617ms anchors=20/20 fresh=20 leaves=452/440 …]
#
# Everything in that line is true and none of it moves the problem: on an
# ambiguity the titles are identical BY DEFINITION, so the sample quoted the one
# axis that cannot ever separate the rows. The open question — is the usage
# count a usable fourth dimension? — was unanswerable from the failure, and the
# track in question showed `user_count: 0`, so it is not even a rare case.
#
# These pin that the next such failure answers it by itself. ⚠️ They pin
# EVIDENCE only: no test below asserts that a differing usage count disambiguates
# anything, because that judge has not been proven and clicking on an unproven
# judge is the exact move this whole path exists to refuse.


def test_a_usage_count_is_kept_as_the_platform_wrote_it():
    assert dp.parse_music_usage("30023人使用") == "30023"
    assert dp.parse_music_usage("1.2万人使用") == "1.2万"
    assert dp.parse_music_usage("3亿人使用") == "3亿"
    # Whitespace between the number and its unit is the platform's, not ours.
    assert dp.parse_music_usage("3 万人使用") == "3万"


def test_an_unreadable_count_is_not_the_count_zero():
    """**The guard**, and the one that matters most on this field: 「0人使用」 is
    a real catalogue value — the track behind the production refusal had exactly
    that — so a row we could not read must not land on the same rendering."""
    assert dp.parse_music_usage("0人使用") == "0"
    assert dp.parse_music_usage("") is None
    assert dp.parse_music_usage(None) is None
    assert dp.parse_music_usage("人气很高") is None

    unread = dp.describe_music_usage(rows(("未来", "", "")), ref())
    zero = dp.describe_music_usage(rows(("未来", "", "0人使用")), ref())
    assert unread != zero
    assert "uses=?" in unread
    assert "uses=0" in zero


def test_counts_that_differ_and_counts_that_agree_do_not_render_alike():
    """**The guard against this batch proving nothing.**

    The whole point of the clause is to separate two futures — "usage is a real
    fourth dimension" from "these rows are identical on it too" — so a fixture
    where both render the same string would make every assertion below pass
    while answering nothing. Both shapes are built here, and their renderings
    are asserted DIFFERENT.
    """
    distinct = dp.describe_music_usage(
        rows(
            ("未来", "", "0人使用"),
            ("未来", "", "31人使用"),
            ("未来", "", "1.2万人使用"),
        ),
        ref(user_count=0),
    )
    identical = dp.describe_music_usage(
        rows(
            ("未来", "", "0人使用"),
            ("未来", "", "0人使用"),
            ("未来", "", "0人使用"),
        ),
        ref(user_count=0),
    )

    assert distinct != identical
    assert distinct == "uses=0|31|1.2万 want=0"
    assert identical == "uses=0|0|0 want=0"
    # And each is readable as its own answer without the other next to it.
    assert len(set(distinct.split("uses=")[1].split(" ")[0].split("|"))) == 3
    assert len(set(identical.split("uses=")[1].split(" ")[0].split("|"))) == 1


def test_the_count_we_expected_is_shown_and_zero_is_not_unknown():
    """`want=` is what the panel stored at pick time. `0` and "the panel stored
    none" are different facts, and on this exact track the real value WAS 0 —
    rendering the absence as `0` would fabricate a match against the rows."""
    picked_at_zero = dp.describe_music_usage(rows(("未来", "", "0人使用")), ref(user_count=0))
    never_stored = dp.describe_music_usage(
        rows(("未来", "", "0人使用")), dp.MusicReference(music_id="1", music_name="未来")
    )

    assert picked_at_zero.endswith("want=0")
    assert never_stored.endswith("want=?")
    assert picked_at_zero != never_stored
    # A typed name has no reference at all, and that is also `?`, never `0`.
    assert dp.describe_music_usage(rows(("未来", "", "0人使用")), None).endswith("want=?")


def test_the_wire_keeps_a_stored_zero_apart_from_a_missing_one():
    """**The guard on the boundary**, and it was missing on the first pass: the
    test above built its `MusicReference` by hand, so `read_music_reference`
    could have collapsed `absent` into `0` (`int(raw or 0)` — the obvious way to
    write it) and everything stayed green.

    `user_count: 0` is what the panel really stored for the track behind the
    2026-08-17 refusal, so a reader that renders "the panel stored nothing" as
    `want=0` would have the diagnostic assert a match against every row reading
    `uses=0` — a fabricated agreement, in the one field added to detect one.
    """
    stored_zero = dp.read_music_reference({**REF, "user_count": 0})
    absent = dp.read_music_reference({k: v for k, v in REF.items() if k != "user_count"})
    unparseable = dp.read_music_reference({**REF, "user_count": "lots"})
    assert stored_zero is not None and absent is not None and unparseable is not None

    assert stored_zero.user_count == 0
    assert absent.user_count is None
    assert unparseable.user_count is None

    candidates = rows(("起风了", "", "0人使用"))
    assert dp.describe_music_usage(candidates, stored_zero).endswith("want=0")
    assert dp.describe_music_usage(candidates, absent).endswith("want=?")


def test_the_usage_clause_is_bounded_in_number_and_length():
    """Same reason the titles are bounded: this lands in
    `publish_task_accounts.error_message`, which the caller caps at 500 chars.
    A truncated list also says so — "all of them agree" read off a silent sample
    of a wider set would be the wrong conclusion drawn confidently."""
    many = rows(*[("未来", "", "1234567890人使用")] * 10)
    clause = dp.describe_music_usage(many, ref(user_count=30023))

    assert clause.count("|") == dp.MUSIC_SAMPLE_USAGE  # 3 separators + the "+N"
    assert f"+{10 - dp.MUSIC_SAMPLE_USAGE}" in clause
    assert len(clause) < 60
    # A clipped count says it was clipped. `1234567890` cut silently to
    # `12345` is still a legible number, and four of those compared side by
    # side would be a comparison of prefixes wearing the look of a comparison
    # of counts — the one reading this clause exists to support.
    assert "12345…" in clause
    assert "|12345|" not in clause
    # A count that fits is printed whole, no decoration.
    assert "31" == dp.describe_music_usage(rows(("未来", "", "31人使用")), None).split("uses=")[1].split(" ")[0]
    # `want` is clipped and marked on the same rule — it is read as one side of
    # a comparison against the counts beside it, so a silent prefix is the same
    # lie there.
    huge = dp.describe_music_usage(rows(("未来", "", "31人使用")), ref(user_count=12345678901234))
    assert huge.endswith("want=123456789…")


def test_the_row_probe_hands_the_usage_line_back_out_of_the_page():
    """**The guard on the one seam no fixture can drive.**

    `FakePage` has no JS engine — it answers the probe with a dict of its own —
    so a probe that stopped *returning* `usage` would leave every test here
    green while every production row rendered `?`. And `?` is the reading that
    blames the platform, so the bug would arrive disguised as its own answer.

    Structural, therefore: the value has to survive into the RETURNED mapping,
    not merely be collected into the intermediate object that never leaves the
    page.
    """
    body = dp._MUSIC_ROWS_JS
    collected, marker, returned = body.rpartition("rows.push(")
    assert marker, "the probe no longer builds its rows"
    assert "anchor.innerText" in returned, (
        "the probe never reads the anchor's own text"
    )
    assert "usage:" in returned, "the probe reads the usage line and drops it"
    assert "levels," in returned, "the probe collects the ladder and drops it"
    assert "levels.push(" in collected, "the probe never walks up from the anchor"


def test_the_reader_survives_a_probe_that_answers_without_a_usage_key():
    """An older browser tab still running the previous bundle answers in the
    previous shape. That is a row we could not read a count for — `None` — and
    emphatically not a row with zero users."""
    assert dp.parse_music_row(0, "起风了", "吴青峰·05:25").usage is None
    assert dp.parse_music_row(0, "起风了", "吴青峰·05:25", None).usage is None


def test_no_candidates_means_no_clause_at_all():
    """An empty string, so the caller appends nothing rather than an empty
    bracket that reads as "we looked and there were none"."""
    assert dp.describe_music_usage((), ref()) == ""


async def test_an_ambiguous_refusal_shows_each_candidates_usage_count():
    """**The guard.** Delete the clause from the message and this goes red — and
    with it goes the only way to learn, from an ordinary failed publish, whether
    usage separates same-titled rows. `detail` cannot carry it: the caller
    (`publish_distribution._finish_account`) keeps `reason` + `message` and drops
    every other key.
    """
    page = music_page(
        (
            ("起风了", "", "0人使用"),
            ("起风了", "", "31人使用"),
            ("起风了", "", "1.2万人使用"),
        )
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(
            page, job(music="起风了", ref_payload={**REF, "user_count": 0}), Deadline(10)
        )

    assert excinfo.value.detail["reason"] == "music_ambiguous"
    assert "uses=0|31|1.2万 want=0" in excinfo.value.message


async def test_an_ambiguous_refusal_shows_identical_counts_as_identical():
    """The other future, end to end. If this and the test above produced the
    same message, the clause would be decoration."""
    page = music_page(
        (
            ("起风了", "", "0人使用"),
            ("起风了", "", "0人使用"),
            ("起风了", "", "0人使用"),
        )
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(
            page, job(music="起风了", ref_payload={**REF, "user_count": 0}), Deadline(10)
        )

    assert "uses=0|0|0 want=0" in excinfo.value.message


async def test_a_row_whose_count_we_could_not_read_says_so_in_the_refusal():
    """Third future: the probe is the problem, and the platform is off the hook.
    Rendered `?`, so nobody reads it as "this row has zero users"."""
    page = music_page((("起风了", "", ""), ("起风了", "", "31人使用")))
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(
            page, job(music="起风了", ref_payload={**REF, "user_count": 0}), Deadline(10)
        )

    assert "uses=?|31 want=0" in excinfo.value.message


async def test_usage_counts_that_differ_still_publish_nothing():
    """**The guard on requirement zero: this batch adds evidence, not a judge.**

    Three rows whose usage counts are all different is exactly the shape a
    usage-based tie-break would resolve — and it must still refuse, still click
    nothing. Whether usage identifies a row has not been proven, and publishing
    on an unproven judge is the same move as publishing a same-titled stranger:
    it looks like a success and cannot be undone.
    """
    page = music_page(
        (
            ("起风了", "", "0人使用"),
            ("起风了", "", "31人使用"),
            ("起风了", "", "1.2万人使用"),
        )
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(
            page, job(music="起风了", ref_payload={**REF, "user_count": 31}), Deadline(10)
        )

    assert excinfo.value.detail["reason"] == "music_ambiguous"
    assert excinfo.value.status is SessionStatus.FAILED
    # Not even the row whose count equals `want`.
    assert not any(
        selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in page.clicks
    )


async def test_a_row_that_the_fingerprint_does_pick_is_unaffected_by_its_count():
    """The verdict is still (title, author, length). A usage count that
    disagrees with the panel's — it climbs, that is the whole reason it cannot
    be a matching dimension — must not turn a good pick into a refusal."""
    page = music_page(
        (
            ("起风了", "买辣椒也用券·05:11", "5人使用"),
            ("起风了", "吴青峰·05:25", "999999人使用"),
        )
    )
    result = await dp._set_music(
        page, job(music="起风了", ref_payload={**REF, "user_count": 30023}), Deadline(10)
    )

    assert result["music"] == "applied"
    assert result["music_match"] == "exact"
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="1"]' in page.clicks


async def test_the_whole_refusal_still_fits_what_the_caller_will_store():
    """`publish_distribution._finish_account` writes `f"[{reason}] {message}"`
    truncated to **500** characters. A diagnostic that pushes the sentence it
    explains off the end has made the failure less legible, not more — so the
    pessimistic shape is measured here rather than hoped for.

    Pessimistic on every axis at once: twenty rows, titles past the sample's
    clip, every row a candidate (so the `+N` marker fires), and a readiness
    reading whose counts are six digits wide.
    """

    def painted_a_lot(page, _stamp):
        answered = page.keyboard.pressed
        return {
            "anchors": 20 if answered else 0,
            "fresh": 20 if answered else 0,
            "leaves": 999999,
            "sig": 2 if answered else 1,
            "textlen": 999999,
        }

    page = music_page(
        tuple(("起" * 40, "", "1234567890人使用") for _ in range(20))
    )
    page.music_probe = painted_a_lot
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(
            page,
            job(music="起" * 40, ref_payload={**REF, "music_name": "起" * 40}),
            Deadline(10),
        )

    stored = f"[{excinfo.value.detail['reason']}] {excinfo.value.message}"
    assert "uses=" in stored and "attrs=" in stored and "dims=" in stored
    # Named individually rather than counted: a clause that stopped rendering
    # would free up budget and make this test PASS more easily, so "they are
    # all here" has to be asserted before "they all fit".
    assert "fit=" in stored

    # `waited_ms` renders as `1` here because this file shrinks the wait to
    # 60 ms; a real publish can print the full ceiling, so the widest that one
    # number can ever be is charged on top rather than quietly enjoyed as slack.
    slack = len(str(SHIPPED_MUSIC_READY_TIMEOUT_MS)) - 1
    # `dims=` prints two counted pairs over the candidate rows. This fixture
    # runs 20 (the width the production refusal actually had), so the four
    # numbers are two digits each; a longer result list would print four. The
    # difference is charged rather than enjoyed, for the same reason as above.
    slack += 4 * (len("9999") - len("20"))
    # `fit=` prints the same pair over the same rows, plus the chosen
    # container's line count. `up=` cannot widen (it is bounded by
    # `MUSIC_ROW_MAX_DEPTH`, one digit) and `svg=` is a single character, but a
    # container we could not read may have any number of lines, so the widest
    # `ln=` this page could print is charged rather than enjoyed.
    slack += 2 * (len("9999") - len("20"))
    slack += len("999") - len("2")

    # **The contract.** The whole string no longer fits 500 in this shape, and
    # that is a deliberate trade rather than an oversight: what must fit is
    # every clause of EVIDENCE, and what may be cut is the closing sentence —
    # boilerplate the frontend replaces with its own localized note anyway
    # (`RecordsPage.PUBLISH_NOTE_KEYS` matches on `[music_ambiguous]`), and the
    # raw text stays whole in the row's tooltip.
    #
    # ⚠️ The end of the evidence is found by the prose that FOLLOWS it, not by
    # the first `]` in the string. The first `]` closes `[music_ambiguous]` —
    # the reason prefix, 17 characters in — so measuring to it made this
    # assertion `17 + slack <= 500` and the guard passed no matter how long the
    # evidence grew. It was vacuous from the day it was written, and it is the
    # only thing standing between a new clause and silent truncation, which
    # reads as "the platform did not report that" rather than as a bug.
    close = stored.index("]. Nothing was published")
    evidence = stored[: close + 1]
    assert len(evidence) + slack <= 500, f"evidence={len(evidence)}+{slack}"
    # Said as the thing a reader of the stored row actually gets: after the
    # caller's clamp, the evidence bracket is closed — no clause was cut in half.
    assert "]. Nothing was published" in stored[:500]

    # ...and the ORDER is what guarantees it stays true as clauses are added:
    # older evidence is nearer the front, so a new clause can only ever crowd
    # itself and then the prose — never `rows=`, `saw:`, `ready=`, `uses=` or
    # `attrs=`.
    for earlier, later in (
        ("rows=", "ready="),
        ("ready=", "uses="),
        ("uses=", "attrs="),
        ("attrs=", "dims="),
        ("dims=", "fit="),
        ("fit=", "Nothing was published"),
    ):
        assert stored.index(earlier) < stored.index(later), f"{earlier} after {later}"


# --- the row-attribute census -----------------------------------------------
#
# The fingerprint (title, author, length) can TIE — that is what
# `music_ambiguous` is, and no amount of re-running the step changes it. The
# only thing that cannot tie is `music_id`, and nothing here can address a row
# by one, because the row's markup has never been measured: `dom_fixture.py`
# contains no music rows at all, and the only proposal for getting them was to
# drive a real browser by hand.
#
# So the census asks the page for the attribute NAMES its rows carry and lets
# an ordinary failed publish carry the answer back. Names only, collected
# in-page via `getAttributeNames()` — a `data-*` VALUE here could be an id, an
# account handle or a token, and this string is stored, logged and rendered in
# a UI, in a public repo. The guarantee is in the API, not in remembering.


def test_the_census_tells_a_row_with_keys_apart_from_one_without():
    """**The guard against this batch proving nothing.**

    The obvious false green: every fixture row carries no attributes, so
    "there is an id" and "there is no id" render the same and every assertion
    below passes while answering neither. Both shapes are built here and their
    renderings are asserted DIFFERENT.
    """
    with_keys = dp.describe_music_attributes(
        dp.MusicRowsRead([], attributes=("class", "data-id", "role"))
    )
    class_only = dp.describe_music_attributes(
        dp.MusicRowsRead([], attributes=("class", "style"))
    )
    bare = dp.describe_music_attributes(dp.MusicRowsRead([], attributes=()))

    assert with_keys != class_only != bare
    assert with_keys != bare
    # The informative keys come FIRST, so a clip can only ever eat furniture.
    assert with_keys == "attrs=data-id,role,class"
    assert class_only == "attrs=class,style"
    assert bare == "attrs=none"


def test_a_census_we_could_not_take_is_not_a_row_without_attributes():
    """**The guard**, and the same rule as `rows=?` vs `rows=0`.

    Rendering "we could not look" as "there is nothing there" would retire the
    click-by-id idea on the strength of an observation nobody made — and it
    would retire it silently, because `attrs=none` is a perfectly plausible
    answer.
    """
    unread = dp.describe_music_attributes(dp.MusicRowsRead([], attributes=None))
    bare = dp.describe_music_attributes(dp.MusicRowsRead([], attributes=()))

    assert unread == "attrs=?"
    assert bare == "attrs=none"
    assert unread != bare


def test_furniture_is_sorted_last_and_never_dropped():
    """Ordering, not filtering. A filter would decide for the reader which of
    the platform's keys are interesting, and a census exists precisely because
    we do not yet know which those are — so everything stays eligible and the
    `+N` says how many did not fit."""
    read = dp.MusicRowsRead(
        [], attributes=("class", "style", "data-e2e", "data-id", "href", "role")
    )
    rendered = dp.describe_music_attributes(read)

    assert rendered.startswith("attrs=data-e2e,data-id,href")
    assert rendered.endswith(f"+{6 - dp.MUSIC_SAMPLE_ATTRS}")
    # `class` and `style` really are still in the running — they were ranked
    # last and then clipped, which the `+N` states.
    assert "class" not in rendered and "style" not in rendered


def test_the_census_is_bounded_and_marks_what_it_clipped():
    """Bounded in both directions — how many keys, and how long each may be.
    A clipped key is MARKED, for the same reason a clipped count is: `data-mus`
    is a plausible attribute name, and someone would go looking for it."""
    long_name = "data-" + "x" * 60
    rendered = dp.describe_music_attributes(dp.MusicRowsRead([], attributes=(long_name,)))

    assert len(rendered) < 30
    assert "…" in rendered
    assert long_name not in rendered
    assert rendered.startswith("attrs=data-xxxxxxxxx")

    # ...and the count bound, stated separately so one cannot mask the other.
    many = dp.MusicRowsRead([], attributes=tuple(f"data-{i}" for i in range(9)))
    listed = dp.describe_music_attributes(many)
    assert listed.count(",") == dp.MUSIC_SAMPLE_ATTRS  # 2 separators + the "+N"
    assert listed.endswith(f"+{9 - dp.MUSIC_SAMPLE_ATTRS}")
    assert len(listed) < 60


async def test_an_ambiguous_refusal_reports_the_keys_the_rows_carry():
    """**The guard.** Delete the clause and this goes red — and with it goes
    the only route to "is there an id on the row?" that does not need a human
    driving a browser."""
    page = music_page((("起风了", "", "0人使用"), ("起风了", "", "0人使用")))
    page.music_attrs = ("class", "data-music-id")

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "attrs=data-music-id,class" in excinfo.value.message
    assert excinfo.value.detail["music_row_attributes"] == ("class", "data-music-id")


async def test_an_ambiguous_refusal_says_so_when_the_rows_carry_nothing():
    """The other answer, and it is just as useful: the fingerprint really is
    all there is, and the effort belongs elsewhere. If this and the test above
    produced the same message the census would be decoration."""
    page = music_page((("起风了", "", "0人使用"), ("起风了", "", "0人使用")))
    page.music_attrs = ()

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "attrs=none" in excinfo.value.message
    assert excinfo.value.detail["music_row_attributes"] == ()


async def test_a_tab_on_the_older_bundle_reports_no_census_not_an_empty_one():
    """The probe used to answer with a bare ARRAY. A tab still running that
    bundle has perfectly good rows and no census — `?`, never `none`."""
    page = music_page((("起风了", "", "0人使用"), ("起风了", "", "0人使用")))
    page.music_rows_legacy_shape = True

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    # The rows still parsed — this is not a broken read.
    assert "rows=2" in excinfo.value.message
    assert "attrs=?" in excinfo.value.message
    assert excinfo.value.detail["music_row_attributes"] is None


def test_the_census_survives_out_of_the_page():
    """**The guard on the seam no fixture can drive**, and it was missing on the
    first pass: a probe that collects the census and then drops it from its
    return value left every test here green, because `FakePage` has no JS engine
    and answers with a dict of its own. Production would then render `attrs=?`
    for every publish — and `?` is the reading that blames our probe, so the bug
    would arrive already wearing its own excuse.

    Structural, therefore: the value must be collected AND must appear in the
    object the probe actually returns.
    """
    body = dp._MUSIC_SITE_JS
    collected, marker, returned = body.rpartition("return {")
    assert marker, "the probe no longer returns an object"
    assert "attrs.add(" in collected, "the probe never collects any key"
    assert "attrs" in returned, "the probe collects the census and drops it"
    assert "setAttribute(" in collected, "the probe stopped stamping the row"
    # ...and the rows themselves still come back from the pass that reads them.
    assert "rows" in dp._MUSIC_ROWS_JS.rpartition("return {")[2]


def test_every_attribute_this_module_stamps_is_registered():
    """**The guard for whoever adds the next stamp.**

    The census subtracts `MUSIC_OWN_ATTRIBUTES` from what it found. A third
    `data-nous-*` introduced without joining that tuple would be reported back
    as the PLATFORM's — and that is the worst failure this file has, because it
    is encouraging: it says "there is an id on the row", someone builds
    click-by-id on it, every test passes, and the thing addresses nothing real.

    So the registry is checked against the module's own source rather than
    against anyone's memory.
    """
    source = Path(dp.__file__).read_text(encoding="utf-8")
    stamped = set(re.findall(r'"(data-nous-[a-z0-9-]+)"', source))

    assert stamped, "no stamped attributes found — has the naming changed?"
    missing = stamped - set(dp.MUSIC_OWN_ATTRIBUTES)
    assert not missing, f"stamped but not registered in MUSIC_OWN_ATTRIBUTES: {missing}"
    # And the registry does not claim attributes the module never writes.
    assert set(dp.MUSIC_OWN_ATTRIBUTES) <= stamped


def test_the_census_never_reports_our_own_marks():
    """**The guard on the one thing that would fake a positive.** This module
    stamps two attributes on the page itself; reporting them back would read as
    "the rows carry data attributes" and send someone building a click-by-id
    path against markup that is ours.

    Structural, because the fake cannot run the page: the probe must exclude
    them by name, and the caller must be the one telling it which names.
    """
    body = dp._MUSIC_SITE_JS
    assert "options.ours" in body
    assert "ours.has(name)" in body
    # ...and the caller really passes them.
    source = inspect.getsource(dp._music_rows)
    assert '"ours"' in source
    assert "MUSIC_OWN_ATTRIBUTES" in source
    # The registry really does hold every stamp this module writes.
    assert dp.MUSIC_ROW_ATTRIBUTE in dp.MUSIC_OWN_ATTRIBUTES
    assert dp.MUSIC_SEEN_ATTRIBUTE in dp.MUSIC_OWN_ATTRIBUTES
    assert dp.MUSIC_ANCHOR_ATTRIBUTE in dp.MUSIC_OWN_ATTRIBUTES


def test_the_census_collects_names_and_cannot_reach_a_value():
    """**The privacy guard, structural.** Values could be ids, handles or
    tokens; this lands in a stored, logged, UI-rendered string in a public
    repo. `getAttributeNames()` cannot return a value even by accident —
    walking `.attributes` or `.dataset` could, so neither may appear."""
    # Comments stripped first: a comment cannot read a value, and one that
    # explains WHY `.attributes` is avoided must not be what fails this test.
    code = "\n".join(
        line.split("//")[0]
        for script in (dp._MUSIC_ROWS_JS, dp._MUSIC_SITE_JS)
        for line in script.splitlines()
    )
    assert "getAttributeNames()" in code
    assert ".attributes" not in code
    assert ".dataset" not in code
    assert "getAttribute(" not in code.replace("getAttributeNames(", "")
    # `setAttribute` is ours — the anchor stamp in the first pass and the row
    # stamp in the second — and both write, never read.
    assert code.count("setAttribute(") == 2


async def test_the_census_changes_no_verdict():
    """A row the fingerprint does pick is still picked, whatever the rows carry.
    The census is evidence; it decides nothing."""
    page = music_page(
        (("起风了", "买辣椒也用券·05:11", ""), ("起风了", "吴青峰·05:25", ""))
    )
    page.music_attrs = ("data-music-id", "class")
    result = await dp._set_music(
        page, job(music="起风了", ref_payload=REF), Deadline(10)
    )

    assert result["music"] == "applied"
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="1"]' in page.clicks


async def test_the_typed_name_path_is_untouched_by_any_of_this():
    """No reference = the old, deliberately looser policy. A user who typed a
    name still gets the closest row and is told it was approximate — changing
    that would break the field's original reason for existing."""
    page = music_page((("起风了 (Cover)", "someone·03:20"),))
    result = await dp._set_music(page, job(music="起风了", ref_payload=None), Deadline(10))

    assert result["music_match"] == "approximate"
    assert result["music_selected"] == "起风了 (Cover)"
    assert "music_match_by" not in result


# --- one unreadable row no longer costs every row its fingerprint ------------
#
# The refusal of 2026-08-17 said 「4 results are indistinguishable」 for a track
# the catalogue lists as separable — the picked upload and its siblings differ
# by uploader and by running time, and the live dialog shows both on every row.
# The reason the fingerprint did not separate them is that it was never applied:
# the axis gate read
#
#     if ref.music_author and all(row.author is not None for row in pool):
#
# so ONE row anywhere in the list whose second line failed to parse withdrew the
# author axis from **all twenty**, and the same for the length. What was left
# compared titles — which on a same-titled list is exactly the axis guaranteed
# to carry no information, i.e. the matcher this file exists to replace, re-
# entered through a data condition rather than through a code change.
#
# ⚠️ The gate's INTENT was right and is preserved: an unreadable row is still
# never excluded, because it may be the row the user picked. What changed is
# that its unreadability is no longer contagious.


def test_one_unparsed_row_no_longer_withdraws_the_author_axis_from_the_others():
    """RED on the old gate. Three same-titled rows: the user's, a different
    uploader's, and one whose second line did not parse.

    Old: `all(...)` is false because of the third row, so neither axis runs and
    all three tie — `ambiguous`, quoting three identical titles.
    New: the different uploader is READ and dropped. Two remain (the match and
    the unreadable one), so this still refuses — but on two, and the wrong
    uploader is gone rather than padding the tie.
    """
    choice = dp.judge_music_reference(
        ref(),
        rows(
            ("起风了", "吴青峰·05:25"),
            ("起风了", "买辣椒也用券·05:11"),
            ("起风了", ""),
        ),
    )
    assert choice.match == "ambiguous"
    # The count is the assertion that fails on the old code: it said 3.
    assert len(choice.candidates) == 2
    assert {row.index for row in choice.candidates} == {0, 2}


def test_a_wrong_uploader_is_dropped_even_though_another_row_is_unreadable():
    """RED on the old gate, and this one changes the VERDICT rather than the
    count.

    Two rows are read and both are the wrong uploader; one row did not parse.
    Old: the axis never runs, three titles tie, `ambiguous`.
    New: both wrong uploaders are excluded, leaving only the unreadable row —
    which is refused for the right reason and with the right words, because a
    row nothing was ever read on cannot be confirmed as the user's pick.
    """
    choice = dp.judge_music_reference(
        ref(),
        rows(
            ("起风了", "买辣椒也用券·05:11"),
            ("起风了", "翻唱君·05:25"),
            ("起风了", ""),
        ),
    )
    assert choice.match == "ambiguous"
    assert len(choice.candidates) == 1
    assert "nothing on it could be read" in choice.reason


def test_the_length_axis_also_survives_a_neighbour_that_did_not_parse():
    """The same gate guarded the running time, and the ground-truth case turns
    on exactly that axis: the two surviving uploads share a title AND an
    uploader, and differ only in length (0:38 vs 0:25).
    """
    choice = dp.judge_music_reference(
        ref(music_author="", duration=325),
        rows(
            ("起风了", "吴青峰·05:25"),
            ("起风了", "吴青峰·04:11"),
            ("起风了", ""),
        ),
    )
    assert choice.match == "ambiguous"
    # Old code: 3 (the axis never ran). New: the 04:11 upload is read and gone.
    assert {row.index for row in choice.candidates} == {0, 2}


def test_a_fully_readable_list_still_resolves_to_the_one_row_that_matches():
    """The fix must not cost the case that already worked."""
    choice = dp.judge_music_reference(
        ref(),
        rows(
            ("起风了", "买辣椒也用券·05:11"),
            ("起风了", "吴青峰·05:25"),
            ("起风了", "翻唱君·05:25"),
        ),
    )
    assert (choice.match, choice.index) == ("exact", 1)


def test_a_row_that_survived_only_because_its_siblings_were_excluded_is_refused():
    """The bar that keeps the narrowing from becoming a new way to guess.

    Two rows: one read as the wrong uploader, one unreadable. Dropping the first
    leaves exactly one row standing — and clicking it would be a publish of a
    row nothing was ever verified about beyond its title, which is the failure
    this whole path exists to prevent. `exact` requires a row that AGREED, not
    one that merely outlasted the others.

    ⚠️ GREEN on the old code too, and deliberately so — it is a guard, not a
    regression test. The old gate never excluded anything on a partially
    readable list, so it could not reach this shape at all; the exclusion that
    reaches it is new, and so is the risk. Kept because the assertion goes red
    the moment someone relaxes the `full` requirement to "one row left".
    """
    choice = dp.judge_music_reference(
        ref(), rows(("起风了", "买辣椒也用券·05:11"), ("起风了", ""))
    )
    assert choice.match == "ambiguous"


# --- the axis census ---------------------------------------------------------
#
# `dims=` is the clause that tells the two worlds apart. Before it, a refusal
# over same-titled rows was equally consistent with "the platform's rows really
# do not differ in anything we can see" and with "our parser did not read the
# second line", and `saw:` could not distinguish them because on an ambiguity
# the titles are identical by definition. The two call for opposite work.


def test_the_census_says_when_the_axes_were_read_on_every_row():
    choice = dp.judge_music_reference(
        ref(), rows(("起风了", "甲·05:25"), ("起风了", "乙·05:25"))
    )
    assert dp.describe_music_dimensions(choice) == "dims=name+author+dur au=2/2 du=2/2"


def test_the_census_says_when_the_axes_were_read_on_nobody():
    """The shape that names OUR parser as the defect. `au=0/3` cannot be read as
    "the rows differ in ways we cannot see" — it says the rows were never asked
    successfully, and the fix is on this side of the wire."""
    choice = dp.judge_music_reference(
        ref(), rows(("起风了", ""), ("起风了", ""), ("起风了", ""))
    )
    assert dp.describe_music_dimensions(choice) == "dims=name au=0/3 du=0/3"
    # ...and the axis list says so a second way: no axis but the title ran.
    assert choice.dimensions is not None
    assert choice.dimensions.used == ("name",)


def test_the_census_counts_partial_readability_rather_than_rounding_it_away():
    """The exact shape that used to be invisible: some rows answered, some did
    not, and the old gate turned that into "no axis at all" for everybody."""
    choice = dp.judge_music_reference(
        ref(),
        rows(("起风了", "甲·05:25"), ("起风了", ""), ("起风了", "乙·05:11")),
    )
    assert dp.describe_music_dimensions(choice) == "dims=name+author+dur au=2/3 du=2/3"


def test_an_axis_the_reference_never_carried_reads_as_absent_not_as_zero():
    """`-` and `0/2` are different findings: one is "nothing was asked", the
    other is "we asked and the page said nothing". Collapsing them would blame
    the parser for a reference that simply had no author — the same
    `?`-is-not-`0` rule `rows=` and `attrs=` follow."""
    choice = dp.judge_music_reference(
        ref(music_author="", duration=0),
        rows(("起风了", "甲·05:25"), ("起风了", "乙·05:11")),
    )
    assert dp.describe_music_dimensions(choice) == "dims=name au=- du=-"


def test_the_census_is_counts_only_and_never_quotes_a_row():
    """It rides in a stored, logged, UI-rendered string in a PUBLIC repo."""
    choice = dp.judge_music_reference(
        ref(), rows(("起风了", "买辣椒也用券·05:11"), ("起风了", ""))
    )
    rendered = dp.describe_music_dimensions(choice)
    assert "买辣椒也用券" not in rendered and "吴青峰" not in rendered


# --- the sentence may not claim an axis the judge did not use ----------------


def test_the_exact_sentence_names_only_the_axes_that_were_actually_compared():
    """It used to say "title, author and length" unconditionally. On a list
    whose second lines did not parse, that was a plain false statement about how
    the row had been chosen — and it was the only thing a reader of a successful
    publish had to go on."""
    on_everything = dp.judge_music_reference(
        ref(), rows(("起风了", "买辣椒也用券·05:11"), ("起风了", "吴青峰·05:25"))
    )
    assert on_everything.reason == "one row matched title, author and length"

    # Nothing but the title was readable, so nothing but the title was compared.
    on_title_only = dp.judge_music_reference(ref(), rows(("起风了", "")))
    assert on_title_only.match == "exact"
    assert on_title_only.reason == "one row matched title"
    assert "author" not in on_title_only.reason


def test_the_exact_sentence_drops_the_axis_the_reference_did_not_carry():
    choice = dp.judge_music_reference(
        ref(duration=0),
        rows(("起风了", "买辣椒也用券·05:11"), ("起风了", "吴青峰·05:25")),
    )
    assert choice.reason == "one row matched title and author"


# --- finding the row a 「N人使用」 anchor belongs to ----------------------------
#
# [实测 2026-08-17, 生产] The refusal that produced this section read
# `rows=19 … dims=name au=0/5 du=0/5`: nineteen rows on screen, five of them
# sharing the requested title, and **not one gave up an author or a running
# time** — on a dialog whose every row shows both. The fingerprint had degraded
# to the title-only matcher this whole file exists to replace, silently,
# through a data condition, on the one axis nothing was measuring: whether the
# element we were calling a row was one.
#
# The walk stopped at "the first ancestor with two or more text lines", which is
# a proxy for being a row and not a test of it. `resolve_music_row` replaces the
# proxy with the reading — the row is the smallest container that gives up a
# title, an author AND a running time — and a container that gives up fewer is
# not called a row at all.
#
# ⚠️ Every fixture below hands the probe a LADDER whose rungs differ, because a
# fixture whose innermost rung is already the right container proves nothing:
# the old rule and the new one both pass it. The rung the old rule would have
# taken is present in each one, and each test says which rung was taken.


def ladder(*rungs: tuple[str, ...]) -> list[dict[str, object]]:
    """Rungs from the anchor outwards, innermost first. Plain HTML nodes."""
    return [{"lines": list(rung), "svg": False} for rung in rungs]


# One row of the live dialog as the ladder it really is: the count's own cell,
# then a header carrying the title beside the count, then the row. The middle
# rung is the trap — two text lines, so the old walk stopped there, read line 1
# as the title (correctly, which is why `saw:` looked fine) and line 2 as
# 「作者·时长」, which is where `au=0/5 du=0/5` came from.
NESTED = (
    "起风了",
    "吴青峰·05:25",
    "30023人使用",
    ladder(
        ("30023人使用",),
        ("起风了", "30023人使用"),
        ("起风了", "吴青峰·05:25", "30023人使用"),
    ),
)
# The same shape for a row that is NOT the one the reference points at, so a
# list can hold both without the fixture deciding the verdict by having one row.
NESTED_OTHER = (
    "起风了",
    "买辣椒也用券·05:11",
    "5人使用",
    ladder(
        ("5人使用",),
        ("起风了", "5人使用"),
        ("起风了", "买辣椒也用券·05:11", "5人使用"),
    ),
)


def test_the_row_is_the_smallest_container_that_reads_as_a_whole_row():
    """**The fix, stated as the thing that goes red on a revert.**

    Rung 1 has two text lines, so the old walk took it and read `30023人使用` as
    the author line. Rung 2 is the row. Both the level and the reading are
    asserted: a revert to "first rung with two lines" fails on `level`, and a
    revert to "line 2 is the author" fails on `author`.
    """
    site = dp.resolve_music_row(
        [
            dp.MusicRowLevel(lines=("30023人使用",)),
            dp.MusicRowLevel(lines=("起风了", "30023人使用")),
            dp.MusicRowLevel(lines=("起风了", "吴青峰·05:25", "30023人使用")),
        ]
    )

    assert site.level == 2
    assert site.fit is True
    assert (site.name, site.author, site.duration_s) == ("起风了", "吴青峰", 325)


def test_a_container_that_reads_two_of_the_three_is_not_called_a_row():
    """The production shape, with no outer rung ever rendering an author. It
    still has to click somewhere and still has to quote a title, but it is
    reported unfit and gives up nothing to match on — because "we could not read
    it" and "the page says this" are the two readings the refusal turns on."""
    site = dp.resolve_music_row(
        [
            dp.MusicRowLevel(lines=("30023人使用",)),
            dp.MusicRowLevel(lines=("起风了", "30023人使用")),
        ]
    )

    assert site.fit is False
    assert site.name == "起风了"
    assert site.author is None and site.duration_s is None


def test_the_author_and_the_length_may_arrive_on_two_separate_lines():
    """`innerText` does not reproduce CSS-generated content, so a row rendering
    「作者 · 时长」 with the dot as a `::before` arrives as two lines with no
    separator anywhere. Reading that as "this row did not tell us its author" is
    the same `?`-for-a-fact this module refuses everywhere else."""
    site = dp.resolve_music_row(
        [dp.MusicRowLevel(lines=("起风了", "吴青峰", "05:25", "30023人使用"))]
    )

    assert site.fit is True
    assert (site.name, site.author, site.duration_s) == ("起风了", "吴青峰", 325)


def test_the_author_and_the_length_may_arrive_run_together():
    """The same row when those two are inline siblings: `innerText` puts them on
    one line and the generated dot is still absent."""
    site = dp.resolve_music_row(
        [dp.MusicRowLevel(lines=("起风了", "吴青峰05:25", "30023人使用"))]
    )

    assert site.fit is True
    assert (site.name, site.author, site.duration_s) == ("起风了", "吴青峰", 325)


def test_the_walk_stops_at_the_list_instead_of_calling_it_a_row():
    """A rung carrying two 「N人使用」 counts holds two rows, so it is the list.
    Climbing into it would find a title, an author and a length — belonging to
    the row NEXT to this one — and call them this row's.

    That is not a near miss but a confident wrong reading, which is the one
    failure this path exists to prevent.
    """
    site = dp.resolve_music_row(
        [
            dp.MusicRowLevel(lines=("30023人使用",)),
            dp.MusicRowLevel(lines=("起风了", "30023人使用")),
            dp.MusicRowLevel(
                lines=(
                    "起风了",
                    "30023人使用",
                    "起风了",
                    "买辣椒也用券·05:11",
                    "5人使用",
                )
            ),
        ]
    )

    assert site.fit is False
    assert site.level == 1


def test_an_icon_is_never_mistaken_for_the_row_and_says_so_when_it_was_taken():
    """`svg=` exists because 2026-08-17's census came back `d,fill,fill-opacity`
    — SVG path attributes — and nothing in the refusal could say whether that
    meant "the row contains an icon" or "we were reading an icon". The chosen
    container now answers it itself."""
    icon_first = dp.resolve_music_row(
        [
            dp.MusicRowLevel(lines=("30023人使用", "使用"), svg=True),
            dp.MusicRowLevel(lines=("起风了", "吴青峰·05:25", "30023人使用")),
        ]
    )
    assert icon_first.level == 1 and icon_first.svg is False

    # ...and when there is nothing better, the flag is what makes the bad hop
    # legible instead of merely unproductive.
    only_icon = dp.resolve_music_row(
        [dp.MusicRowLevel(lines=("30023人使用", "使用"), svg=True)]
    )
    assert only_icon.fit is False and only_icon.svg is True


def test_a_ladder_with_nothing_to_name_yields_no_row_at_all():
    """No rung says anything but the count. There is nothing to click and
    nothing to quote, and inventing either would be worse than the gap."""
    assert dp.resolve_music_row([]).level is None
    assert dp.resolve_music_row([dp.MusicRowLevel(lines=("30023人使用",))]).level is None


# --- the same thing through the step -----------------------------------------


async def test_the_publish_reads_the_row_off_the_container_that_holds_it_all():
    """End to end on the nested shape. The old walk stopped one rung short, read
    no author and no length off either row, and refused `music_ambiguous`; this
    picks the user's row and publishes.

    The stamp plan is asserted too, because that IS the hop: the click lands on
    rung 2, not on the header the old walk chose.
    """
    page = music_page((NESTED_OTHER, NESTED))
    result = await dp._set_music(
        page, job(music="起风了", ref_payload=REF), Deadline(10)
    )

    assert result["music"] == "applied"
    assert result["music_match"] == "exact"
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="1"]' in page.clicks
    assert page.music_stamped == [
        {"index": 0, "level": 2},
        {"index": 1, "level": 2},
    ]


async def test_an_ambiguous_refusal_says_whether_it_ever_found_the_row():
    """**The clause that separates the two worlds `dims=` cannot.** Rows whose
    container never renders an author are indistinguishable *to us*; rows that
    read cleanly and still tie are indistinguishable *on the platform*. Both
    refuse, and until this clause they produced identical evidence."""
    unread = music_page(
        (
            ("起风了", "", "0人使用", ladder(("0人使用",), ("起风了", "0人使用"))),
            ("起风了", "", "0人使用", ladder(("0人使用",), ("起风了", "0人使用"))),
        )
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(unread, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "fit=0/2" in excinfo.value.message
    assert "up=2/2 ln=2 svg=0" in excinfo.value.message
    assert excinfo.value.detail["music_rows_fit"] == 0

    # The other world: both rows read completely, and they still tie.
    read = music_page(
        (
            ("起风了", "吴青峰·05:25", "0人使用"),
            ("起风了", "吴青峰·05:25", "1人使用"),
        )
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(read, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "fit=2/2" in excinfo.value.message
    assert excinfo.value.detail["music_rows_fit"] == 2


def test_the_container_clause_says_nothing_when_there_are_no_rows():
    """`rows=0` has already said it, and a clause that repeats it spends
    characters the sentence itself needs. A probe that never ran says `?`."""
    assert dp.describe_music_container(dp.MusicRowsRead([], sites=())) == ""
    assert dp.describe_music_container(dp.MusicRowsRead([])) == "fit=?"


def test_a_row_with_no_container_at_all_renders_its_position_as_unknown():
    """`_music_rows` drops such a row rather than reporting one it cannot
    address, so this branch is not reachable through the step today. It is
    pinned anyway, because the alternative to a `?` here is a `TypeError` in
    the middle of composing a failure message — a diagnostic that crashes while
    explaining a failure replaces the diagnosis with its own stack trace."""
    site = dp.MusicRowSite(level=None, levels=4, fit=False)
    assert dp.describe_music_container(
        dp.MusicRowsRead([], sites=(site,))
    ) == "fit=0/1 up=?/4 ln=0 svg=0"


async def test_the_census_is_taken_on_the_container_that_was_chosen():
    """The census answers "is there an id on the row?", and an answer read off
    the header the old walk stopped at is an answer about the header. It rides
    on the second pass precisely so it cannot be taken anywhere else."""
    page = music_page(
        (
            ("起风了", "", "0人使用", ladder(("0人使用",), ("起风了", "0人使用"))),
            NESTED,
        )
    )
    page.music_attrs = ("class", "data-music-id")

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "attrs=data-music-id,class" in excinfo.value.message
    # Row 0 never resolved past its header (rung 1); row 1 did (rung 2). The
    # census was asked about exactly those two nodes.
    assert page.music_stamped == [
        {"index": 0, "level": 1},
        {"index": 1, "level": 2},
    ]


async def test_a_second_pass_that_blows_up_costs_the_census_not_the_rows():
    """The rows were read before the stamp was attempted, so they stand — and
    the reading of their containers stands with them. What is lost is the
    census, and it is lost as `?` ("we could not look"), never as `none` ("the
    rows carry nothing"): a page nobody looked at must not retire the id idea.

    ⚠️ The stamp is lost too, so a click would find no such selector and fail
    with `music_click_failed`. `FakePage` cannot show that — it records the
    selector rather than resolving it — so this pins what is observable here
    and the click's own refusal stays pinned by its own test.
    """
    page = music_page(
        (
            ("起风了", "吴青峰·05:25", "0人使用"),
            ("起风了", "吴青峰·05:25", "1人使用"),
        )
    )
    page.music_attrs = ("class", "data-music-id")
    page.music_site_probe_fails = True

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert "rows=2" in excinfo.value.message
    assert "fit=2/2" in excinfo.value.message
    assert "attrs=?" in excinfo.value.message
    assert excinfo.value.detail["music_row_attributes"] is None


# --- the line reader ---------------------------------------------------------


def test_one_line_is_read_as_author_and_length_in_three_renderings():
    """Three renderings of ONE thing — 「作者·时长」 — and which one a row
    produces is decided by CSS, not by the platform's copy."""
    assert dp.parse_music_meta("吴青峰·05:25") == ("吴青峰", 325)
    assert dp.parse_music_meta("吴青峰05:25") == ("吴青峰", 325)
    assert dp.parse_music_meta("05:25") == (None, 325)


def test_a_line_that_is_not_a_running_time_claims_nothing():
    """Half a reading is the kind of evidence that looks like a match without
    being one."""
    assert dp.parse_music_meta("30023人使用") == (None, None)
    assert dp.parse_music_meta("吴青峰") == (None, None)
    assert dp.parse_music_meta("") == (None, None)
    assert dp.parse_music_meta(None) == (None, None)


# --- the identity path: the dialog's own search response ---------------------
#
# Everything above is about being *unsure* which row is the user's, and every
# one of those tests ends in a refusal. This section is the first thing in this
# file that can end in a **click on a list of character-identical rows** — and
# it can only do so because the click no longer rests on what the row looked
# like.
#
# [实测 2026-08-19] Opening the dialog and typing makes the page fetch
#
#     GET tsearch.amemv.com/openapi/aweme/v1/music/search/?keyword=...
#     → {"music": [{"id_str": "...", "title": "...", "duration": 30, ...}]}
#
# so the identity the fingerprint was standing in for is one listener away. The
# fixtures below copy that wire shape exactly (`id_str` a STRING — the sibling
# `id` is Snowflake-scale and lossy in JSON), per CLAUDE.md's 「边界 mock 必须
# 用真实 JSON 形状」.


def _song(music_id: str, title: str, *, duration: int = 325) -> dict:
    return {"id_str": music_id, "title": title, "author": "x", "duration": duration}


async def test_the_search_response_resolves_the_ambiguity_it_used_to_refuse():
    """**The payoff, and the one test that had to go red before.**

    These are the same three character-identical rows that
    `test_an_ambiguous_result_publishes_nothing_and_says_why` refuses — the
    shape production hit twice (「未来」, `4 results are indistinguishable`).
    Nothing about the ROWS changed; what changed is that the platform's own
    response says which of them is `6953836671917951012`.

    Run this against the code before the id path and it raises
    `music_ambiguous`. That is the point: the fingerprint could only ever
    refuse this case, never resolve it.
    """
    page = music_page((("起风了", ""), ("起风了", ""), ("起风了", "")))
    page.music_catalog = [
        _song("1111111111111111111", "起风了"),
        _song(REF["music_id"], "起风了"),
        _song("3333333333333333333", "起风了"),
    ]

    result = await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert result["music"] == "applied"
    assert result["music_match"] == "exact"
    # Not `reference`. The two are both `exact` and they are NOT the same
    # claim: one says a scraped fingerprint agreed, the other says the platform
    # named the row. Collapsing them would hide the day this path stopped
    # running, which would look exactly like nothing having changed.
    assert result["music_match_by"] == "id"
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="1"]' in page.clicks
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="0"]' not in page.clicks
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="2"]' not in page.clicks


async def test_a_payload_that_does_not_line_up_refuses_exactly_as_before():
    """The safety property, and the reason the pairing is checked at all.

    The id is right there and unambiguous. The ONLY thing wrong is that the
    payload's second entry is a different track from the row rendered second —
    so the index would address a row nobody proved is the right one. An
    implementation that clicks anyway publishes the wrong song and every gate
    stays green.
    """
    page = music_page((("起风了", ""), ("起风了", ""), ("起风了", "")))
    page.music_catalog = [
        _song("1111111111111111111", "起风了"),
        _song(REF["music_id"], "另一首完全不同的歌"),
        _song("3333333333333333333", "起风了"),
    ]

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_ambiguous"
    assert "ids=misaligned@1" in excinfo.value.message
    assert not any(
        selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in page.clicks
    )


async def test_a_refusal_with_no_payload_says_so_rather_than_staying_silent():
    """`ids=none` names OUR listener, not the platform.

    Without this clause, "we never captured a response" and "we captured one
    that carried no matching id" produce the same refusal — and they send a
    reader to opposite places (our URL fragment vs. the catalogue).
    """
    page = music_page((("起风了", ""), ("起风了", "")))
    assert page.music_catalog is None

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_ambiguous"
    assert "ids=none" in excinfo.value.message


async def test_an_id_that_is_absent_from_the_payload_still_refuses():
    """A captured, well-aligned payload that simply does not contain the track.

    This must NOT click: the alignment holding says the rows are addressable,
    it says nothing about the picked track being among them.
    """
    page = music_page((("起风了", ""), ("起风了", "")))
    page.music_catalog = [
        _song("1111111111111111111", "起风了"),
        _song("3333333333333333333", "起风了"),
    ]

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job(music="起风了", ref_payload=REF), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_ambiguous"
    assert "ids=2/2" in excinfo.value.message


async def test_the_typed_name_path_never_consults_the_payload():
    """A typed name is a different request and keeps its own policy.

    The payload is present and its second entry is the user's word; the typed
    path must still take the row the TITLE matched, because "some song called
    this" was the whole request. Reading an id here would silently convert one
    policy into the other.
    """
    page = music_page((("起风了", ""), ("海阔天空", "")))
    page.music_catalog = [
        _song("1111111111111111111", "起风了"),
        _song(REF["music_id"], "海阔天空"),
    ]

    result = await dp._set_music(page, job(music="海阔天空", ref_payload=None), Deadline(10))

    assert result["music_match"] == "exact"
    assert "music_match_by" not in result
    assert f'[{dp.MUSIC_ROW_ATTRIBUTE}="1"]' in page.clicks
