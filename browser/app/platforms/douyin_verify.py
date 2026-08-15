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

The selectors, and what a live account said about them
======================================================
[实测 2026-08-11] `CARD_SELECTORS` was finally counted against a bound account
(12 works rendered, 5 image posts + 7 videos, via the read-only inspect
endpoint). Three things came back, and all three are now designed for:

  * only ONE of the four candidates matches — `[class*="video-card"]`.
    `content-card` 0, `work-card` 0, `[class^="card-"]` 2 (page chrome, not
    works). The list keeps all four anyway: they cost one `count()` each and
    they are the fallbacks for a console that moves.
  * the platform uses the SAME `video-card` class family for image posts
    (图文) as for videos — 72 matches ÷ 12 works = 6 nodes each, uniform
    across both content types. There is no image-specific card shape to
    detect, which is why nothing in this module branches on content type.
  * those 72 matches are **6 per card, not 1** — the card root plus five
    descendants that carry the same class prefix. See `read_work_cards` for
    why that made the reader blind past the fourth work, and what fixes it.

What is still NOT verified is the status vocabulary beyond 「已发布」: that
account had no work in 审核中 / 定时中 / 未通过 / 已下架 at the time (the
non-zero counts for two of those words came from the page's own filter tabs,
not from cards). Those markers remain INFERRED — see `_STATE_MARKERS`.

So the design still makes being wrong SAFE rather than plausible-looking,
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

⚠️ The one hole in that argument, stated because it was reachable and is now
only bounded, not closed: reading a PARTIAL list is indistinguishable from
reading a complete one, so a work that exists but sits past `MAX_CARDS` still
reads as `not_found`. That is exactly how the node-vs-work bug produced false
"deleted" verdicts (see `outermost_only`). The bound is now 24 WORKS against a
page that renders 12 per load, but a console that lazily renders fewer, or an
account whose target post is old enough to be paginated away, would hit it
again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Sequence
from urllib.parse import urlsplit

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
# [实测 2026-08-11] Re-counted, and it is stronger than "no watch links": the
# whole manage page contains **zero `<a>` elements** — `a` 0, `a[href*=
# "/video/"]` 0, `a[href*="/note/"]` 0, `a[target="_blank"]` 0, with 12 works
# of both content types on screen. So this is not "videos link and image posts
# use some other path"; nothing on that page is an anchor at all.
#
# That measurement is also why this stayed a single pattern instead of growing
# into a `/video/`-or-`/note/` candidate list, which is what the image-post
# design originally proposed: a list of dead selectors is still dead, and it
# would advertise a URL back-fill that cannot happen.
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
#
# [实测 2026-08-11] on a bound account with 12 works on screen:
#   [class*="content-card"]  0
#   [class*="work-card"]     0
#   [class*="video-card"]   72   ← the one that matches, and it over-matches 6:1
#   [class^="card-"]         2   (page chrome)
CARD_SELECTORS: tuple[str, ...] = (
    '[class*="content-card"]',
    '[class*="work-card"]',
    '[class*="video-card"]',
    '[class^="card-"]',
)


