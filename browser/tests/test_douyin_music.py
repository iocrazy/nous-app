"""Picking a post's background music by name (「选择音乐」).

Three layers, and the split is deliberate. **None of them runs against a live
page** — the dialog only ever appears part-way through a real publish — so what
each layer pins is stated exactly:

1. **Pure matching** (`judge_music_choice`) — which of the titles the dialog
   listed gets clicked. This is where the "exact first, otherwise the first
   result, and say so" policy lives, and it is testable exhaustively without a
   DOM.
2. **The step** (`_set_music`) driven through `tests.fakes.FakePage` — the
   refusals. Every one of them exists because the alternative is a post that
   went out silently music-less, which looks exactly like a successful publish.
3. **The dialog's shape**, as an HTML fixture (`tests.dom_fixture`) built to
   what the T0 survey read off the live editor. It pins the one measurement
   that changes how the code is written: 「选择音乐」 matches **twice** there, so
   `.first` — what every other entry point in `douyin_publish` uses — is a coin
   flip here. ⚠️ The fixture cannot run the row probe: that is JavaScript and
   this shim has no engine. The probe's own logic is therefore unproven; what
   is proven is what happens when it comes back empty or wrong.

⚠️ So: none of this proves the selectors are right. What it does prove is that
every way of being wrong about them lands on a typed failure the user sees,
rather than on a published post with no music on it.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.platforms import douyin_publish as dp
from app.publish import Deadline, PublishJob
from app.schemas import MediaItem, PublishIntent, SessionStatus
from tests import dom_fixture
from tests.fakes import FakePage

pytestmark = pytest.mark.unit

EDITOR_URL = "https://creator.douyin.com/creator-micro/content/post/video"
MUSIC_ENTRY = f"text={dp.MUSIC_ENTRY_TEXT}"
MUSIC_SEARCH = dp.MUSIC_SEARCH_INPUT_SELECTORS[0]


def row_selector(index: int) -> str:
    return f'[{dp.MUSIC_ROW_ATTRIBUTE}="{index}"]'


# Captured at import, BEFORE the shrinking fixture below can touch them, so the
# production values stay assertable. A test suite that only ever runs the 60 ms
# version cannot notice the day someone ships the 60 ms version.
SHIPPED_MUSIC_READY_TIMEOUT_MS = dp.MUSIC_READY_TIMEOUT_MS
SHIPPED_MUSIC_READY_POLL_MS = dp.MUSIC_READY_POLL_MS


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setenv("BROWSER_PUBLISH_POLL_INTERVAL_S", "0.2")
    # Resolved at call time by `wait_for_music_results`, which is what lets a
    # test shrink them without the shrunk value becoming production.
    monkeypatch.setattr(dp, "MUSIC_READY_TIMEOUT_MS", 60)
    monkeypatch.setattr(dp, "MUSIC_READY_POLL_MS", 1)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def job(music: str | None = None) -> PublishJob:
    from app.assets import StagedAsset

    options = {} if music is None else {"music": music}
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


def music_page(
    *,
    rows=("Dream It Possible", "Dream It Possible (Live)"),
    entry_count: int = 2,
    opens_on: int | None = 0,
    closes: bool = True,
    search_present: bool = True,
    mentions_after_pick: int = 1,
    baseline_mentions: int = 0,
) -> FakePage:
    """A page whose music dialog behaves as scripted.

    `opens_on` is which of the 「选择音乐」 nodes actually opens the dialog —
    `None` for "none of them does". That parameter *is* the T0 measurement: the
    string matches twice, and which one is the button is not ours to know.
    """

    def visible(page: FakePage) -> set[str]:
        state = {dp.TITLE_INPUT_SELECTOR}
        opened = opens_on is not None and (MUSIC_ENTRY, opens_on) in page.click_targets
        row_clicked = any(
            selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in page.clicks
        )
        if opened and search_present and not (closes and row_clicked):
            state.add(MUSIC_SEARCH)
        return state

    page = FakePage(url=EDITOR_URL, visible=visible)
    page.counts[MUSIC_ENTRY] = entry_count
    page.music_rows = tuple(rows)

    def mentions(pg: FakePage, needle: str) -> int:
        row_clicked = any(
            selector.startswith(f"[{dp.MUSIC_ROW_ATTRIBUTE}=") for selector in pg.clicks
        )
        if row_clicked and needle in rows:
            return baseline_mentions + mentions_after_pick
        return baseline_mentions

    page.music_mentions = mentions
    return page


# --- pure: which row gets clicked -------------------------------------------


def test_an_exact_title_wins_over_an_earlier_near_miss():
    """The platform's search is fuzzy and puts its own idea of relevance first.
    A title that matches character for character is the user's answer, wherever
    it landed in the list."""
    choice = dp.judge_music_choice(
        "起风了", ["起风了 (Cover)", "起风了", "起风了 DJ版"]
    )
    assert (choice.match, choice.name, choice.index) == ("exact", "起风了", 1)


def test_case_and_whitespace_are_not_a_disagreement_about_which_song():
    choice = dp.judge_music_choice("dream  it possible", ["Dream It Possible"])
    assert choice.match == "exact"
    assert choice.name == "Dream It Possible"


def test_no_exact_title_takes_the_first_result_and_says_it_was_approximate():
    """The opposite of the collection step, on purpose: a wrong collection is
    worse than none (nobody re-checks filing), while music the user did not get
    is the failure the field exists to prevent. The approximation is named, not
    hidden — `name` is what the caller reports back."""
    choice = dp.judge_music_choice("起风了", ["起风了 (Cover)", "unrelated"])
    assert (choice.match, choice.name, choice.index) == (
        "approximate",
        "起风了 (Cover)",
        0,
    )


def test_an_empty_result_list_selects_nothing():
    choice = dp.judge_music_choice("起风了", [])
    assert (choice.match, choice.name, choice.index) == ("none", None, None)


def test_blank_rows_are_skipped_without_shifting_the_indices():
    """The index addresses a DOM row. Renumbering a filtered list would click
    the row next to the chosen one — a wrong track that looks like a right
    one."""
    choice = dp.judge_music_choice("起风了", ["", "  ", "起风了"])
    assert choice.index == 2


def test_punctuation_still_separates_two_uploads_of_the_same_title():
    """Deliberately NOT folded: 「(Live)」 and 「(Cover)」 are different uploads,
    and treating them as noise would turn an exact match into a silent near
    one."""
    assert dp.canonical_music("起风了 (Live)") != dp.canonical_music("起风了")


# --- the step: every way of being wrong is visible ---------------------------


async def test_music_that_was_not_asked_for_never_opens_the_dialog():
    page = music_page()
    assert await dp._set_music(page, job(), Deadline(5)) == {"music": "not_requested"}
    assert page.clicks == []


async def test_the_entry_is_tried_until_the_dialog_opens():
    """[实测 2026-08-12] 「选择音乐」 has exact=2 — the block heading and the
    button. `.first` (what every other entry point in the module uses) opens
    the dialog only half the time, and the other half is indistinguishable from
    "the control is gone"."""
    page = music_page(opens_on=1)
    result = await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert result["music"] == "applied"
    assert result["music_entry_index"] == 1
    # Both were tried, in order, and the second is the one that worked.
    assert (MUSIC_ENTRY, 0) in page.click_targets
    assert (MUSIC_ENTRY, 1) in page.click_targets


async def test_a_dialog_that_never_opens_fails_rather_than_publishing_silent():
    page = music_page(opens_on=None)
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("Dream It Possible"), Deadline(5))

    assert excinfo.value.detail["reason"] == "music_entry_missing"
    assert excinfo.value.detail["requested_music"] == "Dream It Possible"
    assert excinfo.value.status is SessionStatus.FAILED


async def test_the_requested_name_is_typed_into_the_dialogs_search_box():
    page = music_page()
    await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert (MUSIC_SEARCH, "Dream It Possible") in page.fills
    assert "Enter" in page.keyboard.pressed


# --- waiting for the search to answer ---------------------------------------
#
# [实测 2026-08-17, 生产库] The step used to press Enter, sleep
# `publish_settle_ms` (1 500 ms) and read once. The same day's logs put the
# catalogue search at 1.2–2.5 s, so the read landed mid-search and the user was
# told the platform does not have his song. `rows=?` did NOT fire — we really
# looked, and the dialog really was empty *at that instant*.


def slow_page(*, appears_after: int, rows=("Dream It Possible",)) -> FakePage:
    """A dialog whose results render only after `appears_after` readings.

    Latency expressed in probe calls rather than seconds: the fake has no
    clock, and a test that slept for real would be pinning the machine it runs
    on rather than the code.
    """
    calls = {"n": 0}

    def scripted(_page):
        calls["n"] += 1
        return tuple(rows) if calls["n"] > appears_after else ()

    page = music_page(rows=rows)
    page.music_rows = scripted
    return page


async def test_the_dialog_gets_time_to_answer_before_its_silence_is_believed():
    """**The incident, reproduced forwards.** Results that arrive after the old
    fixed settle are found now instead of being reported as "the platform does
    not have this track".

    Against the 1 500 ms-and-read version this is red: that one reads once, at
    a moment when the scripted dialog is still empty, and raises
    `music_not_found`.
    """
    page = slow_page(appears_after=3)
    result = await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert result["music"] == "applied"
    assert result["music_selected"] == "Dream It Possible"


async def test_never_seeing_the_results_is_not_the_platform_saying_no():
    """A wait that ran out says "we did not read the list", never "the list did
    not have it". The two need opposite responses — one is worth retrying and
    the other is not — so they are different reasons, and this asserts the
    counterfactual rather than only the new value."""
    page = slow_page(appears_after=10_000)
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_results_not_seen"
    assert excinfo.value.detail["reason"] != "music_not_found"
    assert excinfo.value.status is SessionStatus.TIMEOUT


async def test_a_list_that_never_changed_is_not_this_searchs_answer():
    """The dialog may keep showing what it had while the search runs. "Some
    rows are on screen" is therefore not "the search answered", and a step that
    reads the leftovers is reading a list this query never produced.

    The fake here holds one list from before Enter to well past it. Readiness
    requires a list the pre-search stamp never touched (or a different text
    hash), so this times out — and because only the *approximate* fallback
    matched, the leftovers are refused rather than published. Against the
    read-once version this is red in the worst possible way: it publishes
    「Some Other Song」 as the closest match and reports success.
    """
    page = music_page(rows=("Some Other Song",))
    page.music_probe = lambda _page, _stamp: {
        "anchors": 1,
        # Nothing new: this anchor was already stamped before the search.
        "fresh": 0,
        "leaves": 41,
        "sig": 777,
        "textlen": 902,
    }
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_results_not_seen"


async def test_a_relisted_search_is_accepted_even_when_the_nodes_are_reused():
    """The second route to "this is a new list", and it exists because the
    first one can fail honestly: a UI that re-renders text into the SAME DOM
    nodes keeps our stamp, so `fresh` stays 0 forever. The anchors' text hash
    moving says the list changed anyway. Without it, a node-reusing dialog
    could never become ready and music would be unpublishable.

    Asserted against `wait_for_music_results` rather than through `_set_music`:
    driven end-to-end, an *exact* title match is accepted whether or not the
    wait succeeded (by design — see `_set_music`), so the whole step would go
    green with this route deleted. That is the shape of false green this
    session has now hit repeatedly: the assertion held for a different reason
    than the one under test.
    """
    before = dp.MusicProbe(anchors=1, fresh=0, leaves=41, sig=111, text_len=902)
    page = music_page()
    # Same node count, same (zero) freshness — only the content moved.
    page.music_probe = lambda _page, _stamp: {
        "anchors": 1,
        "fresh": 0,
        "leaves": 41,
        "sig": 222,
        "textlen": 902,
    }

    readiness = await dp.wait_for_music_results(
        page, before, timeout_ms=50, poll_ms=1
    )

    assert readiness.ready is True
    assert readiness.reason == "results"


async def test_the_same_list_twice_is_never_ready():
    """The counterfactual of the test above, and the reason the hash is not
    simply "has the page changed": an identical reading is the dialog's
    leftovers, and accepting it is reading a list this search never produced.
    """
    same = dp.MusicProbe(anchors=1, fresh=0, leaves=41, sig=111, text_len=902)
    page = music_page()
    page.music_probe = lambda _page, _stamp: {
        "anchors": 1,
        "fresh": 0,
        "leaves": 41,
        "sig": 111,
        "textlen": 902,
    }

    readiness = await dp.wait_for_music_results(page, same, timeout_ms=50, poll_ms=1)

    assert readiness.ready is False


async def test_half_a_rendered_list_is_not_a_list():
    """Stability across two consecutive readings. A list still growing is a
    list a song can be missing from — the same false negative by a different
    route, and the one #1862 hit on the read-back."""
    seq = {"n": 0}

    def never_settles(_page, _stamp):
        # One more row every time we look: always new, never the same twice.
        seq["n"] += 1
        return {
            "anchors": seq["n"],
            "fresh": seq["n"],
            "leaves": 30 + seq["n"],
            "sig": seq["n"],
            "textlen": 500 + seq["n"],
        }

    page = music_page()
    page.music_probe = never_settles
    readiness = await dp.wait_for_music_results(
        page,
        dp.MusicProbe(anchors=0, fresh=0, leaves=20, sig=0, text_len=400),
        timeout_ms=5,
        poll_ms=1,
    )

    assert readiness.ready is False
    assert readiness.reason == "timeout"


