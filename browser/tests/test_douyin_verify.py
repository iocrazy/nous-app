"""Publish read-back (P1-3): fixture-HTML parsing + the pure judgement.

Two layers, and the split is deliberate:

* `TestAgainstFixtureHtml` runs the REAL reader (`read_work_cards`,
  `list_is_empty`, `verify_publish`) over real markup via `dom_fixture`. This
  is the evidence that the DOM half works — selector choice, nested-wrapper
  de-duplication, item-id extraction off the anchor.
* the rest pin the pure judgement, including the cases the fixture cannot
  stage (a title that matches two cards, a status word we do not know).

[实测 2026-08-11] The fixture is no longer a guess about the live console's
shape: it is modelled on counts taken from a bound account (12 works, 5 of them
image posts, 6 nodes per card, zero anchors page-wide). See the fixture's own
header for what was measured and what is still modelled — the status words
other than 「已发布」 are still the latter.

What these tests cannot prove is that the vocabulary for the states nobody has
seen is right. What they DO prove is the property that makes the unknown
survivable: when the shape is wrong, the answer is `list_unreadable`
(inconclusive), never a false "live" and never a false "deleted".
`test_unknown_markup_is_inconclusive_not_a_verdict` is that test.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

from app.platforms import douyin_verify
from app.platforms.douyin_verify import (
    CARD_SELECTORS,
    OPERATION_MARKERS,
    Readiness,
    has_work_evidence,
    wait_for_works_list,
    LIVE_MARKERS,
    ListProbe,
    PageProbe,
    WorkCard,
    WorkState,
    build_published_url,
    caption_lines,
    card_lines,
    classify_work_state,
    extract_item_id,
    judge_readback,
    list_is_empty,
    match_cards,
    measure_network,
    measure_page,
    measure_page_probes,
    measure_render,
    RenderProbe,
    normalize_title,
    outermost_only,
    page_label,
    probe_label,
    read_work_cards,
    read_works_list,
    verify_publish,
)
from app.verify import ReadbackVerdict, response_for_judgement
from app.schemas import SessionStatus

from tests.dom_fixture import FakePage, UnsupportedSelector

pytestmark = pytest.mark.unit

# Captured at import, BEFORE the autouse fixture below shrinks them, so the
# "what actually ships" test can still see the real values.
SHIPPED_READY_TIMEOUT_MS = douyin_verify.READY_TIMEOUT_MS
SHIPPED_READY_POLL_MS = douyin_verify.READY_POLL_MS


@pytest.fixture(autouse=True)
def _fast_readiness_wait(monkeypatch):
    """Shrink the readiness bounds for every test in this file.

    `FakePage` is a static document, so a page that is not ready never becomes
    ready and the real 20-second timeout would be spent in full, on every
    negative test. Shrunk here rather than passed at each call site so that
    `verify_publish` — which takes only `(page, title)`, because that is the
    shape `VerifySpec` calls — is still exercised through its real signature.

    `test_the_shipped_bounds_are_sane` pins the production values, so this
    fixture cannot quietly become the thing that ships.
    """
    monkeypatch.setattr(douyin_verify, "READY_TIMEOUT_MS", 60)
    monkeypatch.setattr(douyin_verify, "READY_POLL_MS", 5)

FIXTURE = (Path(__file__).parent / "fixtures" / "douyin_works_manage.html").read_text(
    encoding="utf-8"
)

EMPTY_PAGE = """
<div class="content-list-wrap">
  <div class="empty-state-z9y8"><div class="tip-a1">暂无作品</div></div>
</div>
"""

# Markup whose card roots match NONE of `CARD_SELECTORS` — what a console
# redesign looks like from here.
REDESIGNED_PAGE = """
<main class="feed">
  <article class="post-tile"><h3>Autumn Harvest Field Notes</h3><span>已发布</span></article>
  <article class="post-tile"><h3>Winter Kitchen Experiment</h3><span>审核中</span></article>
</main>
"""


class TestAgainstFixtureHtml:
    """The real reader over real markup."""

    async def test_reads_every_card_once(self):
        cards = await read_work_cards(FakePage(FIXTURE))
        assert len(cards) == 12, [c.text[:40] for c in cards]

    async def test_one_match_is_one_work_not_one_node(self):
        """**The bug this fixture was rebuilt for.**

        [实测 2026-08-11] the live console builds each card from a root plus
        five descendants carrying the same class prefix, so the raw selector
        resolves to 6 nodes per work. Reading those nodes directly spent
        `MAX_CARDS` on the first four works and judged every later one
        `not_found` — a false "your post is gone", the one verdict this module
        promises never to invent.
        """
        page = FakePage(FIXTURE)
        raw = await page.locator('[class*="video-card"]').count()
        assert raw == 72, "fixture must reproduce the measured 6-nodes-per-card"
        cards = await read_work_cards(page)
        assert len(cards) == 12
        # Every card read must be a whole work, not a fragment of one: the
        # status word and the caption have to travel together, because the
        # verdict is read off the same string the title matched. A cover node
        # or a caption node on its own classifies UNKNOWN.
        assert all(
            classify_work_state(c.text) is not WorkState.UNKNOWN for c in cards
        ), [c.text[:40] for c in cards]

    async def test_the_enclosing_list_wrapper_is_not_read_as_a_card(self):
        """The list sits in a wrapper whose class contains "card" ([实测
        2026-08-11]). Reading THAT as a card would put every work's text in one
        string and let one post's status answer for another's."""
        cards = await read_work_cards(FakePage(FIXTURE))
        assert not any("Gorge Ridge" in c.text and "Winter Kitchen" in c.text
                       for c in cards)

    async def test_a_live_card_with_no_link_still_verifies(self):
        """**The shape the live console is believed to actually have.**

        Checked against a real account on 2026-08-08 (recorded in
        RecordsPage.tsx): the manage page's cards carry no href, no id
        attribute, and no listing XHR returns one. The verdict must therefore
        come from the status text alone — `published_url` is opportunistic and
        stays null, which is a documented limit, not a failure.
        """
        judgement = await verify_publish(
            FakePage(FIXTURE), "Linkless Live Card From The Real Console"
        )
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.item_id is None
        assert judgement.published_url is None

    async def test_item_id_comes_off_the_watch_link(self):
        """The opportunistic path, kept alive against a console that does not
        currently use it — [实测 2026-08-11] the live page has zero anchors."""
        cards = await read_work_cards(FakePage(FIXTURE))
        anchored = next(c for c in cards if "Anchored Card" in c.text)
        assert anchored.item_id == "7412345678901234567"
        # The card next to it has no link, and that must read as "no id", not
        # as "borrow the neighbour's".
        linkless = next(c for c in cards if "Linkless Live Card" in c.text)
        assert linkless.item_id is None

    async def test_live_post_verifies_with_a_url(self):
        judgement = await verify_publish(
            FakePage(FIXTURE), "Anchored Card From A Hypothetical Console"
        )
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.reason == "live"
        assert judgement.published_url == (
            "https://www.douyin.com/video/7412345678901234567"
        )
        assert judgement.detail["cards_seen"] == 12

    async def test_private_post_counts_as_live(self):
        """仅自己可见 is a visibility the batch can legitimately request. If
        this ever flips to not_live, every private publish gets blocked."""
        judgement = await verify_publish(
            FakePage(FIXTURE), "Private Draft Reference Cut"
        )
        assert judgement.verdict is ReadbackVerdict.LIVE

    @pytest.mark.parametrize(
        "title,reason",
        [
            ("Winter Kitchen Experiment", "under_review"),
            ("Studio Tour Walkthrough", "rejected"),
            ("Spring Planting Timelapse", "still_scheduled"),
        ],
    )
    async def test_not_live_states_are_typed(self, title, reason):
        judgement = await verify_publish(FakePage(FIXTURE), title)
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == reason

    async def test_deleted_post_reads_as_not_found(self):
        """The user's acceptance case: publish, then delete it on the platform.
        The list still renders, our post simply is not on it."""
        judgement = await verify_publish(FakePage(FIXTURE), "A Post That Was Deleted")
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "not_found"
        assert judgement.detail["cards_seen"] == 12

    async def test_empty_account_reads_as_not_found(self):
        page = FakePage(EMPTY_PAGE)
        assert await list_is_empty(page) is True
        judgement = await verify_publish(page, "Anything At All")
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "not_found"
        assert judgement.detail["list_empty"] is True

    async def test_unknown_markup_is_inconclusive_not_a_verdict(self):
        """**The safety property.** A console redesign must cost a "please
        check this", never a false "it was deleted" — the read-back learned
        nothing, and saying nothing-shaped-as-something is the whole failure
        this module exists to avoid.

        The reason is now `list_not_ready` rather than `list_unreadable`: a
        page whose cards never appear also never satisfies the readiness
        signal, so the wait times out first. Both are INCONCLUSIVE, which is
        the property that matters, and the new code says the more specific of
        the two true things.
        """
        page = FakePage(REDESIGNED_PAGE)
        assert await read_work_cards(page) == []
        assert await list_is_empty(page) is False
        judgement = await verify_publish(page, "Autumn Harvest Field Notes")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.reason == "list_not_ready"


# A console that nests its card roots inside a container matching the SAME
# selector. Not observed — see `outermost_only`'s residual-hazard note.
WRAPPED_PAGE = """
<div class="video-card-list-zz01">
  <div class="video-card-a1"><div class="video-card-info-a1">
    Refused Neighbour Post<br/>未通过</div></div>
  <div class="video-card-b2"><div class="video-card-info-b2">
    Perfectly Fine Post<br/>已发布</div></div>