def outermost_only(selector: str) -> str:
    """`selector`, restricted to matches that no other match contains. Pure.

    **This is the difference between counting works and counting nodes**, and
    getting it wrong was a live bug rather than a theoretical one.

    The console builds each card out of a root plus five descendants that all
    carry the same CSS-Modules class prefix, so `[class*="video-card"]`
    resolves to 72 nodes for 12 works ([实测 2026-08-11]). Reading those nodes
    directly meant `read_work_cards`'s `limit` was spent on the first four
    cards, every work past position four was invisible, and `judge_readback`
    — correctly, given what it was handed — called those works `not_found`,
    i.e. NOT_LIVE. A false "your post is gone" is precisely the verdict this
    module's whole design exists to make impossible, and it was reachable.

    Image posts were hit hardest: on the account measured, four of the five
    图文 works sat at positions 8-11.

    `:not(SEL *)` keeps only the shallowest match of each nesting chain, which
    on that same page turns 72 into exactly 12. Verified through the real
    Playwright engine (the inspect endpoint counts with `page.locator`), not
    assumed to be supported.

    ⚠️ Residual hazard, stated rather than papered over: if a console ever
    wraps the whole list in a node that ALSO matches, the outermost match is
    that wrapper and one "card" would carry every work's text — which could
    mis-report one work's status as another's. Today's page does not do this
    for the selector that wins (`[class*="video-card"]:not(...)` = 12, not 1)
    and the generic `[class*="card"]` form that DOES collapse to 1 is not in
    `CARD_SELECTORS`. This exposure is not new — the pre-existing structural
    fallback `[class^="card-"]` had it too — but it is not fixed here either.
    """
    return f"{selector}:not({selector} *)"


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
#
# ⚠️ How much of this table is actually VERIFIED, and how much is inferred.
#
# [实测 2026-08-11] Image posts and videos are listed in one table and use the
# SAME status vocabulary, so this table needed no image-specific entry. But the
# only card status observed on a live account was 「已发布」 (12 of 12 works).
# 「审核中」 and 「未通过」 counted 1 each — those were the page's own filter
# TABS, not cards; 「定时中」 and 「已下架」 counted 0. So "image posts use the
# same words" is confirmed only for the published state; for every other state
# it remains an INFERENCE, and confirming it needs a work actually sitting in
# that state (a scheduled image post would do it, and T7 sends one).
#
# 「公开」 was DELETED from LIVE_MARKERS on the strength of that same read, and
# the reasoning is the one that matters here: it counted **0 on the whole
# page** — the manage cards do not show a visibility word at all (「仅自己可见」
# and 「好友可见」 also counted 0). So it bought nothing, while being a
# two-character word common enough to appear inside a CAPTION — and the caption
# is part of the text these markers match. A card in a status we do not
# recognise whose caption happened to contain 「公开」 would have classified
# LIVE, i.e. a work item closed as published on the strength of the user's own
# prose. Dropping it moves that case to UNKNOWN → INCONCLUSIVE → ask a human,
# which is the direction this module always takes when it is unsure.
# 「仅自己可见」/「好友可见」 stay despite also counting 0: they are long and
# distinctive enough not to collide with prose, and they carry the product
# guarantee above.
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
)

_STATE_MARKERS: tuple[tuple[WorkState, tuple[str, ...]], ...] = (
    (WorkState.REJECTED, REJECTED_MARKERS),
    (WorkState.UNDER_REVIEW, UNDER_REVIEW_MARKERS),
    (WorkState.SCHEDULED, SCHEDULED_MARKERS),
    (WorkState.LIVE, LIVE_MARKERS),
)

# How many cards to read. The list is paginated and the post we want is among
# the most recent; walking the whole history would cost minutes and add nothing.
#
# ⚠️ This bounds WORKS, and only does so because `read_work_cards` matches card
# roots via `outermost_only`. Applied to raw selector matches it bounded NODES
# instead — 24 nodes is 4 works on the live console — and the works past that
# point read as `not_found`. Anything that reintroduces nested matches here
# reintroduces a false "your post is gone".
#
# [实测 2026-08-11] the live page renders 12 works per load (34 on the account,
# the rest behind lazy loading), so 24 leaves real headroom.
MAX_CARDS = 24

# Below this many characters a title is too weak to match on as a substring of
# the WHOLE card — 「测试」 would match half the account. Short titles match a
# single rendered LINE of the card instead (see `match_cards` / `caption_lines`).
#
# ⚠️ This number has no derivation. It was picked when the module was written
# and no measurement, spec or plan justifies 6 rather than 4 or 8 — said out
# loud because the previous short-title rule looked equally deliberate and was
# structurally unsatisfiable. What the number now controls is only "when may a
# title be matched loosely against the whole card", and BOTH sides of the cliff
# are now reachable, which is the property that was missing.
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


