"""Publish read-back (P1-3): fixture-HTML parsing + the pure judgement.

Two layers, and the split is deliberate:

* `TestAgainstFixtureHtml` runs the REAL reader (`read_work_cards`,
  `list_is_empty`, `verify_publish`) over real markup via `dom_fixture`. This
  is the evidence that the DOM half works — selector choice, nested-wrapper
  de-duplication, item-id extraction off the anchor.
* the rest pin the pure judgement, including the cases the fixture cannot
  stage (a title that matches two cards, a status word we do not know).

The one thing these cannot prove is that the live creator centre is shaped like
the fixture. That needs a bound account. What they DO prove is the property
that makes the unknown survivable: when the shape is wrong, the answer is
`list_unreadable` (inconclusive), never a false "live" and never a false
"deleted". `test_unknown_markup_is_inconclusive_not_a_verdict` is that test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.platforms.douyin_verify import (
    WorkCard,
    WorkState,
    build_published_url,
    classify_work_state,
    extract_item_id,
    judge_readback,
    list_is_empty,
    match_cards,
    normalize_title,
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
        assert len(cards) == 5, [c.text[:40] for c in cards]

    async def test_item_id_comes_off_the_watch_link(self):
        cards = await read_work_cards(FakePage(FIXTURE))
        live = next(c for c in cards if "Autumn Harvest" in c.text)
        assert live.item_id == "7412345678901234567"
        # The under-review card has no link yet, and that must read as "no id",
        # not as "borrow the neighbour's".
        pending = next(c for c in cards if "Winter Kitchen" in c.text)
        assert pending.item_id is None

    async def test_live_post_verifies_with_a_url(self):
        judgement = await verify_publish(
            FakePage(FIXTURE), "Autumn Harvest Field Notes"
        )
        assert judgement.verdict is ReadbackVerdict.LIVE
        assert judgement.reason == "live"
        assert judgement.published_url == (
            "https://www.douyin.com/video/7412345678901234567"
        )
        assert judgement.detail["cards_seen"] == 5

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
        assert judgement.detail["cards_seen"] == 5

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
            classify_work_state("Title\n审核未通过\n公开") is WorkState.REJECTED
        )


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