</div>
"""


class TestOutermostScoping:
    def test_it_builds_the_form_the_live_engine_answered(self):
        """Pinned literally because the string is the contract with Playwright:
        [实测 2026-08-11] this exact form returned 12 against the live console
        where the unscoped selector returned 72."""
        assert outermost_only('[class*="video-card"]') == (
            '[class*="video-card"]:not([class*="video-card"] *)'
        )

    @pytest.mark.parametrize(
        "selector",
        [
            '[class*="video-card"]:not(.sidebar)',  # :not() we did NOT implement
            "div > span",  # child combinator
            "div:nth-child(2)",
            ".video-card",  # bare class selector
        ],
    )
    def test_the_shim_still_refuses_selectors_it_did_not_implement(self, selector):
        """Adding one `:not()` form to `dom_fixture` must not turn it into a
        shim that guesses. A selector it does not understand has to raise —
        silently returning "no matches" would make a broken reader look like a
        page with no cards, i.e. turn a failing test green.
        """
        with pytest.raises(UnsupportedSelector):
            FakePage(FIXTURE).locator(selector)

    async def test_known_gap_a_matching_list_wrapper_collapses_to_one_card(self):
        """**A documented limitation, pinned so it cannot drift unnoticed.**

        If a console ever wraps the list in a node matching the same selector,
        the outermost match is that wrapper and one "card" carries every work's
        text — so one post's status answers for another's. Below, a perfectly
        live post reads as refused.

        It is recorded rather than fixed because (a) the live page does not do
        this for the selector that wins, (b) the exposure predates this change
        (`[class^="card-"]` had it), and (c) the direction is still a blocked
        work item a human looks at, never a silent "done".
        """
        cards = await read_work_cards(FakePage(WRAPPED_PAGE))
        assert len(cards) == 1
        judgement = await verify_publish(FakePage(WRAPPED_PAGE), "Perfectly Fine Post")
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "rejected"
        assert judgement.verdict is not ReadbackVerdict.LIVE


class TestImagePosts:
    """图文 works, against the fixture built from the live counts.

    There is deliberately no image-specific code path to test. [实测
    2026-08-11] the console lists image posts and videos in ONE table, built
    from the same `video-card-…` component and labelled with the same status
    vocabulary; the only differences are cosmetic (a `{N}张` badge instead of a
    duration, and 划走率 / 文案展开率 / 平均浏览图片数 instead of 完播率 /
    2秒跳出率). So what these tests pin is that the reader stays content-type
    blind — and that the thing which DID break image posts, reading nodes
    instead of works, stays fixed.
    """

    @pytest.mark.parametrize(
        "title",
        [
            "Gorge Ridge Solo Walk",  # 4张,  position 8
            "Snowline Pilgrim Road",  # 6张,  position 9
            "Red Cabin Mountain Retreat",  # 11张, position 10
            "Old Album Rediscovered",  # 5张,  position 11
        ],
    )
    async def test_image_posts_deep_in_the_list_verify(self, title):
        """On the live account the image posts sat at positions 8-11 — behind
        42 of the 72 nodes. Any regression to node-counting puts all four out
        of reach and reports them as deleted."""
        judgement = await verify_publish(FakePage(FIXTURE), title)
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.reason == "live"

    async def test_image_post_without_a_count_badge_verifies(self):
        """[实测 2026-08-11] the newest image post rendered with NO 「N张」
        badge (4 badges across 5 image works; cause unknown). Anything keying
        off that badge to recognise an image post would have missed it — which
        is the argument for not keying off it at all."""
        page = FakePage(FIXTURE)
        card = next(
            c for c in await read_work_cards(page) if "Autumn Harvest" in c.text
        )
        assert "张" not in card.text
        assert "平均浏览图片数" in card.text  # it IS an image post
        judgement = await verify_publish(page, "Autumn Harvest Field Notes")
        assert judgement.verdict is ReadbackVerdict.LIVE

    async def test_image_and_video_cards_are_read_from_the_same_list(self):
        cards = await read_work_cards(FakePage(FIXTURE))
        image_cards = [c for c in cards if "平均浏览图片数" in c.text]
        video_cards = [c for c in cards if "完播率" in c.text]
        # All 5 image posts carry the image metric set, as on the live account.
        # Only the published videos carry 完播率 — the fixture's modelled
        # 审核中 / 未通过 / 定时中 cards have no metrics, which is why these
        # two do not add up to 12.
        assert len(image_cards) == 5
        assert len(video_cards) == 3
        assert len(cards) == 12

    def test_image_metric_vocabulary_trips_no_state_marker(self):
        """划走率 / 文案展开率 / 平均浏览图片数 are words only image cards
        carry. None of them may collide with a status marker — a metric label
        voting on the verdict would be silent and wrong."""
        metrics = "划走率 55.63% 文案展开率 0.7% 平均浏览图片数 2 吸粉量 0"
        assert classify_work_state(f"Some Image Post\n{metrics}") is WorkState.UNKNOWN

    def test_an_image_post_in_an_unknown_state_is_not_guessed_at(self):
        """The honest half of the V17 finding: only 「已发布」 was ever seen on
        a real card. If the platform labels an image post with a word we do not
        know, the answer is "ask a human", not a verdict."""
        cards = [WorkCard(text="Gorge Ridge Solo Walk\n图文审核排队中\n划走率 55%")]
        judgement = judge_readback(cards, "Gorge Ridge Solo Walk")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.reason == "unknown_work_state"


class TestTitleNormalisation:
    def test_whitespace_and_case_do_not_break_a_match(self):
        assert normalize_title("  Autumn   Harvest\n") == normalize_title(
            "autumn harvest"
        )

    def test_full_width_space_is_normalised(self):
        assert normalize_title("Autumn　Harvest") == "autumn harvest"

    def test_short_titles_do_not_match_a_longer_caption(self):
        """A two-character title as a substring would match half the account,
        and a false match reports the WRONG post's state as this batch's."""
        cards = [WorkCard(text="测试版本 已发布")]
        assert match_cards(cards, "测试") == []

    def test_empty_title_matches_nothing(self):
        assert match_cards([WorkCard(text="anything 已发布")], "   ") == []


# One card as the reader hands it over: `inner_text` of the whole card root,
# so caption AND status word AND counters, in the order the live console
# renders them ([实测 2026-08-11], recorded in the fixture header — image card
# = 「{N}张」 / caption / operation words / date / status / 播放… / 图文 metrics).
# Captions are English test data (repo rule); the chrome is that read's
# vocabulary.
def _image_card(caption: str, status: str = "已发布") -> str:
    return (
        "2张\n"
        f"{caption}\n"
        "编辑作品\n设置权限\n作品置顶\n删除作品\n"
        f"2025年11月20日 22:30\n{status}\n"
        "播放 12 点赞 0 评论 0 分享 0 收藏 0\n"
        "划走率 44.58% 文案展开率 2.56% 平均浏览图片数 2.2 吸粉量 0"
    )


class TestShortTitles:
    """**Regression: the short-title rule was structurally unsatisfiable.**

    Titles under `_MIN_SUBSTRING_TITLE_LEN` used to be compared for EQUALITY
    against `card.text` — the card's whole rendered text, which the reader's
    own docstring says carries the status word and the counters alongside the
    caption. No caption can equal that string, so every short title resolved to
    `not_found` → NOT_LIVE → a blocked work item, whatever the platform
    actually showed.

    It was not theoretical. A post published with the title `test` went live,
    the user saw it on the platform, and the read-back reported it missing on
    the first attempt. Four-Han-character captions are the same class of title
    and are entirely ordinary.
    """

    def test_the_old_rule_could_never_have_held(self):
        """The root cause, pinned so it cannot be reintroduced as an
        'optimisation': the caption is a strict part of the card's text, so
        equality against the whole card is unsatisfiable by construction."""
        text = _image_card("test")
        assert normalize_title("test") != normalize_title(text)
        assert normalize_title("test") in normalize_title(text)

    def test_a_four_character_title_verifies_on_a_real_shaped_card(self):
        """**The reported case.** Revert `match_cards` and this goes red."""
        judgement = judge_readback([WorkCard(text=_image_card("test"))], "test")
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.reason == "live"

    @pytest.mark.parametrize("caption", ["Fog", "test", "Dusk1"])
    def test_every_length_below_the_substring_floor_verifies(self, caption):
        """Not just the one reported length. Everything under
        `_MIN_SUBSTRING_TITLE_LEN` took the broken branch, and the floor counts
        CHARACTERS — so on the Chinese captions this product is mostly used for
        it swallows four-character titles, an entirely ordinary length, not
        just 「测试」-style noise."""
        cards = [WorkCard(text=_image_card(caption))]
        assert judge_readback(cards, caption).verdict is ReadbackVerdict.LIVE

    def test_a_short_title_still_will_not_borrow_a_neighbours_status(self):
        """The property the old rule was reaching for, kept. Our post is NOT on
        the list; a longer caption containing it is. That must read as missing,
        not as the neighbour's 已发布."""
        cards = [
            WorkCard(text=_image_card("test drive of the new lens")),
            WorkCard(text=_image_card("something else entirely")),
        ]
        judgement = judge_readback(cards, "test")
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "not_found"

    def test_a_short_title_reads_its_own_cards_status_not_a_live_neighbours(self):
        """Multiple cards, only one of them ours, and ours was refused. The
        verdict must come off OUR card — matching loosely here is how a refused
        post gets closed as published."""
        cards = [
            WorkCard(text=_image_card("holiday lantern walk")),
            WorkCard(text=_image_card("test", status="未通过")),
        ]
        judgement = judge_readback(cards, "test")
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "rejected"

    @pytest.mark.parametrize("chrome", ["已发布", "审核中", "编辑作品", "2张"])
    def test_console_chrome_as_a_caption_matches_nothing(self, chrome):
        """A caption that happens to BE a word the console prints on every card
        must not match every card. `已发布` would otherwise pick up whichever
        card is live and close the work item as published — the one direction
        this module promises never to invent."""
        cards = [WorkCard(text=_image_card("holiday lantern walk"))]
        assert match_cards(cards, chrome) == []

    def test_long_titles_are_unaffected_and_still_match_as_substrings(self):
        cards = [WorkCard(text=_image_card("Autumn Harvest Field Notes"))]
        assert len(match_cards(cards, "Autumn Harvest Field Notes")) == 1

    def test_card_lines_splits_before_it_normalises(self):
        """`normalize_title` collapses newlines into spaces, so splitting after
        normalising would always yield exactly one 'line' — the same shape of
        can-never-hold bug this fix removes."""
        assert card_lines("Autumn Harvest\n已发布\n播放 12") == (
            "autumn harvest",
            "已发布",
            "播放 12",
        )
        assert card_lines("  \n\n Sole Line \n") == ("sole line",)

    def test_caption_lines_drops_chrome_but_keeps_the_users_words(self):
        assert caption_lines(_image_card("Autumn Harvest")) == (
            "autumn harvest",
            "2025年11月20日 22:30",
            "播放 12 点赞 0 评论 0 分享 0 收藏 0",
            "划走率 44.58% 文案展开率 2.56% 平均浏览图片数 2.2 吸粉量 0",
        )