def card_lines(text: str) -> tuple[str, ...]:
    """One card's rendered text, split into normalised non-empty lines. Pure.

    Split BEFORE normalising, because `normalize_title` collapses every run of
    whitespace — newlines included — into single spaces. Normalising first and
    splitting after would always yield exactly one line, which is the same
    class of structurally-impossible code this function exists to remove.
    """
    lines = (normalize_title(part) for part in text.splitlines())
    return tuple(line for line in lines if line)


# Text a card renders that is the console's, not the user's. Excluded from the
# short-title match below so that a caption which happens to BE one of these
# words cannot borrow another post's card.
#
# Two sources, both from the same live read ([实测 2026-08-11], recorded in the
# fixture header): the status vocabulary already tabulated above, and the four
# operation words every card carries verbatim (编辑作品 设置权限 作品置顶
# 删除作品). The regexes cover the badge, which is a duration on a video card
# and 「{N}张」 on an image card — the only other short, per-card, repeating
# text observed.
_CARD_CHROME_LINES: frozenset[str] = frozenset(
    normalize_title(word)
    for word in (
        *REJECTED_MARKERS,
        *UNDER_REVIEW_MARKERS,
        *SCHEDULED_MARKERS,
        *LIVE_MARKERS,
        "编辑作品",
        "设置权限",
        "作品置顶",
        "删除作品",
    )
)
_CARD_CHROME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d{1,4}张$"),  # image-post badge
    re.compile(r"^\d{1,3}:\d{2}$"),  # video duration badge
)


def caption_lines(text: str) -> tuple[str, ...]:
    """`card_lines(text)` minus the console's own chrome. Pure.

    What is left is everything the user could plausibly have typed as the
    caption. It is deliberately a filter over the whole card rather than an
    attempt to pick THE caption line: which line that is depends on markup
    nobody has measured, whereas "not one of the words the console puts on
    every card" needs only the vocabulary already tabulated here.
    """
    return tuple(
        line
        for line in card_lines(text)
        if line not in _CARD_CHROME_LINES
        and not any(pattern.match(line) for pattern in _CARD_CHROME_PATTERNS)
    )


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


# The distinctive part of a card selector, for the compact probe line. Falls
# back to the whole literal rather than to a guess: an unlabelled selector in a
# diagnostic is still readable, a mislabelled one is a lie.
_SELECTOR_LABEL = re.compile(r'\[class[*^$]?="([^"]+)"\]')


def probe_label(selector: str) -> str:
    """Short name for `selector` in the probe line. Pure."""
    match = _SELECTOR_LABEL.search(selector)
    return match.group(1) if match else selector


def _num(value: int | None) -> str:
    """A count, or `?` when it could not be measured. Pure.

    **`None` must never render as `0`.** A probe that failed and a page that
    genuinely has none of something are opposite findings, and a diagnostic
    whose broken output is shaped like a negative answer is not evidence —
    the same rule as `readyz`'s three-state `dbos` field.
    """
    return "?" if value is None else str(value)


# Path of `WORKS_URL`, so "are we still on the works page" is derived from the
# one constant that says where the works page is, not from a second copy of it.
_WORKS_PATH = urlsplit(WORKS_URL).path.rstrip("/")


