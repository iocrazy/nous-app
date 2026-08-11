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

from pathlib import Path

import pytest

from app.platforms.douyin_verify import (
    LIVE_MARKERS,
    WorkCard,
    WorkState,
    build_published_url,
    classify_work_state,
    extract_item_id,
    judge_readback,
    list_is_empty,
    match_cards,
    normalize_title,
    outermost_only,
    read_work_cards,
    verify_publish,
)
from app.verify import ReadbackVerdict, response_for_judgement
from app.schemas import SessionStatus

from tests.dom_fixture import FakePage

pytestmark = pytest.mark.unit

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
        this module exists to avoid."""
        page = FakePage(REDESIGNED_PAGE)
        assert await read_work_cards(page) == []
        assert await list_is_empty(page) is False
        judgement = await verify_publish(page, "Autumn Harvest Field Notes")
        assert judgement.verdict is ReadbackVerdict.INCONCLUSIVE
        assert judgement.reason == "list_unreadable"


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

    def test_short_titles_need_whole_string_equality(self):
        """A two-character title as a substring would match half the account,
        and a false match reports the WRONG post's state as this batch's."""
        cards = [WorkCard(text="测试版本 已发布")]
        assert match_cards(cards, "测试") == []

    def test_empty_title_matches_nothing(self):
        assert match_cards([WorkCard(text="anything 已发布")], "   ") == []


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