class TestListProbeRendering:
    """**The diagnostic must be falsifiable.**

    The read-back's only observable output is `verify_detail`, which
    `publish_readback.verdict_for` builds from `[reason] message` alone — every
    other key of the detail dict is dropped. So these pin two things: that the
    counts reach the message at all, and that a probe which could not measure
    something says so instead of reporting a zero.
    """

    def test_a_failed_measurement_is_not_a_zero(self):
        """`None` renders `?`. A broken probe whose output is shaped like "the
        answer is none" is not evidence — it is the `xvfb_ready` mistake, and
        it is the whole reason these fields are `int | None`."""
        rendered = ListProbe(
            roots=(('[class*="video-card"]', None, None),),
            won=None,
            cards=None,
            title_exact=None,
            op_words=(None, None),
        ).render()
        assert "cards=?" in rendered
        assert "video-card:?/?" in rendered
        assert "title_exact=?" in rendered
        assert "ops=?/?" in rendered
        assert "0" not in rendered

    def test_zero_still_renders_as_zero(self):
        """The other half of the same property: a real zero must not be
        indistinguishable from a failure either."""
        rendered = ListProbe(
            roots=(('[class*="work-card"]', 0, 0),),
            won=None,
            cards=0,
            title_exact=0,
            op_words=(0, 0),
            won_len=0,
            won_lines=0,
            page=PageProbe(
                where="manage",
                ready="cards",
                waited_ms=0,
                text_len=0,
                divs=0,
                login_gate=0,
                login_wide=0,
                works_words=0,
                empty_words=0,
                busy=0,
            ),
        ).render()
        assert "cards=0" in rendered
        assert "work-card:0/0" in rendered
        assert "title_exact=0" in rendered
        # Every field set to a real zero — so a `?` anywhere would mean the
        # renderer invented an unmeasured field, which is the bug this pins.
        assert "?" not in rendered

    def test_it_carries_the_numbers_that_tell_the_hypotheses_apart(self):
        probe = ListProbe(
            roots=(
                ('[class*="content-card"]', 3, 1),
                ('[class*="work-card"]', 0, 0),
                ('[class*="video-card"]', 72, 12),
                ('[class^="card-"]', 2, 2),
            ),
            won='[class*="content-card"]',
            cards=1,
            title_exact=1,
            op_words=(12, 12),
        )
        rendered = probe.render()
        # "an earlier candidate won while the real one had 12 works waiting"
        # has to be readable straight off this line — that is the finding the
        # live account could not be asked about.
        assert "won=content-card" in rendered
        assert "video-card:72/12" in rendered
        assert "cards=1" in rendered
        assert "title_exact=1" in rendered
        assert "ops=12/12" in rendered

    def test_it_never_carries_page_content(self):
        """`verify_detail` lands in the database and in logs, and this repo is
        public. The probe may carry counts and selector literals — never a
        caption, never card text."""
        probe = ListProbe(
            roots=(('[class*="video-card"]', 1, 1),),
            won='[class*="video-card"]',
            cards=1,
            title_exact=1,
            op_words=(1, 1),
        )
        rendered = probe.render()
        assert all(ch not in rendered for ch in "编删作品")
        for token in rendered.replace("[probe ", "").rstrip("]").split():
            assert "=" in token

    def test_an_unlabelled_selector_falls_back_to_its_literal(self):
        """Never a guess: a selector the label regex does not understand is
        printed whole rather than abbreviated into something untrue."""
        assert probe_label('[class*="video-card"]') == "video-card"
        assert probe_label("tbody tr") == "tbody tr"