def page_label(url: str) -> str:
    """Coarse name for the page we actually landed on. Pure.

    A LABEL, never the URL. `verify_detail` lands in a database row and in
    logs, and this repository is public — a creator-console URL can carry
    query parameters, so the raw string does not leave this function. The
    labels are derived from constants this module already owns.

    Answers the question no count could: `not_found` on a works page and
    `not_found` on some other page that merely rendered are the same output
    today, and they are completely different bugs.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if not host:
        return "unknown"
    if host not in CREATOR_HOSTS:
        return "off-host"
    path = (parts.path or "/").rstrip("/")
    if path == _WORKS_PATH:
        return "manage"
    if "login" in path:
        return "login"
    if path.startswith("/creator-micro"):
        return "creator-other"
    return "other"


# Login vocabulary DELIBERATELY WIDER than `LOGIN_TEXT_MARKERS`. That tuple is
# the one `verify.py` gates on before the list is ever read, so by the time
# this module runs it has already measured 0 — re-counting it would only
# restate a decision that was already taken. These are the words a login screen
# would carry that our gate does NOT recognise, which is exactly the blind spot
# that turns "logged out" into "this account has no works".
#
# ⚠️ CANDIDATES, not measurements. Nobody has counted these against a live
# logged-out console; a zero here means "not seen", never "not there".
# `exact=True` throughout, which is also what keeps 「退出登录」 (a logged-IN
# control) from being read as a login prompt.
LOGIN_MARKER_CANDIDATES: tuple[str, ...] = (
    "登录",
    "验证码登录",
    "抖音号登录",
    "登录后可查看",
    "请先登录",
)

# Page chrome the works page itself should carry, as opposed to its cards.
# ⚠️ Also CANDIDATES — the 2026-08-11 read recorded card vocabulary only, so
# nothing here has ever been counted.
WORKS_PAGE_MARKER_CANDIDATES: tuple[str, ...] = (
    "作品管理",
    "内容管理",
    "全部作品",
    "发布视频",
)

# Nodes that mean "still rendering". If these are non-zero at the moment we
# read, the settle was too short and every count above is a measurement of a
# half-built page.
#
# ⚠️ Summed across selectors that OVERLAP — a `loading-spinner` class matches
# both `loading` and `spin`, so one node can count twice. This is a
# boolean-shaped signal ("was the page still working"), not a node census, and
# reading it as the latter would overstate.
BUSY_SELECTORS: tuple[str, ...] = (
    '[class*="loading"]',
    '[class*="skeleton"]',
    '[class*="spin"]',
)


@dataclass(frozen=True)
class PageProbe:
    """WHICH PAGE the read-back actually reached. Counts and labels only.

    `ListProbe` answers "why did the list read that way". This answers the
    question that turned out to come first: **was there a list at all**. The
    first live probe returned zero works, zero card roots under three of four
    selectors, and zero operation words — a shape that says the page had no
    works list on it, and nothing in the output could say why.
    """

    where: str | None = None
    text_len: int | None = None
    divs: int | None = None
    login_gate: int | None = None
    login_wide: int | None = None
    works_words: int | None = None
    empty_words: int | None = None
    busy: int | None = None

    def render(self) -> str:
        return (
            f"[page where={self.where or '?'}"
            f" textlen={_num(self.text_len)}"
            f" divs={_num(self.divs)}"
            f" login={_num(self.login_gate)}+{_num(self.login_wide)}"
            f" works={_num(self.works_words)}"
            f" empty={_num(self.empty_words)}"
            f" busy={_num(self.busy)}]"
        )


@dataclass(frozen=True)
class ListProbe:
    """Why the works list read the way it did. **Counts and selector literals
    only — never page content.**

    This exists because the read-back's one observable output is
    `verify_detail`, and `verify_detail` is built from `[reason] message`
    alone (`publish_readback.verdict_for` drops every other key of the detail
    dict). A read that reported `read 1 work(s)` therefore said nothing about
    WHICH of the four candidate selectors produced that 1, or whether the
    other three were even tried — and answering that took a live-account
    reconnaissance run that nobody could schedule. The numbers below ride the
    message instead, so the next occurrence answers itself.

    ⚠️ Nothing here may become content. `verify_detail` lands in a database
    row and in logs, and this repository is public: card text, captions,
    account identifiers and URLs are all forbidden. `title_exact` is a COUNT
    of nodes whose whole text equals the caption — the caption itself never
    appears.

    Every field is `int | None`, and `None` means "could not measure", which
    renders as `?`. See `_num`.
    """

    # (selector, raw matches, matches after `outermost_only`) per candidate.
    roots: tuple[tuple[str, int | None, int | None], ...] = ()
    # The candidate `read_work_cards` actually read from, if any.
    won: str | None = None
    # Cards handed to the judgement.
    cards: int | None = None
    # Nodes whose ENTIRE text is the published caption. ≥1 proves the caption
    # occupies an element of its own — which is what `match_cards`' line route
    # assumes and what no measurement had ever confirmed.
    title_exact: int | None = None
    # Nodes whose entire text is 编辑作品 / 删除作品 — the operation words the
    # console prints on every card. A card count that needs no class name.
    # ⚠️ Nodes, not cards: a wrapper whose only text is that word matches too,
    # so read these as a multiple of the works on screen, not as the works.
    op_words: tuple[int | None, int | None] = (None, None)
    # Size and line count of the first card the reader ACCEPTED — never its
    # text. `card-:3/1` told us a chrome node won the loop but not what it was;
    # a 12-character single-line "card" and a 400-character six-line one are
    # different findings, and both are readable from two integers.
    won_len: int | None = None
    won_lines: int | None = None
    # Which page this was read off, when it could be determined.
    page: PageProbe | None = None

    def render(self) -> str:
        roots = ",".join(
            f"{probe_label(sel)}:{_num(raw)}/{_num(scoped)}"
            for sel, raw, scoped in self.roots
        )
        won = probe_label(self.won) if self.won else "none"
        page = f" {self.page.render()}" if self.page is not None else ""
        return (
            f"[probe cards={_num(self.cards)} won={won}"
            f" wonlen={_num(self.won_len)}/{_num(self.won_lines)}"
            f" roots={roots or 'none'}"
            f" title_exact={_num(self.title_exact)}"
            f" ops={_num(self.op_words[0])}/{_num(self.op_words[1])}]"
            f"{page}"
        )


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

    Two ways in, and a card matches on either:

      * **any long-enough title, as a substring of the whole card.** The card's
        text is the WHOLE card — caption plus status word plus counters — so
        equality against it could never hold; a substring is the only form that
        can. `_MIN_SUBSTRING_TITLE_LEN` is the floor, because 「测试」 as a
        substring would match half the account.
      * **any title, as one whole rendered LINE of the card** (`caption_lines`).
        This is the stricter form: 「测试」 matches a card captioned exactly
        「测试」 and NOT one captioned 「测试版本」.

    ⚠️ The second route replaces a rule that was **structurally unsatisfiable**,
    and the failure was live rather than theoretical. Short titles used to be
    compared for equality against the whole card's text — the same string whose
    own docstring says it also carries the status word and the view counter. No
    caption can ever equal that, so EVERY title under
    `_MIN_SUBSTRING_TITLE_LEN` characters resolved to `not_found` → NOT_LIVE →
    a blocked work item, no matter what the platform actually showed. A post
    published with a four-character title went live, was confirmed live on the
    platform by eye, and the read-back reported it missing anyway. The floor
    counts CHARACTERS, so on Chinese captions it swallows every four-Han-
    character title — an entirely ordinary length, not just 「测试」-style
    noise.

    A miss is still preferred over a wrong match, and that ordering is why the
    replacement is line equality rather than "substring for short titles too":
    a miss retries and then asks a human, whereas a false match reports some
    OTHER post's state as this batch's outcome — including, in the worst
    direction, closing the work item as published on the strength of a
    different post being live.

    ⚠️ What the line route rests on, stated because it is an INFERENCE and
    STILL not a measurement: that the caption occupies a rendered line of its
    own, i.e. that it sits in its own block element inside the card. The
    2026-08-11 live read counted six same-prefixed nodes per card but recorded
    no class names, and the page-text excerpt it captured had already been
    whitespace-collapsed (`inspect.py` joins `body.innerText` on single
    spaces), so no line structure was ever observed.

    [2026-08-15] One live attempt to measure it has now been made and it came
    back **uninformative, not negative**. `ListProbe.title_exact` — nodes whose
    whole text is the caption — read 0, but on that same read `ops` (the
    operation words the console prints on every card) read 0 as well and every
    card selector read 0: the page carried no works list at all, so a caption
    could not have been found whatever the markup does. **A zero measured on a
    page that has nothing to measure is not evidence against the inference**,
    and writing it up as "disproved" would be the same mistake as reading an
    empty probe as a negative answer. The question is still open, and
    `title_exact` will answer it on the first read that actually reaches a
    works list.

    Either way the failure direction is unchanged: if the inference is wrong
    the short title simply does not match — the same miss as before, never a
    new false verdict.
    """
    needle = normalize_title(title)
    if not needle:
        return []
    out: list[WorkCard] = []
    for card in cards:
        haystack = normalize_title(card.text)
        if not haystack:
            continue
        long_enough = len(needle) >= _MIN_SUBSTRING_TITLE_LEN
        if (long_enough and needle in haystack) or needle in caption_lines(card.text):
            out.append(card)
    return out