async def test_rows_zero_now_says_whether_the_dialog_painted_anything():
    """The residual ambiguity #1865 left behind. `rows=0` meant two unrelated
    things — an empty dialog, and a dialog whose 「N人使用」 copy no longer
    matches our anchor — and a production failure could not be attributed to
    either.

    They are different strings now. Nothing but counts goes into them.
    """
    empty = dp.MusicReadiness(
        False,
        "timeout",
        12_000,
        dp.MusicProbe(anchors=0, fresh=0, leaves=18, sig=0, text_len=210),
        dp.MusicProbe(anchors=0, fresh=0, leaves=18, sig=0, text_len=210),
    )
    copy_moved = dp.MusicReadiness(
        False,
        "timeout",
        12_000,
        dp.MusicProbe(anchors=0, fresh=0, leaves=18, sig=0, text_len=210),
        dp.MusicProbe(anchors=0, fresh=0, leaves=96, sig=0, text_len=1840),
    )
    read = dp.MusicRowsRead([])

    assert dp.describe_music_rows(read, empty) != dp.describe_music_rows(read, copy_moved)
    assert "leaves=18/96" in dp.describe_music_rows(read, copy_moved)
    assert "txt=210/1840" in dp.describe_music_rows(read, copy_moved)


async def test_an_unobservable_dialog_renders_question_marks_not_zeroes():
    """`?` is not `0`. A probe that failed did not see an empty dialog; it saw
    nothing at all, and printing `anchors=0` would be the same lie this repo
    has now removed in four places."""
    blind = dp.MusicProbe(error="TypeError")
    clause = dp.describe_music_rows(
        dp.MusicRowsRead([]),
        dp.MusicReadiness(False, "timeout", 900, blind, blind),
    )
    assert "anchors=?/?" in clause
    assert "anchors=0" not in clause