class TestPageLabel:
    """`page_label` — a LABEL, never the URL. The one question no count could
    answer: `not_found` read off the works page and `not_found` read off some
    other page that merely rendered are the same output today, and they are
    completely different bugs."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://creator.douyin.com/creator-micro/content/manage", "manage"),
            ("https://creator.douyin.com/creator-micro/content/manage/", "manage"),
            ("https://creator.douyin.com/creator-micro/content/manage?tab=1", "manage"),
            ("https://creator.douyin.com/creator-micro/home", "creator-other"),
            (
                "https://creator.douyin.com/creator-micro/content/upload",
                "creator-other",
            ),
            ("https://creator.douyin.com/login", "login"),
            # The logged-out redirect keeps the original path in a query
            # parameter — the same trap `judge_douyin_session` documents. The
            # PATH is what decides, so this must not read as "manage".
            (
                "https://creator.douyin.com/login?redirect_url="
                "%2Fcreator-micro%2Fcontent%2Fmanage",
                "login",
            ),
            ("https://www.douyin.com/user/self", "off-host"),
            ("https://creator.douyin.com/", "other"),
            ("", "unknown"),
            ("not a url at all", "unknown"),
        ],
    )
    def test_labels(self, url, expected):
        assert page_label(url) == expected

    def test_it_never_emits_the_url_itself(self):
        """`verify_detail` is a database row and a log line, and this repo is
        public. A console URL can carry query parameters; the label may not
        contain any part of one."""
        url = (
            "https://creator.douyin.com/creator-micro/content/manage"
            "?sec_uid=SECRET&tab=2"
        )
        label = page_label(url)
        assert label == "manage"
        assert "SECRET" not in label
        assert "douyin" not in label


LOGIN_URL = "https://creator.douyin.com/login"


def _document(fragment: str) -> str:
    """A fragment as a real page. Real consoles always have a `<body>`, and
    `text_len` is read off it — testing against a bare fragment would exercise
    a path production never takes and leave the field unproven."""
    return f"<body>{fragment}</body>"


class TestPageProbe:
    """Which page did we reach, and had it finished rendering."""

    async def test_the_works_fixture_reads_as_the_works_page(self):
        probe = await measure_page(FakePage(_document(FIXTURE)))
        assert probe.where == "manage"
        assert probe.busy == 0
        assert probe.empty_words == 0
        assert probe.login_gate == 0
        assert probe.text_len and probe.text_len > 100
        assert probe.divs and probe.divs > 10

    async def test_the_empty_state_page_is_told_apart_from_a_missing_list(self):
        """**The distinction the first live probe could not make.**

        A works page that renders 「暂无作品」 is the platform answering; a page
        with no works list at all is us being somewhere else. Both produce zero
        cards.
        """
        empty = await measure_page(FakePage(_document(EMPTY_PAGE)))
        missing = await measure_page(FakePage(_document(REDESIGNED_PAGE)))
        assert empty.empty_words == 1
        assert missing.empty_words == 0

    async def test_a_login_screen_our_gate_does_not_recognise_is_still_visible(self):
        """`verify.py` gates on `LOGIN_TEXT_MARKERS` before the list is read,
        so by here that count is always 0 — which means a login page whose
        wording changed sails straight through and reads as "no works". The
        wider candidate set is the only thing that would show it."""
        page = FakePage(
            _document(
                '<div class="page"><div class="box">请先登录</div>'
                '<div class="btn">验证码登录</div></div>'
            ),
            url=LOGIN_URL,
        )
        probe = await measure_page(page)
        assert probe.login_gate == 0, "the gate's own vocabulary must still miss"
        assert probe.login_wide == 2, "…while the wider set sees it"
        assert probe.where == "login"

    async def test_a_still_rendering_page_says_so(self):
        page = FakePage(
            _document(
                '<div class="wrap"><div class="loading-spinner-x1"></div>'
                '<div class="skeleton-row-a2"></div></div>'
            )
        )
        probe = await measure_page(page)
        # 3, not 2: `loading-spinner` matches both `loading` and `spin`. The
        # selectors overlap by design and the field is documented as a
        # boolean-shaped signal — pinned here so nobody later reads it as a
        # node census and "fixes" the number.
        assert probe.busy == 3

    async def test_a_page_with_no_body_is_unmeasurable_not_empty(self):
        """A console that rendered no document at all must not report a text
        length of 0 — that reads like "the page was blank" when the truth is
        "we could not look"."""
        probe = await measure_page(FakePage(FIXTURE))  # fragment, no <body>
        assert probe.text_len is None
        assert "textlen=?" in probe.render()

    async def test_every_field_degrades_to_unmeasurable_not_to_zero(self):
        class ExplodingPage:
            url = "https://creator.douyin.com/creator-micro/content/manage"

            def locator(self, selector):
                raise RuntimeError("detached")

            def get_by_text(self, text, exact=False):
                raise RuntimeError("detached")

        probe = await measure_page(ExplodingPage())
        # The URL is read off an attribute, so the label survives — and that is
        # the point: it is the one signal that does not need the DOM.
        assert probe.where == "manage"
        assert probe.text_len is None
        assert probe.divs is None
        assert probe.busy is None
        assert probe.login_wide is None
        rendered = probe.render()
        assert "textlen=?" in rendered
        assert "busy=?" in rendered
        assert "0" not in rendered.replace("where=manage", "")

    async def test_a_partly_failed_group_is_unmeasurable_rather_than_understated(self):
        """A sum that silently dropped a failed probe would read like a real
        observation. Better `?` than a number nobody can trust."""

        class HalfBrokenPage(FakePage):
            def get_by_text(self, text, exact=False):
                if text == "扫码登录":
                    raise RuntimeError("detached")
                return super().get_by_text(text, exact=exact)

        probe = await measure_page(HalfBrokenPage(FIXTURE))
        assert probe.login_gate is None
        # …and a group that fully succeeded is still a number.
        assert probe.empty_words == 0


# The shape the first live probe actually returned: three `card-` nodes, one
# of them outermost, and NOTHING else — no works, no operation words, no card
# roots under the other three selectors. Reconstructed from the counts alone
# ([实测 2026-08-15]); the real page's markup was never captured, and this is
# a page that REPRODUCES THE NUMBERS, not a copy of it.
LIVE_SHAPE_NO_WORKS = """
<div class="card-shell-a1">
  <div class="card-head-b2">Console chrome</div>
  <div class="card-body-c3">Nothing that is a work</div>
</div>
"""


class TestTheLiveShapeIsNowDiagnosable:
    """**The read this round has to explain.**

    [实测 2026-08-15] the live read-back returned
    `cards=1 won=card- roots=content-card:0/0,work-card:0/0,video-card:0/0,
    card-:3/1 title_exact=0 ops=0/0` — a page with no works list on it. The
    list-shape probe proved the shadowing hypothesis was only half the story
    (`card-` did win, but `video-card` was 0 too, so the real selector had
    nothing to find either). What it could NOT say is why the page had no
    works. These pin that the next read will.
    """

    async def test_the_chrome_node_is_no_longer_accepted_as_a_work(self):
        """**The fix.** The selector still matches the chrome — that is a fact
        about the page, and the probe still reports it — but the node carries
        no operation word and no status word, so it is not a work and is not
        counted as one."""
        cards, probe = await read_works_list(FakePage(LIVE_SHAPE_NO_WORKS))
        measured = {sel: (raw, scoped) for sel, raw, scoped in probe.roots}
        # The raw counts are unchanged: this is still the same page.
        assert measured['[class^="card-"]'] == (3, 1)
        assert measured['[class*="video-card"]'] == (0, 0)
        # …but nothing on it is a work any more.
        assert cards == []
        assert probe.cards == 0
        assert probe.won is None

    async def test_arriving_early_is_now_inconclusive_rather_than_not_live(self):
        """**The verdict that caused the incident.**

        Same page, same moment, and the old code called it
        `NOT_LIVE / not_found` — "we read the list and your post is gone" —
        about a post that was live on the platform. It must now be a
        no-conclusion that retries.
        """
        page = FakePage(_document(LIVE_SHAPE_NO_WORKS))
        judgement = await verify_publish(page, "Some Published Caption")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.verdict is not ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "list_not_ready"

    async def test_the_empty_state_question_is_asked_again(self):
        """`empty = False if cards else …` meant one chrome node removed the
        "does the platform say this account is empty" question from the whole
        judgement. With the chrome no longer counted as a card, and the check
        no longer short-circuited, it is asked."""
        page = FakePage(_document(LIVE_SHAPE_NO_WORKS + EMPTY_PAGE))
        judgement = await verify_publish(page, "Some Published Caption")
        # The platform positively said "no works", which IS an answer.
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "not_found"
        assert judgement.detail["list_empty"] is True

    async def test_the_accepted_card_is_described_without_quoting_it(self):
        """Two integers, when there IS an accepted card. A short single-line
        node is a chip, a long multi-line one at least looks like a work."""
        _cards, probe = await read_works_list(FakePage(FIXTURE))
        assert probe.won_lines and probe.won_lines > 1
        rendered = probe.render()
        assert f"wonlen={probe.won_len}/{probe.won_lines}" in rendered
        assert "Autumn Harvest" not in rendered

    @pytest.mark.parametrize(
        "url,expected_where",
        [
            ("https://creator.douyin.com/creator-micro/content/manage", "manage"),
            ("https://creator.douyin.com/login", "login"),
            ("https://creator.douyin.com/creator-micro/home", "creator-other"),
        ],
    )
    async def test_where_separates_the_remaining_hypotheses(self, url, expected_where):
        """The same zero-works page, reached at three different addresses —
        three different bugs, and until now one indistinguishable output."""
        page = FakePage(_document(LIVE_SHAPE_NO_WORKS), url=url)
        probe = await measure_page(page)
        assert probe.where == expected_where

    async def test_the_whole_line_still_lands_in_the_message(self):
        """End to end: the diagnosis survives the fix. A read that gives up
        waiting must still say where it was, what it saw and how long it
        waited — otherwise the next regression is invisible again."""
        page = FakePage(_document(LIVE_SHAPE_NO_WORKS))
        judgement = await verify_publish(page, "Some Published Caption")
        assert judgement.reason == "list_not_ready"
        assert "[probe cards=0 won=none" in judgement.message
        assert "card-:3/1" in judgement.message
        assert "[page where=manage ready=timeout/" in judgement.message
        assert "empty=0" in judgement.message
        # and still not one word of the page in it
        assert "Console chrome" not in judgement.message


class TestProbeAgainstFixtureHtml:
    """The probe measured through the real reader, over real markup."""

    async def test_it_reports_every_candidate_not_only_the_winner(self):
        cards, probe = await read_works_list(FakePage(FIXTURE))
        assert len(cards) == 12
        assert probe.cards == 12
        assert probe.won == '[class*="video-card"]'
        measured = {sel: (raw, scoped) for sel, raw, scoped in probe.roots}
        # All four counted, including the three that did not win — the number
        # that was missing when a chrome node could shadow the real selector.
        assert set(measured) == set(CARD_SELECTORS)
        assert measured['[class*="video-card"]'] == (72, 12)
        assert measured['[class*="content-card"]'] == (0, 0)
        assert measured['[class*="work-card"]'] == (0, 0)

    async def test_shadowing_chrome_no_longer_costs_the_real_list(self):
        """**Regression for the shadowing half of the incident.**

        An earlier candidate matches a piece of page chrome. It used to win the
        loop outright, so the selector under which twelve works were sitting
        was never tried and the output read `1 work` — indistinguishable from a
        one-work account. Now the chrome carries no work evidence, is not
        counted, and the loop moves on to the candidate that does have works.
        """
        shadowed = (
            '<div class="content-card-header-x1">Filter bar chrome</div>'
            + FIXTURE
        )
        cards, probe = await read_works_list(FakePage(shadowed))
        assert len(cards) == 12
        assert probe.won == '[class*="video-card"]'
        measured = {sel: (raw, scoped) for sel, raw, scoped in probe.roots}
        # The chrome is still THERE and still reported — we did not stop
        # seeing it, we stopped believing it was a work.
        assert measured['[class*="content-card"]'] == (1, 1)
        assert measured['[class*="video-card"]'] == (72, 12)
        assert "won=video-card" in probe.render()

    async def test_the_probe_reaches_the_message_of_a_not_found_verdict(self):
        """If it does not land in the message it does not land in
        `verify_detail`, and a diagnostic nobody can read is not a
        diagnostic."""
        judgement = await verify_publish(FakePage(FIXTURE), "A Post That Was Deleted")
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "not_found"
        assert "[probe cards=12 won=video-card" in judgement.message
        assert "title_exact=0" in judgement.message

    async def test_title_exact_proves_the_caption_has_an_element_of_its_own(self):
        """**The measurement `match_cards`' line route was waiting on.**

        `title_exact` counts nodes whose ENTIRE text is the caption. ≥1 means
        the caption is not glued to the badge or the status word — it occupies
        an element of its own, and therefore a line of its own in `inner_text`.
        On the fixture that is true by construction; on the live console it is
        the number that will confirm or kill the inference.
        """
        cards, probe = await read_works_list(FakePage(FIXTURE))
        probe = await measure_page_probes(
            FakePage(FIXTURE), "Autumn Harvest Field Notes", probe
        )
        assert probe.title_exact == 1
        # And the same page's caption really does read back as its own line.
        card = next(c for c in cards if "Autumn Harvest" in c.text)
        assert "autumn harvest field notes" in card_lines(card.text)

    async def test_a_caption_that_is_absent_measures_zero_not_unmeasurable(self):
        probe = await measure_page_probes(
            FakePage(FIXTURE), "No Such Caption Anywhere", ListProbe()
        )
        assert probe.title_exact == 0

    async def test_a_blank_title_is_unmeasurable_rather_than_zero(self):
        probe = await measure_page_probes(FakePage(FIXTURE), "   ", ListProbe())
        assert probe.title_exact is None

    async def test_operation_word_counts_are_a_card_count_without_class_names(self):
        """The number that settles "does this account even have more than one
        work" independently of every selector in this module."""
        probe = await measure_page_probes(FakePage(FIXTURE), "x", ListProbe())
        assert probe.op_words == (12, 12)

    async def test_an_unreadable_page_still_produces_a_probe(self):
        """The case that matters most — nothing matched — must still explain
        itself, and must not fabricate a winner."""
        page = FakePage(REDESIGNED_PAGE)
        cards, probe = await read_works_list(page)
        assert cards == []
        assert probe.won is None
        assert probe.cards == 0
        assert all(scoped == 0 for _sel, _raw, scoped in probe.roots)
        judgement = await verify_publish(page, "Autumn Harvest Field Notes")
        assert judgement.reason == "list_not_ready"
        assert "won=none" in judgement.message

    async def test_a_live_verdict_stays_clean(self):
        """The probe explains the answers that need explaining. A post that
        verified needs none, and `verify_detail` is user-visible."""
        judgement = await verify_publish(
            FakePage(FIXTURE), "Linkless Live Card From The Real Console"
        )
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert "[probe" not in judgement.message

    async def test_reading_survives_a_page_whose_locators_raise(self):
        """Fails soft, and the failure is reported as `?` rather than as 0 —
        otherwise a broken page would look like an empty one."""

        class ExplodingPage:
            def locator(self, selector):
                raise RuntimeError("detached")

            def get_by_text(self, text, exact=False):
                raise RuntimeError("detached")

        page = ExplodingPage()
        cards, probe = await read_works_list(page)
        assert cards == []
        assert probe.won is None
        assert all(raw is None and scoped is None for _s, raw, scoped in probe.roots)
        probe = await measure_page_probes(page, "anything", probe)
        assert probe.title_exact is None
        assert "title_exact=?" in probe.render()
        assert "video-card:?/?" in probe.render()