def judge_readback(
    cards: Sequence[WorkCard],
    title: str,
    *,
    list_empty: bool = False,
    probe: ListProbe | None = None,
) -> ReadbackJudgement:
    """Cards + the caption we published → the verdict. Pure. Total.

    `probe` is appended to the message of the three "we did not find it"
    outcomes only — `not_found` (both forms) and `list_unreadable`. Those are
    exactly the verdicts where the counts explain the answer; a LIVE post needs
    no explanation, and a refused one is about the platform, not about our
    reading. It rides the MESSAGE rather than `detail` because the message is
    the only part that survives into `verify_detail`
    (`publish_readback.verdict_for`). Passing `None` changes nothing, which is
    what keeps this function pure and every existing caller correct.

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
    suffix = f" {probe.render()}" if probe is not None else ""

    if not matches:
        if cards:
            # We read a real list and this post is not on it. For a scheduled
            # batch past its go-live time that means it never landed, or it was
            # deleted — the case the user explicitly asked to have surfaced.
            return ReadbackJudgement(
                ReadbackVerdict.NOT_LIVE,
                "not_found",
                f"read {len(cards)} work(s) from the creator centre and none "
                f"matches the published title{suffix}",
                detail={"cards_seen": len(cards)},
            )
        if list_empty:
            return ReadbackJudgement(
                ReadbackVerdict.NOT_LIVE,
                "not_found",
                "the creator centre reports this account has no works at all"
                f"{suffix}",
                detail={"cards_seen": 0, "list_empty": True},
            )
        # Zero cards AND no empty state: the page did not render, or our card
        # selectors no longer match it. Not evidence about the post.
        return ReadbackJudgement(
            ReadbackVerdict.INCONCLUSIVE,
            "list_unreadable",
            "could not read the works list (no cards found and no empty-state "
            f"marker) — the read-back reached the page but learned nothing{suffix}",
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
    loop over a mis-scoped root would walk the entire page.

    [实测 2026-08-11] This returns `[]` on every card of today's console,
    because the page has no `<a>` elements at all — see the note on
    `_ITEM_ID_PATTERN`. It is kept rather than deleted for one reason: it is
    the only thing that would notice a console that starts exposing links, and
    `judge_readback` already treats the id as optional everywhere. Deleting it
    would turn "fill the URL when the platform offers one" into "never fill
    it", which is a different promise from the one the read-back makes.
    """
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