def test_the_two_music_probes_share_one_anchor():
    """Both probes look for 「N人使用」, and they must look for the SAME thing.
    Two literal copies is how the readiness probe goes on reporting "the list
    is there" about a pattern the row reader no longer matches — the two would
    then disagree about one page, which is the very ambiguity the readiness
    counts exist to remove."""
    assert dp._MUSIC_USAGE_JS in dp._MUSIC_ROWS_JS
    assert dp._MUSIC_USAGE_JS in dp._MUSIC_READY_JS
    # And neither carries a second, hand-written copy of it.
    assert dp._MUSIC_ROWS_JS.count("人使用") == 1
    assert dp._MUSIC_READY_JS.count("人使用") == 1


def test_the_python_usage_reader_matches_the_same_copy_the_probes_anchor_on():
    """**The guard**, and the third party to that same agreement.

    `parse_music_usage` reads the count out of the very line the probes select
    rows BY. A hand-rolled second regex on the Python side would drift exactly
    like a second copy in the JS does — except worse, because the failure is
    silent and inverted: every row would still be found, and every row's count
    would render `?`, which reads as "the platform stopped publishing usage
    counts" rather than as "our parser is stale".
    """
    assert dp.MUSIC_USAGE_PATTERN in dp._MUSIC_USAGE_JS
    assert dp._MUSIC_USAGE_RE.pattern == dp.MUSIC_USAGE_PATTERN
    # Not merely equal strings: the SAME object, so a second literal cannot be
    # slipped in beside it and stay green.
    assert dp._MUSIC_USAGE_RE.pattern is dp.MUSIC_USAGE_PATTERN
    # And the one pattern really does read a real line, both ways.
    assert dp.parse_music_usage("1.2万人使用") == "1.2万"