class TestWorkStateMarkers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Some Title\n已发布\n播放 12", WorkState.LIVE),
            ("Some Title\n仅自己可见", WorkState.LIVE),
            ("Some Title\n好友可见", WorkState.LIVE),
            ("Some Title\n审核中", WorkState.UNDER_REVIEW),
            ("Some Title\n未通过", WorkState.REJECTED),
            ("Some Title\n已下架", WorkState.REJECTED),
            ("Some Title\n定时中", WorkState.SCHEDULED),
            ("Some Title\n某个我们没见过的状态", WorkState.UNKNOWN),
            ("", WorkState.UNKNOWN),
        ],
    )
    def test_classification(self, text, expected):
        assert classify_work_state(text) is expected

    def test_rejection_outranks_any_reassuring_word_on_the_same_card(self):
        """The substring trap, pinned: 「未通过」 contains 「通过」, and a card
        can carry publishing vocabulary next to its refusal. Reading the
        reassuring word first is how a refused post gets called live."""
        assert (
            classify_work_state("Title\n审核未通过\n已发布") is WorkState.REJECTED
        )

    def test_the_word_gong_kai_no_longer_votes_for_live(self):
        """「公开」 was dropped from LIVE_MARKERS. [实测 2026-08-11] it counts
        **0 on the whole manage page** — the cards show no visibility word —
        while being short enough to turn up inside a user's own caption. The
        text these markers match INCLUDES the caption, so keeping it meant a
        card in an unrecognised state could be closed as published on the
        strength of the user's prose. Unknown → ask a human is the safe
        direction, and this pins that we take it."""
        assert "公开" not in LIVE_MARKERS
        assert (
            classify_work_state("如何做一场公开演讲\n某个我们没见过的状态")
            is WorkState.UNKNOWN
        )

    def test_private_visibility_words_are_still_live(self):
        """They also count 0 on the live page, but they are long enough not to
        collide with prose and they carry a product guarantee: a private
        publish is a publish, and calling it not-live blocks a batch the user
        deliberately asked for."""
        assert classify_work_state("Title\n仅自己可见") is WorkState.LIVE
        assert classify_work_state("Title\n好友可见") is WorkState.LIVE


class TestItemIds:
    @pytest.mark.parametrize(
        "hrefs,expected",
        [
            ((), None),
            (("https://www.douyin.com/video/7412345678901234567",), "7412345678901234567"),
            (("/video/7412345678901234567?from=manage",), "7412345678901234567"),
            # Too short to be a snowflake — a tracking path, not an item.
            (("https://www.douyin.com/video/12",), None),
            (("https://www.douyin.com/user/abc",), None),
        ],
    )
    def test_extraction(self, hrefs, expected):
        assert extract_item_id(hrefs) == expected

    def test_url_building(self):
        assert build_published_url("7412345678901234567") == (
            "https://www.douyin.com/video/7412345678901234567"
        )
        assert build_published_url(None) is None


class TestJudgementEdges:
    def test_duplicate_titles_resolve_toward_live(self):
        """A caption is the only handle we have. If one post with that caption
        is live, blocking the user over the other one is the wrong call."""
        cards = [
            WorkCard(text="Repeat Caption Here\n未通过"),
            WorkCard(
                text="Repeat Caption Here\n已发布",
                hrefs=("https://www.douyin.com/video/7412345678901111111",),
            ),
        ]
        judgement = judge_readback(cards, "Repeat Caption Here")
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.item_id == "7412345678901111111"

    def test_matched_but_unrecognised_status_is_inconclusive(self):
        """Vocabulary drift is OUR gap. Blocking the user's work item over it
        is the same class of mistake as calling it done."""
        cards = [WorkCard(text="Known Title Here\n平台新造的状态词")]
        judgement = judge_readback(cards, "Known Title Here")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.reason == "unknown_work_state"


SKELETON = """
<div class="card-shell-a1"><div class="loading-spinner-b2"></div></div>
"""