async def _count_or_none(page: Any, selector: str) -> int | None:
    """Matches for `selector`, or None if it could not be counted.

    None rather than 0, always — see `_num`. A locator that raises and a page
    with no such node are opposite findings.
    """
    try:
        return int(await page.locator(selector).count())
    except Exception:  # noqa: BLE001 - an unusable probe is not a crash
        return None


async def _exact_text_count(page: Any, text: str) -> int | None:
    """Nodes whose ENTIRE text is `text`, or None if it could not be counted.

    `exact=True` for the repo's usual reason (「允许」 is a substring of
    「不允许」), and because the question being asked is precisely "is there a
    node that contains this and nothing else" — a substring count cannot
    answer it.
    """
    if not text:
        return None
    try:
        return int(await page.get_by_text(text, exact=True).count())
    except Exception:  # noqa: BLE001
        return None


async def _group_count(page: Any, markers: Sequence[str]) -> int | None:
    """Total nodes matching any of `markers` exactly, or None.

    **None if ANY single probe failed**, not a partial sum. An understated
    total is worse than an honest `?`: it reads like a real observation and
    would be quoted as one, which is the whole failure mode `_num` exists to
    prevent.
    """
    total = 0
    for marker in markers:
        count = await _exact_text_count(page, marker)
        if count is None:
            return None
        total += count
    return total