def test_the_shipped_music_wait_is_not_the_shrunk_test_value():
    """The fixture above shrinks these to 60 ms/1 ms. Pinned separately so the
    shrink cannot be the thing that ships — and bounded well under the
    per-publish budget so a music miss cannot eat the post's time."""
    assert SHIPPED_MUSIC_READY_TIMEOUT_MS == 12_000
    assert SHIPPED_MUSIC_READY_POLL_MS == 400
    assert SHIPPED_MUSIC_READY_TIMEOUT_MS > 2_500  # the measured search latency
    assert SHIPPED_MUSIC_READY_TIMEOUT_MS < 60_000


async def test_a_search_that_comes_back_empty_is_a_typed_failure():
    """**The guard.** A track the platform does not have must not become a post
    published on 原声 while the batch reports success — the user asked for
    music precisely because that outcome is worse, and nothing downstream
    re-reads `detail` looking for a note.

    Delete the raise this asserts on and this test goes red.

    ⚠️ The *reason* changed on 2026-08-17 and the change is the point: a dialog
    that lists nothing and one that has not answered yet are the same page to
    us, and we hold no measured copy for the platform's own "no results" state.
    Reporting the union as `music_not_found` is the assertion this whole fix
    exists to stop making. What the guard actually guarantees — a typed
    failure, never a silent 原声 publish — is asserted directly below, and
    survives whichever of the two reasons is right.
    """
    page = music_page(rows=())
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("A Track Nobody Uploaded"), Deadline(10))

    assert excinfo.value.detail["stage"] == "music"
    assert excinfo.value.detail["requested_music"] == "A Track Nobody Uploaded"
    assert excinfo.value.detail["reason"] == "music_results_not_seen"