def _card_markup(caption: str, status: str = "已发布") -> str:
    """One image card as MARKUP, in the fixture's measured shape.

    `_image_card` above produces the card's rendered TEXT, which is what the
    pure judgement takes. Anything that goes through a page needs real
    elements — the operation words have to be leaf nodes for an exact text
    match to find them, and the caption has to be its own block.
    """
    return f"""
<div class="video-card-a1b2">
  <div class="video-card-cover-c3"><i class="video-card-badge-d4">2张</i></div>
  <div class="video-card-info-e5">
    <div class="video-card-desc-f6">{caption}</div>
    <div class="op-g7"><span>编辑作品</span><span>设置权限</span>
      <span>作品置顶</span><span>删除作品</span></div>
  </div>
  <div class="video-card-stats-h8">2025年11月20日 22:30 {status} 播放 12</div>
</div>"""


class _LazyPage:
    """A page whose works list appears only after a few readiness polls.

    `FakePage` is deliberately immutable, which is right for everything else in
    this file — but a wait is a behaviour over TIME, and a document that never
    changes can only ever prove the timeout branch. This advances on counted
    readiness polls rather than on wall-clock, so the tests are deterministic.
    """

    def __init__(self, skeleton: str, loaded: str, after_polls: int = 2):
        self._skeleton = FakePage(_document(skeleton))
        self._loaded = FakePage(_document(loaded))
        self._after = after_polls * len(OPERATION_MARKERS)
        self._op_calls = 0
        self.url = self._loaded.url

    @property
    def _now(self) -> FakePage:
        return self._loaded if self._op_calls > self._after else self._skeleton

    def locator(self, selector: str):
        return self._now.locator(selector)

    def get_by_text(self, text: str, exact: bool = False):
        if text in OPERATION_MARKERS:
            self._op_calls += 1
        return self._now.get_by_text(text, exact=exact)


class TestWorkEvidence:
    """What may be counted as a work at all."""

    def test_console_chrome_is_not_a_work(self):
        assert has_work_evidence("Filter bar chrome") is False
        assert has_work_evidence("") is False

    def test_an_operation_word_is_evidence(self):
        assert has_work_evidence("Some Caption\n编辑作品\n删除作品") is True

    def test_a_status_word_alone_is_evidence(self):
        """Kept as a second route so a console that renames the operation
        words still reads — the two fail in different directions."""
        assert has_work_evidence("Some Caption\n已发布") is True
        assert has_work_evidence("Some Caption\n未通过") is True

    def test_an_unknown_status_with_operation_words_is_still_a_work(self):
        """Otherwise a work in a status we have not seen would be dropped from
        the count, and a dropped card reads as a missing post."""
        assert has_work_evidence("Some Caption\n平台新造的状态词\n编辑作品") is True


class TestReadiness:
    """**The fix.** Wait for a signal the page can only produce once it has
    answered — never for a fixed number of milliseconds."""

    def test_the_shipped_bounds_are_sane(self):
        """The autouse fixture shrinks these to keep the suite fast. Pinned so
        the shrink cannot quietly become what ships, and so the wait stays
        inside the read-back's 120 s per-attempt budget."""
        assert SHIPPED_READY_TIMEOUT_MS == 20_000
        assert SHIPPED_READY_POLL_MS == 500
        # …and comfortably inside `validate_attempt_timeout_s` (120 s), which
        # is the budget one read-back attempt has to finish in.
        assert SHIPPED_READY_TIMEOUT_MS < 120_000

    async def test_a_rendered_list_is_ready(self):
        readiness = await wait_for_works_list(FakePage(_document(FIXTURE)))
        assert readiness.ready is True
        assert readiness.reason == "cards"
        assert readiness.op_words and readiness.op_words > 0

    async def test_the_platforms_own_empty_state_is_also_an_answer(self):
        """An account with no works would otherwise never satisfy a
        cards-based signal, and would time out forever — trading one永-pending
        bug for another."""
        readiness = await wait_for_works_list(FakePage(_document(EMPTY_PAGE)))
        assert readiness.ready is True
        assert readiness.reason == "empty"

    async def test_a_skeleton_times_out_rather_than_reading_it(self):
        readiness = await wait_for_works_list(FakePage(_document(SKELETON)))
        assert readiness.ready is False
        assert readiness.reason == "timeout"
        assert readiness.waited_ms is not None

    async def test_chrome_cannot_satisfy_readiness(self):
        """**Why the signal is operation words and not card selectors.**

        This page matches `[class^="card-"]` — it is the shape that made a
        skeleton look like a list that had been read. It must not count as
        rendered.
        """
        readiness = await wait_for_works_list(
            FakePage(_document(LIVE_SHAPE_NO_WORKS))
        )
        assert readiness.ready is False

    async def test_a_list_that_arrives_late_is_waited_for(self):
        """The incident, in one test: the page is a skeleton when we first
        look and a works list a moment later."""
        page = _LazyPage(SKELETON, FIXTURE)
        readiness = await wait_for_works_list(page, timeout_ms=5_000, poll_ms=1)
        assert readiness.ready is True
        assert readiness.reason == "cards"

    async def test_a_half_rendered_list_is_not_read_as_complete(self):
        """The count must be non-zero AND stable. A list rendering
        progressively would otherwise be judged from whatever had arrived, and
        a post missing from a partial list reads as a deleted post."""
        page = _LazyPage(SKELETON, FIXTURE, after_polls=1)
        first = await _group_count_via(page)
        assert first == 0, "first look must land on the skeleton"
        readiness = await wait_for_works_list(page, timeout_ms=5_000, poll_ms=1)
        assert readiness.ready is True


async def _group_count_via(page) -> int:
    """One readiness sample, for tests that need to observe the first look."""
    total = 0
    for marker in OPERATION_MARKERS:
        total += await page.get_by_text(marker, exact=True).count()
    return total


class TestReadinessGovernsTheVerdict:
    """**Requirement 2, as a pure property.** Arriving early may cost us an
    answer; it may never produce a wrong one."""

    CARDS = (WorkCard(text="Some Other Post\n已发布\n编辑作品"),)

    def test_not_ready_and_not_found_is_inconclusive_not_not_live(self):
        judgement = judge_readback(
            self.CARDS, "The Post We Published", ready=False
        )
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.verdict is not ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "list_not_ready"
        assert judgement.detail["ready"] is False

    def test_the_same_input_when_ready_is_still_not_live(self):
        """The safety rule must not have swallowed the real verdict: a post
        genuinely absent from a list we DID read is still not live."""
        judgement = judge_readback(self.CARDS, "The Post We Published", ready=True)
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "not_found"

    def test_not_ready_does_not_suppress_a_post_we_can_see(self):
        """Arriving early cannot make a post we found fake. LIVE stands."""
        cards = [WorkCard(text="The Post We Published\n已发布\n编辑作品")]
        judgement = judge_readback(cards, "The Post We Published", ready=False)
        assert judgement.verdict is ReadbackVerdict.LIVE

    def test_not_ready_does_not_suppress_a_refusal_we_read(self):
        """A status read off a card we matched rests on what we SAW, not on
        what we missed — so it is still a conclusion."""
        cards = [WorkCard(text="The Post We Published\n未通过\n编辑作品")]
        judgement = judge_readback(cards, "The Post We Published", ready=False)
        assert judgement.verdict is ReadbackVerdict.NOT_LIVE
        assert judgement.reason == "rejected"

    def test_not_ready_with_an_empty_account_is_still_inconclusive(self):
        """An empty state we never waited to see is not an empty account."""
        judgement = judge_readback([], "Anything", list_empty=True, ready=False)
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.reason == "list_not_ready"


class TestTheIncidentEndToEnd:
    """**The acceptance shape.** Not "the tests pass" — a live post, on a page
    that renders late, must come back VERIFIED."""

    async def test_a_late_rendering_page_verifies_the_post(self):
        page = _LazyPage(SKELETON, FIXTURE)
        judgement = await verify_publish(page, "Autumn Harvest Field Notes")
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.reason == "live"

    async def test_the_old_code_path_would_have_called_it_missing(self):
        """The counterfactual, pinned: judging the skeleton — which is what
        reading on a 2 500 ms timer did — is what produced the wrong verdict.
        If this ever stops being INCONCLUSIVE, the regression is back.
        """
        judgement = await verify_publish(
            FakePage(_document(SKELETON)), "Autumn Harvest Field Notes"
        )
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.verdict is not ReadbackVerdict.NOT_LIVE

    async def test_the_whole_path_is_total_against_a_page_that_raises(self):
        """`list_is_empty` is now asked on EVERY read, including inside the
        poll loop — and `dom.visible_marker_texts` builds its locator outside
        its own try block, so a page that raises on `get_by_text` propagates
        through it. A read-back that raises is a read-back that cannot report
        a verdict at all, so the guard is pinned here rather than trusted.
        """

        class ExplodingPage:
            url = "https://creator.douyin.com/creator-micro/content/manage"

            def locator(self, selector):
                raise RuntimeError("detached")

            def get_by_text(self, text, exact=False):
                raise RuntimeError("detached")

        judgement = await verify_publish(ExplodingPage(), "Anything At All")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.verdict is not ReadbackVerdict.NOT_LIVE

    async def test_a_short_title_on_a_late_page_also_verifies(self):
        """The two fixes compose: the caption that started all of this is four
        characters long, and it has to survive both the wait and the matcher."""
        page = _LazyPage(SKELETON, _card_markup("test"))
        judgement = await verify_publish(page, "test")
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.reason == "live"