async def measure_page(page: Any) -> PageProbe:
    """Which page did we land on, and had it finished rendering.

    Every field fails soft to None. Nothing here reads as content: a label
    derived from our own constants, a character count, and node counts.
    """
    try:
        url = str(getattr(page, "url", "") or "")
    except Exception:  # noqa: BLE001
        url = ""

    text_len: int | None = None
    try:
        body = await page.locator("body").inner_text()
        text_len = len(body or "")
    except Exception:  # noqa: BLE001
        text_len = None

    busy: int | None = 0
    for selector in BUSY_SELECTORS:
        count = await _count_or_none(page, selector)
        if count is None:
            busy = None
            break
        busy += count

    return PageProbe(
        where=page_label(url) if url else "unknown",
        text_len=text_len,
        divs=await _count_or_none(page, "div"),
        login_gate=await _group_count(page, LOGIN_TEXT_MARKERS),
        login_wide=await _group_count(page, LOGIN_MARKER_CANDIDATES),
        works_words=await _group_count(page, WORKS_PAGE_MARKER_CANDIDATES),
        empty_words=await _group_count(page, LIST_EMPTY_MARKERS),
        busy=busy,
    )


async def read_works_list(
    page: Any, limit: int = MAX_CARDS
) -> tuple[list[WorkCard], ListProbe]:
    """`read_work_cards`, plus the counts that explain what it did.

    **Every candidate is counted, not just the winner.** The loop below still
    takes the first candidate that yields cards — behaviour is unchanged — but
    the counting pass runs over all four first, because "which selector won"
    is only meaningful next to "what did the others see". Four extra `count()`
    calls, which is what `CARD_SELECTORS` already budgets for ("they cost one
    `count()` each").

    That distinction is the whole point: the reader returns on the FIRST
    candidate that produces any card, so a console change that makes an
    EARLIER candidate match one piece of page chrome silently prevents the
    real one from ever being tried — and the outcome looks identical to "the
    account has one work". Nothing in the old output could tell those apart.
    """
    roots: list[tuple[str, int | None, int | None]] = []
    for selector in CARD_SELECTORS:
        raw = await _count_or_none(page, selector)
        scoped = await _count_or_none(page, outermost_only(selector))
        roots.append((selector, raw, scoped))

    for selector, _raw, scoped in roots:
        if not scoped:
            continue
        try:
            locator = page.locator(outermost_only(selector))
        except Exception:  # noqa: BLE001
            continue
        cards = await _read_cards_from(locator, min(scoped, limit))
        if cards:
            return cards, ListProbe(
                roots=tuple(roots),
                won=selector,
                cards=len(cards),
                won_len=len(cards[0].text),
                won_lines=len(card_lines(cards[0].text)),
            )
    return [], ListProbe(roots=tuple(roots), won=None, cards=0)