async def test_an_approximate_pick_reports_the_track_it_actually_selected():
    page = music_page(rows=("起风了 (Cover)", "something else"))
    result = await dp._set_music(page, job("起风了"), Deadline(10))

    assert result["music_match"] == "approximate"
    assert result["music_requested"] == "起风了"
    assert result["music_selected"] == "起风了 (Cover)"


async def test_a_dialog_that_stays_open_is_not_treated_as_a_selection():
    page = music_page(closes=False)
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_dialog_stuck"
    assert excinfo.value.detail["music_selected"] == "Dream It Possible"


async def test_a_click_that_the_editor_never_echoes_back_is_not_a_selection():
    """**The read-back guard.** Clicking a row is idempotent: a click that
    landed on nothing looks exactly like one that worked (the trap
    `_set_download_toggle` documents). The evidence is the editor naming the
    track afterwards.

    Remove the read-back and this test goes red — the step would report
    `applied` for a page that never registered anything.
    """
    page = music_page(mentions_after_pick=0)
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_not_confirmed"
    assert excinfo.value.detail["music_selected"] == "Dream It Possible"


async def test_a_title_that_already_contains_the_track_name_is_not_evidence():
    """The read-back is a *comparison*, not a presence check.

    A user publishing a post titled "Dream It Possible" would satisfy "is that
    string on the page" without any music having been chosen. The baseline is
    taken before the dialog opens, so only a NEW mention counts.
    """
    page = music_page(baseline_mentions=1, mentions_after_pick=0)
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("Dream It Possible"), Deadline(10))

    assert excinfo.value.detail["reason"] == "music_not_confirmed"


async def test_a_dialog_without_a_search_box_fails_at_the_music_stage():
    """The search box IS the open-dialog signal, so "it opened but has no
    search box" and "it never opened" are the same observation — reported as
    one reason rather than two, and both at `stage: music`.

    That attribution is the point: the cover dialog once stayed open and failed
    the *declaration* step two steps later, and the investigation went to a
    perfectly healthy step.
    """
    page = music_page(search_present=False)
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_music(page, job("Dream It Possible"), Deadline(5))

    assert excinfo.value.detail["stage"] == "music"
    assert excinfo.value.detail["reason"] == "music_entry_missing"


async def test_a_non_string_music_option_is_reported_rather_than_ignored():
    """Same treatment as the other platform options: a caller sending a bool
    has a bug, and answering it with "no music requested" hides that bug behind
    a post that went out on 原声."""
    options = dp.read_platform_options({"music": True})
    assert options.music is None
    assert "music" in options.bad_types