class _NetPage(FakePage):
    """A page whose resource-timing buffer we control.

    Dispatches on the SCRIPT, because there are now two probes that evaluate
    JavaScript and handing the render probe a timing dict would quietly make
    every render field unmeasurable in tests that are not about it.
    """

    def __init__(self, html: str, timings, url: str | None = None, rendering=None):
        super().__init__(_document(html), url=url or FakePage("").url)
        self._timings = timings
        self._rendering = rendering

    async def evaluate(self, script, *args):
        if "requestAnimationFrame" in str(script):
            if callable(self._rendering):
                return self._rendering()
            return self._rendering
        if callable(self._timings):
            return self._timings()
        return self._timings


def _worst_case_probe() -> ListProbe:
    """A full probe carrying round five's real numbers.

    The longest line this module can produce on a path that blocks a work
    item: every selector counted, every page field measured, the caption
    absent. Used to size the diagnostic against the downstream store.
    """
    return ListProbe(
        roots=tuple((sel, 0, 0) for sel in CARD_SELECTORS),
        won=None,
        cards=0,
        title_exact=0,
        op_words=(0, 0),
        page=PageProbe(
            where="manage", ready="timeout", waited_ms=20565, text_len=105,
            divs=73, login_gate=0, login_wide=0, works_words=2,
            empty_words=0, busy=1,
        ),
        render_probe=RenderProbe(
            raf=True, body_h=704, vw=1280, vh=720, dpr=1,
            iframes=0, frames=1, shadow=0,
        ),
        net=douyin_verify.NetProbe(
            resources=250, xhr_before=138, xhr=180, ok=180, c4=0, c5=0,
            unknown=0, empty=49, ready_state="complete",
        ),
    )


def _rendering(**over):
    """A healthy browser's answer to the render probe."""
    base = {
        "body_h": 704,
        "vw": 1280,
        "vh": 720,
        "dpr": 1,
        "iframes": 0,
        "shadow": 0,
        "raf": True,
    }
    base.update(over)
    return base


def _timing(**over):
    base = {
        "resources": 40,
        "xhr": 0,
        "ok": 0,
        "c4": 0,
        "c5": 0,
        "unknown": 0,
        "empty": 0,
        "status_supported": False,
        "ready": "complete",
    }
    base.update(over)
    return base


class TestNetworkProbe:
    """**Did the page ever ask for its list.**

    [实测 2026-08-15] After the read-back learnt to wait, it waited the full
    20 s and the page was as empty as it had been at 2.5 s — `textlen=105`,
    `busy=1`, `divs=73` against 105/1/72 before. Not slow: nothing was
    arriving. The next question is one level down, and it is a network
    question.
    """

    def test_the_page_script_cannot_return_a_url(self):
        """**The containment property, asserted structurally.**

        The interesting field on a `PerformanceResourceTiming` is `name` — the
        full request URL. `verify_detail` is a database row and a log line, and
        this repo is public. The aggregation therefore happens inside the page
        and only integers come back; if this script ever learns to read
        `.name`, this test is the thing that says so.
        """
        source = douyin_verify._NETWORK_TIMING_JS
        assert ".name" not in source
        assert "entry.name" not in source
        assert "JSON.stringify" not in source
        # Only these keys may cross the boundary.
        assert set(re.findall(r"(\w+):", source)) <= {
            "resources", "xhr", "ok", "c4", "c5", "unknown", "empty",
            "status_supported", "ready",
        }

    async def test_a_page_that_never_asked_reads_as_zero_xhr(self):
        """Candidate: the request was never sent."""
        page = _NetPage(SKELETON, _timing(resources=40, xhr=0))
        probe = await measure_network(page, xhr_before=0)
        assert probe.xhr == 0
        assert probe.resources == 40
        assert "xhr=0->0" in probe.render()

    async def test_failed_requests_show_up_as_status_buckets(self):
        """Candidate: the request went out and the platform refused it."""
        page = _NetPage(
            SKELETON,
            _timing(xhr=6, c4=5, ok=1, status_supported=True, empty=5),
        )
        probe = await measure_network(page, xhr_before=2)
        assert (probe.ok, probe.c4, probe.c5) == (1, 5, 0)
        assert "xhr=2->6" in probe.render()
        assert "4xx=5" in probe.render()

    async def test_missing_response_status_support_is_unknown_not_zero(self):
        """**The `?`-vs-`0` rule, at the capability level.**

        `responseStatus` needs Chromium 109+. If the browser does not expose
        it, "no 4xx seen" is not an observation we made — it is a measurement
        we could not take, and rendering it as `0` would read like proof that
        nothing failed.
        """
        page = _NetPage(SKELETON, _timing(xhr=6, unknown=6, status_supported=False))
        probe = await measure_network(page)
        assert probe.xhr == 6
        assert probe.ok is None and probe.c4 is None and probe.c5 is None
        assert probe.unknown == 6
        rendered = probe.render()
        assert "ok=? 4xx=? 5xx=?" in rendered
        assert "unk=6" in rendered

    async def test_a_page_without_evaluate_degrades_to_unmeasurable(self):
        """`FakePage` has no `evaluate`, and neither will a page that detached.
        Every field must be `?` — never a zero that reads like "it asked for
        nothing"."""
        probe = await measure_network(FakePage(_document(SKELETON)))
        assert probe.xhr is None and probe.resources is None
        rendered = probe.render()
        assert "res=? xhr=?->?" in rendered
        assert "0" not in rendered

    async def test_a_script_that_returns_null_is_unmeasurable(self):
        """The page script returns `null` when the Performance API itself
        throws — that is a failed measurement, not an empty one."""
        page = _NetPage(SKELETON, None)
        probe = await measure_network(page, xhr_before=3)
        assert probe.resources is None
        assert probe.xhr is None
        assert probe.xhr_before == 3

    async def test_growth_across_the_wait_is_visible(self):
        """A page that is talking and still not rendering moves this number; a
        page that never asked does not. Different bugs, different fixes."""
        page = _NetPage(SKELETON, _timing(xhr=9))
        probe = await measure_network(page, xhr_before=3)
        assert "xhr=3->9" in probe.render()

    async def test_the_net_section_joins_the_others_in_the_message(self):
        """All FOUR sections have to survive together — each one located a
        different round's finding, and losing any to make room would cost the
        next one."""
        page = _NetPage(SKELETON, _timing(xhr=0), url=LOGIN_URL, rendering=_rendering())
        judgement = await verify_publish(page, "Some Caption")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert "[probe cards=" in judgement.message
        assert "[page where=login" in judgement.message
        assert "[render raf=1" in judgement.message
        assert "[net res=40 xhr=0->0" in judgement.message
        assert "doc=complete" in judgement.message


