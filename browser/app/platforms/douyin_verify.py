"""Douyin publish read-back: is the post this batch created actually live?

Why this module exists
======================
`douyin_publish._drive` returns `platform_item_id=None, published_url=None`, and
its comment says why: the post-publish redirect lands on `/content/manage`
carrying no identifier, and taking the newest card off that list would attribute
the wrong post to the batch on any account with a scheduled or concurrently
published item. So the publish itself can only ever report "the editor accepted
us".

For a SCHEDULED publish that gap is the whole problem. The browser types a time
into the form hours before anything goes out; between then and go-live the
platform can review it, refuse it, drop the schedule, or the user can delete the
post. Closing the work item on a timer turns every one of those into a silent
"done".

This module answers the question the timer was guessing at, from the other end:
it goes looking for a SPECIFIC post — identified by the caption we typed
ourselves — and reports what the platform now says about it.

What it delivers, and what it does not
======================================
The deliverable is the **verdict** (live / under review / refused / gone). It
is read from the card's status text and needs no post id.

It does NOT reliably deliver a `published_url`. The manage page was checked
against a live account on 2026-08-08 and its cards carry no href, no id
attribute, and no listing XHR that returns one (the finding is recorded in
`frontend/.../RecordsPage.tsx`, which is why the UI says "Open in platform"
rather than "View post"). The id extraction here is opportunistic — see the
note on `_ITEM_ID_PATTERN`. A live card with no id still verifies.

Shape, matching the rest of this tree
=====================================
This module contributes **selectors, marker texts and pure judgement
functions**; all driving, retrying and bounding lives in the platform-neutral
`verify` module — the same split as `douyin.py` ↔ `validation.py` / `login.py`.
Everything except `read_work_cards` / `list_is_empty` / `verify_publish` is
pure and unit-testable without a browser.

⚠️ Honesty about the selectors
==============================
`CARD_SELECTORS` has **not** been verified against a live creator centre. The
publish flow's selectors were read off a real account (see the dated notes in
`douyin.py` and `douyin_publish.py`); these were not, because confirming them
needs a bound account that already has works on it.

So the design makes being wrong about them SAFE rather than plausible-looking,
and that property is the load-bearing part:

  * wrong selectors ⇒ zero cards read ⇒ `list_unreadable`, which is
    INCONCLUSIVE. The caller retries and eventually abandons — and an abandoned
    verification blocks the work item for a human to look at. It never resolves
    to "the post is gone".
  * the only route to "this account has no such post" needs the page to either
    render cards that do not match, or positively render its own empty state.
    Both mean we DID read the list.

A broken selector therefore costs a false "please check this", never a false
"it went out fine" and never a false "it was deleted".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from ..dom import visible_marker_texts
from ..verify import ReadbackJudgement, ReadbackVerdict, VerifySpec
from . import register_verify_spec
from .douyin import CREATOR_HOSTS, LOGIN_TEXT_MARKERS

PLATFORM = "douyin"

# 作品管理 — where the platform lists everything the account has posted. Same
# page `douyin_publish` already treats as its publish signal (it waits for
# `MANAGE_PATH_FRAGMENT` after clicking confirm).
WORKS_URL = "https://creator.douyin.com/creator-micro/content/manage"

# Public watch URL for an item id. The one place this shape is written down.
VIDEO_URL_TEMPLATE = "https://www.douyin.com/video/{item_id}"

# Item ids are long decimal snowflakes. Bounded rather than `\d+` so a stray
# `/video/0` or a tracking parameter cannot masquerade as one.
_ITEM_ID_PATTERN = re.compile(r"/video/(\d{6,32})")

# ⚠️ EXPECT THIS TO FIND NOTHING on today's console, and do not treat that as a
# failure.
#
# `frontend/components/Distribution/RecordsPage.tsx` records a live-console
# check from 2026-08-08: the manage page's post cards expose **neither an href
# nor an id attribute, and no listing XHR returns one either** — all three
# verified against a real account. That is exactly why the frontend renders
# "Open in platform" instead of "View post", and why `douyin_publish._drive`
# returns `published_url=None` in the first place.
#
# So the id extraction below is OPPORTUNISTIC, not the deliverable:
#
#   * What this read-back actually delivers is the VERDICT — is the post live,
#     under review, refused, or gone. That comes from the card's status text and
#     needs no id at all.
#   * `published_url` / `platform_item_id` get filled only if the console ever
#     starts exposing an id (a redesign, or a layout we have not seen). A live
#     card with no id still verifies — see `judge_readback`, which returns LIVE
#     with `item_id=None`, and `verification_update_stmt`, which only writes the
#     URL when it is non-empty.
#
# Keeping the extraction costs one regex per card and means a console that does
# expose an id gets picked up for free. Deleting it would mean noticing the
# change by hand. Believing it will fire today would be the mistake.

# Candidate roots for one work card, most specific first, class-name-free
# structural fallback last. A list rather than a single selector for the reason
# spelled out in `dom.first_visible_attribute`: this console has already moved
# its QR node twice, and one selector turns any redesign into a hard outage.
CARD_SELECTORS: tuple[str, ...] = (
    '[class*="content-card"]',
    '[class*="work-card"]',
    '[class*="video-card"]',
    '[class^="card-"]',
)

# The platform's own "you have nothing here" state. Reaching it is a REAL
# answer: the account has no works, so the post we are looking for is not one.
#
# Matched with `exact=True` (repo rule, earned the hard way: 「允许」 is a
# substring of 「不允许」). The cost is that a decorated variant
# ("暂无作品，快去发布吧") will NOT match — and that cost is the right way
# round. A missed empty state degrades to `list_unreadable`, which retries and
# then asks a human; a loose match would let an unrelated caption convince us
# the account is empty and blocked the user over it.
LIST_EMPTY_MARKERS: tuple[str, ...] = (
    "暂无作品",
    "暂无内容",
    "还没有作品",
    "你还没有发布作品",
)


class WorkState(str, Enum):
    """What the platform says about one work. Platform-specific by design — the
    neutral layer only ever sees the `ReadbackVerdict` this collapses into."""

    LIVE = "live"
    UNDER_REVIEW = "under_review"
    REJECTED = "rejected"
    SCHEDULED = "scheduled"
    UNKNOWN = "unknown"


# Marker texts per state. **Evaluation order is the design** (see
# `classify_work_state`): the negative states are checked before LIVE, because
# each of them renders on a card that also carries neutral publishing
# vocabulary, and reading the reassuring word first is how a refused post gets
# reported as live.
#
# Substring traps, stated explicitly — this is a plain `in` test against the
# card's text, not a Playwright matcher, so `exact=` does not apply here and the
# discipline has to be manual:
#
#   * 「未通过」 CONTAINS 「通过」, which is why no LIVE marker may be 「通过」.
#   * 「审核中」 does not occur inside 「审核通过」, so it is safe as written.
#   * 「仅自己可见」 / 「好友可见」 are LIVE. A private post IS published, and
#     `visibility=private|friends` is an option the user can legitimately pick
#     on the batch — treating them as not-live would block every private
#     publish the product explicitly supports.
REJECTED_MARKERS: tuple[str, ...] = (
    "未通过",
    "审核不通过",
    "已下架",
    "内容违规",
    "发布失败",
)
UNDER_REVIEW_MARKERS: tuple[str, ...] = (
    "审核中",
    "正在审核",
    "待审核",
)
SCHEDULED_MARKERS: tuple[str, ...] = (
    "定时中",
    "待发布",
    "未发布",
)
LIVE_MARKERS: tuple[str, ...] = (
    "已发布",
    "仅自己可见",
    "好友可见",
    "公开",
)

_STATE_MARKERS: tuple[tuple[WorkState, tuple[str, ...]], ...] = (
    (WorkState.REJECTED, REJECTED_MARKERS),
    (WorkState.UNDER_REVIEW, UNDER_REVIEW_MARKERS),
    (WorkState.SCHEDULED, SCHEDULED_MARKERS),
    (WorkState.LIVE, LIVE_MARKERS),
)

# How many cards to read. The list is paginated and the post we want is among
# the most recent; walking the whole history would cost minutes and add nothing.
MAX_CARDS = 24

# Below this many characters a title is too weak to match on as a substring —
# 「测试」 would match half the account. Short titles fall back to whole-string
# equality, which is stricter, not looser.
_MIN_SUBSTRING_TITLE_LEN = 6

_WHITESPACE = re.compile(r"\s+")
_TRIM_CHARS = "　 \t\r\n​﻿"


def normalize_title(value: str) -> str:
    """Comparable form of a caption. Pure.

    Whitespace-collapsed and case-folded, because the round trip through the
    editor is not byte-preserving: it trims, collapses runs of spaces, and
    renders full-width spaces differently from how we sent them. Comparing raw
    strings reports "not found" for a post that is sitting right there.
    """
    if not value:
        return ""
    cleaned = _WHITESPACE.sub(" ", value.replace("　", " "))
    return cleaned.strip(_TRIM_CHARS).casefold()


def extract_item_id(hrefs: Sequence[str]) -> str | None:
    """First Douyin item id among `hrefs`, or None. Pure."""
    for href in hrefs:
        match = _ITEM_ID_PATTERN.search(href or "")
        if match:
            return match.group(1)
    return None


def build_published_url(item_id: str | None) -> str | None:
    """Watch URL for an item id, or None. Pure."""
    return VIDEO_URL_TEMPLATE.format(item_id=item_id) if item_id else None


@dataclass(frozen=True)
class WorkCard:
    """One card as read off the page.

    `text` is the card's whole rendered text — caption, status word, counters —
    which is what the markers match against. `hrefs` is every watch link inside
    it.
    """

    text: str
    hrefs: tuple[str, ...] = ()

    @property
    def item_id(self) -> str | None:
        return extract_item_id(self.hrefs)


def classify_work_state(text: str) -> WorkState:
    """What the platform says about this card. Pure.

    Negative states first — see the note on `_STATE_MARKERS`. UNKNOWN is a real
    outcome and must not be folded into LIVE: a card whose status vocabulary we
    do not recognise is a card we have no verdict for, and `judge_readback`
    routes it to "ask again later" rather than to "done".
    """
    if not text:
        return WorkState.UNKNOWN
    for state, markers in _STATE_MARKERS:
        if any(marker in text for marker in markers):
            return state
    return WorkState.UNKNOWN


def match_cards(cards: Sequence[WorkCard], title: str) -> list[WorkCard]:
    """Cards whose text carries `title`. Pure.

    Substring rather than equality, because the card's text is the WHOLE card —
    caption plus status word plus view counter — and because the platform
    truncates long captions with an ellipsis.

    Short titles are matched by normalised equality against the card text
    instead. A two-character title would otherwise match nearly every card on
    the account, and a false match is far worse than a miss: a miss retries,
    whereas a false match reports some OTHER post's state as this batch's
    outcome.
    """
    needle = normalize_title(title)
    if not needle:
        return []
    out: list[WorkCard] = []
    for card in cards:
        haystack = normalize_title(card.text)
        if not haystack:
            continue
        if len(needle) >= _MIN_SUBSTRING_TITLE_LEN:
            if needle in haystack:
                out.append(card)
        elif needle == haystack:
            out.append(card)
    return out


def judge_readback(
    cards: Sequence[WorkCard],
    title: str,
    *,
    list_empty: bool = False,
) -> ReadbackJudgement:
    """Cards + the caption we published → the verdict. Pure. Total.

    `list_empty` is the platform's own empty state, and it is what separates
    "this account has no works" (a real answer) from "we read nothing" (no
    answer). Without that distinction a broken card selector is
    indistinguishable from a deleted post — and the safe reading of that
    ambiguity is only available because they are told apart here.

    Multiple matches resolve toward LIVE. A caption is the only handle we have,
    so an account that reposts under the same title can produce two matches;
    calling the batch live when *a* post with that caption is live is the
    direction that does not block a user over a duplicate name.
    """
    matches = match_cards(cards, title)

    if not matches:
        if cards:
            # We read a real list and this post is not on it. For a scheduled
            # batch past its go-live time that means it never landed, or it was
            # deleted — the case the user explicitly asked to have surfaced.
            return ReadbackJudgement(
                ReadbackVerdict.NOT_LIVE,
                "not_found",
                f"read {len(cards)} work(s) from the creator centre and none "
                "matches the published title",
                detail={"cards_seen": len(cards)},
            )
        if list_empty:
            return ReadbackJudgement(
                ReadbackVerdict.NOT_LIVE,
                "not_found",
                "the creator centre reports this account has no works at all",
                detail={"cards_seen": 0, "list_empty": True},
            )
        # Zero cards AND no empty state: the page did not render, or our card
        # selectors no longer match it. Not evidence about the post.
        return ReadbackJudgement(
            ReadbackVerdict.INCONCLUSIVE,
            "list_unreadable",
            "could not read the works list (no cards found and no empty-state "
            "marker) — the read-back reached the page but learned nothing",
            detail={"cards_seen": 0, "list_empty": False},
        )

    states = [classify_work_state(card.text) for card in matches]
    counters = {"cards_seen": len(cards), "matched": len(matches)}

    for card, state in zip(matches, states):
        if state is WorkState.LIVE:
            return ReadbackJudgement(
                ReadbackVerdict.LIVE,
                "live",
                "the post is live on the platform",
                item_id=card.item_id,
                published_url=build_published_url(card.item_id),
                detail=dict(counters),
            )

    # Nothing live. Report the most actionable of the remaining states: a
    # refusal is something the user must act on, a review is something they
    # wait out, so REJECTED outranks the rest.
    for wanted, reason, message in (
        (WorkState.REJECTED, "rejected", "the platform refused this post"),
        (
            WorkState.UNDER_REVIEW,
            "under_review",
            "the platform is still reviewing this post",
        ),
        (
            WorkState.SCHEDULED,
            "still_scheduled",
            "the platform still lists this post as scheduled, past its go-live "
            "time",
        ),
    ):
        for card, state in zip(matches, states):
            if state is wanted:
                return ReadbackJudgement(
                    ReadbackVerdict.NOT_LIVE,
                    reason,
                    message,
                    item_id=card.item_id,
                    published_url=build_published_url(card.item_id),
                    detail=dict(counters),
                )

    # Matched, but the status vocabulary is one we do not know. Deliberately
    # INCONCLUSIVE rather than NOT_LIVE: a relabelled status is OUR gap, and
    # blocking the user's work item over our own vocabulary drift would be the
    # same class of mistake as calling it done.
    return ReadbackJudgement(
        ReadbackVerdict.INCONCLUSIVE,
        "unknown_work_state",
        "found the post but could not recognise the status the platform shows "
        "for it",
        item_id=matches[0].item_id,
        published_url=build_published_url(matches[0].item_id),
        detail=dict(counters),
    )


# --- the impure half --------------------------------------------------------


async def _card_hrefs(node: Any, limit: int = 8) -> list[str]:
    """Watch links inside one card. Bounded — a card has one, and an unbounded
    loop over a mis-scoped root would walk the entire page."""
    try:
        links = node.locator('a[href*="/video/"]')
        count = await links.count()
    except Exception:
        return []
    out: list[str] = []
    for index in range(min(count, limit)):
        try:
            href = await links.nth(index).get_attribute("href")
        except Exception:
            continue
        if href:
            out.append(href)
    return out


async def read_work_cards(page: Any, limit: int = MAX_CARDS) -> list[WorkCard]:
    """Read up to `limit` work cards off the manage page.

    Fails soft, like everything in `dom`: a locator that races a re-render
    yields fewer cards, never an exception. A short list is safe because zero
    cards is handled as "no answer", not as "no post".

    The first selector that resolves to anything wins, and results are
    de-duplicated: the structural fallback in `CARD_SELECTORS` matches nested
    wrappers, which would otherwise report one post several times and inflate
    `cards_seen` — the very number the "we really did read the list" judgement
    rests on.
    """
    for selector in CARD_SELECTORS:
        try:
            locator = page.locator(selector)
            count = await locator.count()
        except Exception:
            continue
        if not count:
            continue

        cards: list[WorkCard] = []
        seen_ids: set[str] = set()
        seen_text: set[str] = set()
        for index in range(min(count, limit)):
            try:
                node = locator.nth(index)
                text = await node.inner_text()
            except Exception:
                continue
            if not text or not text.strip():
                continue
            card = WorkCard(text=text, hrefs=tuple(await _card_hrefs(node)))
            item_id = card.item_id
            if item_id is not None:
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
            else:
                key = normalize_title(text)
                if key in seen_text:
                    continue
                seen_text.add(key)
            cards.append(card)
        if cards:
            return cards
    return []


async def list_is_empty(page: Any) -> bool:
    """Does the page render its own "no works" state?

    `exact=True` — see the note on `LIST_EMPTY_MARKERS` for why a miss here is
    the safe direction.
    """
    return bool(await visible_marker_texts(page, LIST_EMPTY_MARKERS, exact=True))


async def verify_publish(page: Any, title: str) -> ReadbackJudgement:
    """Read an already-loaded manage page → judgement.

    Navigation deliberately lives in `verify.py`, so this whole judgement path
    is exercisable against a page whose content was set directly — no network,
    no creator account.
    """
    cards = await read_work_cards(page)
    empty = False if cards else await list_is_empty(page)
    return judge_readback(cards, title, list_empty=empty)


SPEC = VerifySpec(
    platform=PLATFORM,
    works_url=WORKS_URL,
    creator_hosts=CREATOR_HOSTS,
    # Reused verbatim from the validator so the two halves cannot drift on what
    # "logged out" looks like.
    login_text_markers=LOGIN_TEXT_MARKERS,
    read=verify_publish,
)

register_verify_spec(PLATFORM, SPEC)


__all__ = [
    "CARD_SELECTORS",
    "LIST_EMPTY_MARKERS",
    "LIVE_MARKERS",
    "MAX_CARDS",
    "REJECTED_MARKERS",
    "SCHEDULED_MARKERS",
    "SPEC",
    "UNDER_REVIEW_MARKERS",
    "VIDEO_URL_TEMPLATE",
    "WORKS_URL",
    "WorkCard",
    "WorkState",
    "build_published_url",
    "classify_work_state",
    "extract_item_id",
    "judge_readback",
    "list_is_empty",
    "match_cards",
    "normalize_title",
    "read_work_cards",
    "verify_publish",
]