# --- the dialog's real shape ------------------------------------------------
#
# An HTML fixture built to the T0 survey's description, driven through the
# same shim the read-back tests use. It cannot run the row probe (that is
# JavaScript, and this shim has no engine) — what it *can* do is pin the
# structural facts the production code is written around, so that a wrong
# assumption about the shape shows up here rather than on a live account.

EDITOR_HTML = """
<div class="post-editor">
  <div class="music-block">
    <span class="label">选择音乐</span>
    <span class="aside">点击添加合适作品风格音乐</span>
    <button class="entry"><span>选择音乐</span></button>
    <div class="preview">HEYGO创作的原声</div>
  </div>
</div>
"""

DIALOG_HTML = """
<div class="post-editor">
  <div class="music-block">
    <span class="label">选择音乐</span>
    <span class="aside">点击添加合适作品风格音乐</span>
    <button class="entry"><span>选择音乐</span></button>
  </div>
  <div class="modal" role="dialog">
    <div class="modal-title">选择音乐</div>
    <input placeholder="搜索音乐" />
    <div class="tabs">
      <span>推荐</span><span>热门榜</span><span>收藏</span>
      <span>飙升榜</span><span>原创榜</span><span>卡点</span>
    </div>
    <div class="list">
      <div class="item" data-nous-music-row="0">
        <div class="name">Dream It Possible</div>
        <div class="meta">Delacey·03:41</div>
        <div class="usage">12.3万人使用</div>
      </div>
      <div class="item" data-nous-music-row="1">
        <div class="name">Dream It Possible (Live)</div>
        <div class="meta">Delacey·04:02</div>
        <div class="usage">8万人使用</div>
      </div>
    </div>
  </div>
</div>
"""


async def test_the_entry_string_really_does_match_twice_on_this_page():
    """The measurement the candidate loop exists for. If this ever returns 1,
    the loop is dead weight; if it returns 2, `.first` is a coin flip."""
    page = dom_fixture.FakePage(EDITOR_HTML, url=EDITOR_URL)
    assert await page.get_by_text(dp.MUSIC_ENTRY_TEXT, exact=True).count() == 2


async def test_the_open_dialog_is_recognised_by_its_search_box_not_its_title():
    """With the dialog up, 「选择音乐」 matches a *third* time (the modal's own
    title), so the entry count cannot double as "is it open". The search box's
    placeholder is the handle, and it is unique."""
    page = dom_fixture.FakePage(DIALOG_HTML, url=EDITOR_URL)
    assert await page.get_by_text(dp.MUSIC_ENTRY_TEXT, exact=True).count() == 3
    assert await page.locator(dp.MUSIC_SEARCH_INPUT_SELECTORS[0]).count() == 1


async def test_each_result_row_carries_the_usage_line_the_row_probe_anchors_on():
    """The probe has no class names to work from (none were measured), so it
    finds rows by the one string T0 read on every one of them. This asserts the
    fixture's rows really are shaped that way — the assumption, stated where it
    can fail."""
    page = dom_fixture.FakePage(DIALOG_HTML, url=EDITOR_URL)
    rows = page.locator('[data-nous-music-row="0"]')
    text = await rows.first.inner_text()
    lines = [line for line in text.split("\n") if line.strip()]

    # First line is the title — what the probe reports as the row's name.
    assert lines[0] == "Dream It Possible"
    # And the anchor the probe searches for is on the row, once.
    assert any("人使用" in line for line in lines)


async def test_the_row_attribute_addresses_exactly_one_row():
    """Why the probe stamps an index instead of the driver clicking the song's
    text: the two rows here differ only by a suffix, and 「Dream It Possible」 is
    a substring of 「Dream It Possible (Live)」."""
    page = dom_fixture.FakePage(DIALOG_HTML, url=EDITOR_URL)
    assert await page.locator(f'[{dp.MUSIC_ROW_ATTRIBUTE}="1"]').count() == 1
    # ...whereas the song's own text does not: a substring match would resolve
    # to both rows, and an exact one only works because this fixture happens to
    # render the title on a leaf of its own.
    assert await page.get_by_text("Dream It Possible", exact=False).count() == 2