class TestRenderProbe:
    """**Is our own browser working, and are we reading the whole document.**

    Round five ruled out the platform: `where=manage`, `login=0+0`, 180 XHRs
    with `4xx=0 5xx=0`, `doc=complete` — and then 105 characters and 73 divs on
    screen. Nothing was refused and nothing failed to arrive, so the remaining
    candidates are all on our side of the wire, and none of them had a number.
    """

    def test_the_page_script_cannot_return_content(self):
        """**The containment property, asserted structurally.**

        Same rule as the network probe: `verify_detail` is a database row and a
        log line, and this repo is public. This script walks every element on
        the page, so it is one `.textContent` away from exfiltrating the whole
        console. If it ever learns to read text, this test says so.
        """
        source = douyin_verify._RENDER_PROBE_JS
        for forbidden in (
            "textContent", "innerText", "innerHTML", "outerHTML",
            ".src", ".href", "location", "JSON.stringify", "getAttribute",
        ):
            assert forbidden not in source, forbidden
        # Only these keys may cross the boundary.
        assert set(re.findall(r"(\w+):", source)) <= {
            "body_h", "vw", "vh", "dpr", "iframes", "shadow", "raf",
        }

    async def test_a_healthy_browser_reports_frames_and_one_document(self):
        page = _NetPage(SKELETON, _timing(), rendering=_rendering())
        probe = await measure_render(page)
        assert probe.raf is True
        assert probe.iframes == 0
        assert probe.shadow == 0
        assert "[render raf=1 vp=1280x720/1 bodyh=704 ifr=0/" in probe.render()

    async def test_a_browser_that_never_painted_says_so(self):
        """Candidate 1: no frames.

        The repo has this exact failure recorded on this host — SwiftShader
        stalls, `requestAnimationFrame` never fires, and every Playwright
        `click()` fails its stability gate. `raf=0` is a REAL finding and must
        be distinguishable from `raf=?`.
        """
        page = _NetPage(SKELETON, _timing(), rendering=_rendering(raf=False))
        probe = await measure_render(page)
        assert probe.raf is False
        assert "raf=0" in probe.render()

    async def test_a_list_hidden_in_an_iframe_would_be_visible_as_a_count(self):
        """Candidate 2: the works list is somewhere this reader cannot look.

        Every selector in the module runs against the top document, so a
        console that moved its list into an iframe reads as an empty page
        forever. 73 divs and 105 characters is what that looks like.
        """
        page = _NetPage(SKELETON, _timing(), rendering=_rendering(iframes=3, shadow=12))
        probe = await measure_render(page)
        assert probe.iframes == 3
        assert probe.shadow == 12
        assert "ifr=3/" in probe.render()
        assert "sdw=12" in probe.render()

    async def test_the_window_shape_is_reported(self):
        """Candidate 3: a virtualised list renders no rows into a degenerate
        window."""
        page = _NetPage(
            SKELETON, _timing(), rendering=_rendering(vw=800, vh=16, dpr=2.5, body_h=0)
        )
        probe = await measure_render(page)
        assert "vp=800x16/2.5" in probe.render()
        assert "bodyh=0" in probe.render()

    async def test_frames_come_from_the_driver_not_the_page(self):
        """`iframes` and `frames` measure the same thing two ways on purpose.

        A cross-origin iframe can be opaque to `querySelectorAll` while still
        being a frame Playwright lists, so a DISAGREEMENT between the two is
        itself the finding — which only works if they have independent sources.
        """
        page = _NetPage(SKELETON, _timing(), rendering=_rendering(iframes=0))
        page.frames = [object(), object(), object()]
        probe = await measure_render(page)
        assert probe.iframes == 0
        assert probe.frames == 3
        assert "ifr=0/3" in probe.render()

    async def test_an_unmeasurable_browser_renders_question_marks_not_zeroes(self):
        """**The rule this whole file is built on.**

        "We could not look" and "we looked and there was none" are opposite
        findings. A probe whose broken output is shaped like a negative answer
        is not evidence — and here the negative answer (`raf=0`) is precisely
        the diagnosis being hunted, so a collapse to zero would manufacture it.
        """
        probe = await measure_render(FakePage(_document(SKELETON)))
        rendered = probe.render()
        assert "raf=?" in rendered
        assert "vp=?x?/?" in rendered
        assert "bodyh=?" in rendered
        assert "ifr=?/?" in rendered
        assert "sdw=?" in rendered
        assert "=0" not in rendered

    async def test_a_script_that_returns_the_wrong_shape_is_unmeasurable(self):
        page = _NetPage(SKELETON, _timing(), rendering=None)
        probe = await measure_render(page)
        assert probe.raf is None
        assert "raf=?" in probe.render()

    async def test_a_non_boolean_raf_is_unmeasurable_not_true(self):
        """`raf` is the one field where a truthy non-answer would be worst:
        it would report frames on a browser nobody measured."""
        page = _NetPage(SKELETON, _timing(), rendering=_rendering(raf="yes"))
        probe = await measure_render(page)
        assert probe.raf is None
        assert "raf=?" in probe.render()

    async def test_a_page_that_hangs_is_bounded_rather_than_hanging_the_readback(self):
        """The negative answer is the one that cannot be waited for.

        A browser producing no frames also never runs the in-page timer if its
        event loop is wedged, so the evaluate itself has a ceiling. Without it
        the read-back would burn its whole 120 s attempt budget on the probe
        that was supposed to explain the failure.
        """

        class _Hangs(FakePage):
            async def evaluate(self, script, *args):
                await asyncio.sleep(30)

        monkey = _Hangs(_document(SKELETON))
        original = douyin_verify._RENDER_PROBE_TIMEOUT_S
        douyin_verify._RENDER_PROBE_TIMEOUT_S = 0.05
        try:
            started = time.monotonic()
            probe = await measure_render(monkey)
        finally:
            douyin_verify._RENDER_PROBE_TIMEOUT_S = original
        assert time.monotonic() - started < 5
        assert probe.raf is None
        assert "raf=?" in probe.render()

    async def test_the_render_probe_never_changes_the_verdict(self):
        """A dead browser is still not evidence about the post.

        `raf=0` explains why we saw nothing; it may never become "your post is
        gone". This is the same asymmetry `Readiness` enforces, checked on the
        new field so a future edit cannot quietly wire it into the judgement.
        """
        page = _NetPage(SKELETON, _timing(), rendering=_rendering(raf=False))
        judgement = await verify_publish(page, "Some Caption")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert "raf=0" in judgement.message

    def test_the_render_section_is_ordered_before_the_net_section(self):
        """Order is load-bearing because of a truncation downstream.

        `publish_tasks_repository.verification_update_stmt` stores this string
        as `detail[:500]` — a silent slice, no marker, no log — so whatever
        sits last is what gets eaten first. `[render ...]` goes before
        `[net ...]` rather than at the end; any edit that appends it instead
        fails here.
        """
        rendered = _worst_case_probe().render()
        assert rendered.index("[render ") < rendered.index("[net ")

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BLOCKED on a one-line change outside this module: "
            "publish_tasks_repository.verification_update_stmt truncates to "
            "detail[:500]. Measured — three sections + the abandoned prefix "
            "already reach 499/500 on the list_unreadable path, so a fourth "
            "section cannot fit and the tail of [net ...] is silently cut. "
            "Raise that limit (the column is TEXT, no DB limit) and this test "
            "starts passing, which strict-xfail reports as a failure so the "
            "marker gets removed."
        ),
    )
    @pytest.mark.parametrize("reason, ready", [
        ("list_not_ready", False),
        ("list_unreadable", True),
    ])
    def test_the_whole_diagnostic_survives_the_500_char_store(self, reason, ready):
        """**A diagnostic that gets truncated is a diagnostic that lied.**

        The three existing sections each decided a previous round, so none may
        be sacrificed for the new one — which means the line has to FIT, not
        merely be ordered well. This encodes that requirement against the real
        worst case: an `abandoned` row, whose prefix alone is 83 characters
        before the message starts, and which is exactly the row that blocks a
        work item for a human to read.
        """
        judgement = judge_readback(
            [], "Some Caption", probe=_worst_case_probe(), ready=ready
        )
        line = (
            "[verification_abandoned] gave up after 5 attempt(s); "
            f"last failure: [{reason}] {judgement.message}"
        )
        assert len(line) <= 500, (
            f"{len(line)} chars — the store would cut {len(line) - 500}, "
            f"losing {line[500:]!r}"
        )


class TestStatusMapping:
    """`response_for_judgement` is the only place `not_published` is minted."""

    def test_live_maps_to_published_and_succeeds(self):
        from app.verify import ReadbackJudgement

        response = response_for_judgement(
            ReadbackJudgement(
                ReadbackVerdict.LIVE,
                "live",
                "ok",
                item_id="7412345678901234567",
                published_url="https://www.douyin.com/video/7412345678901234567",
            ),
            "douyin",
            {},
        )
        assert response.success is True
        assert response.status is SessionStatus.PUBLISHED
        assert response.published_url.endswith("7412345678901234567")

    def test_not_live_maps_to_not_published(self):
        from app.verify import ReadbackJudgement

        response = response_for_judgement(
            ReadbackJudgement(ReadbackVerdict.NOT_LIVE, "rejected", "refused"),
            "douyin",
            {},
        )
        assert response.success is False
        assert response.status is SessionStatus.NOT_PUBLISHED
        assert response.detail["reason"] == "rejected"

    async def test_the_probe_survives_into_the_wire_response(self):
        """**The last hop this module owns.**

        `verify_detail` is built by `publish_readback.verdict_for` as
        `[reason] message`, and `browser_client.verify_publish` copies the wire
        `message` through verbatim — so a probe that reaches this response
        reaches the database row. Everything after this is someone else's file;
        everything before it is pinned above. If this breaks, the diagnostic
        goes silent while still looking like it works, which is the exact
        failure it was built to prevent.
        """
        judgement = await verify_publish(FakePage(FIXTURE), "A Post That Was Deleted")
        response = response_for_judgement(judgement, "douyin", {})
        assert response.status is SessionStatus.NOT_PUBLISHED
        assert "[probe cards=12" in response.message
        assert "won=video-card" in response.message

    def test_inconclusive_must_not_wear_not_published(self):
        """The distinction the whole module is built on. `not_published`
        blocks a user's work item; "I could not tell" must never trigger that
        on the strength of one unreadable page."""
        from app.verify import ReadbackJudgement

        response = response_for_judgement(
            ReadbackJudgement(
                ReadbackVerdict.INCONCLUSIVE, "list_unreadable", "no idea"
            ),
            "douyin",
            {},
        )
        assert response.status is SessionStatus.FAILED
        assert response.status is not SessionStatus.NOT_PUBLISHED