async def measure_page_probes(page: Any, title: str, probe: ListProbe) -> ListProbe:
    """`probe` with the page-level counts filled in.

    Separate from `read_works_list` because these two need the caption and the
    list read does not, and because they answer a different question: not "did
    we find the card" but "is the page even shaped the way we think".

    Neither count ever leaves as text. `title_exact` is a number of nodes; the
    caption is used as a needle and discarded.

    The RAW title goes to the matcher, not the normalised one: Playwright
    collapses whitespace on both sides of a text match itself, whereas
    `normalize_title` also case-folds — and case-folding the needle would make
    a case-only mismatch invisible in a number that is supposed to prove the
    caption is rendered as we sent it. A blank title measures as `?`, not 0.
    """
    needle = title if normalize_title(title) else ""
    return replace(
        probe,
        title_exact=await _exact_text_count(page, needle),
        op_words=(
            await _exact_text_count(page, "编辑作品"),
            await _exact_text_count(page, "删除作品"),
        ),
        page=await measure_page(page),
    )


async def read_work_cards(page: Any, limit: int = MAX_CARDS) -> list[WorkCard]:
    """Read up to `limit` work cards off the manage page.

    Fails soft, like everything in `dom`: a locator that races a re-render
    yields fewer cards, never an exception. A short list is safe because zero
    cards is handled as "no answer", not as "no post".

    The first selector that resolves to anything wins, and each candidate is
    scoped by `outermost_only` so that one match is one WORK. Read that
    function before touching this loop: the console nests five same-prefixed
    nodes inside every card, and matching them raw made `limit` run out on the
    fourth work while reporting `cards_seen=24` — every later work, including
    most of the account's image posts, then judged `not_found`.

    Results are still de-duplicated by text on top of that, because a redesign
    can nest cards in a way the CSS scoping does not catch, and one post
    counted several times inflates `cards_seen` — the very number the "we
    really did read the list" judgement rests on.

    Kept as the cards-only door onto `read_works_list` so that callers which
    do not want the diagnostic do not have to unpack it. The selection logic
    lives there and only there — a second copy of "which selector wins" would
    be a second thing to keep true.
    """
    cards, _ = await read_works_list(page, limit)
    return cards


async def _read_cards_from(locator: Any, limit: int) -> list[WorkCard]:
    """Up to `limit` cards off an already-chosen locator. Fails soft."""
    cards: list[WorkCard] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for index in range(limit):
        try:
            node = locator.nth(index)
            text = await node.inner_text()
        except Exception:  # noqa: BLE001
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
    return cards


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

    The probe is measured on EVERY read, including the ones that end LIVE, and
    only rendered where it explains something (see `judge_readback`). Measuring
    conditionally would mean deciding the verdict before deciding what to
    measure, and the page is already loaded — three more counts against it are
    not worth a second code path.
    """
    cards, probe = await read_works_list(page)
    empty = False if cards else await list_is_empty(page)
    probe = await measure_page_probes(page, title, probe)
    return judge_readback(cards, title, list_empty=empty, probe=probe)


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
    "BUSY_SELECTORS",
    "ListProbe",
    "LOGIN_MARKER_CANDIDATES",
    "MAX_CARDS",
    "PageProbe",
    "WORKS_PAGE_MARKER_CANDIDATES",
    "REJECTED_MARKERS",
    "SCHEDULED_MARKERS",
    "SPEC",
    "UNDER_REVIEW_MARKERS",
    "VIDEO_URL_TEMPLATE",
    "WORKS_URL",
    "WorkCard",
    "WorkState",
    "build_published_url",
    "caption_lines",
    "card_lines",
    "classify_work_state",
    "extract_item_id",
    "judge_readback",
    "list_is_empty",
    "match_cards",
    "measure_page",
    "measure_page_probes",
    "normalize_title",
    "outermost_only",
    "page_label",
    "probe_label",
    "read_work_cards",
    "read_works_list",
    "verify_publish",
]
