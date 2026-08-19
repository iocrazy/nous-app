"""The 「选择音乐」 panel's chart tabs, harvested into structured tracks.

What this is for
================
The panel's tab row (推荐 / 热门榜 / 收藏 / 飙升榜 / 原创榜 + seven category
tabs) is the thing a user actually browses music with, and none of it is
reachable from our side as an ordinary API call. [实测 2026-08-19], through the
replay ladder in `session_probe`:

    /web/api/media/music/list   原样重放 → 200, songs 18 首
                                去 cookie / 去 UA / 去任一参数 → status_code=8

Every mutation fails, including dropping a single `cursor`. The signature
(`msToken` + `a_bogus`) is bound to the whole query, so we cannot compose one of
these URLs ourselves — the request has to be made *by the platform's own page*.

So the only way to read a chart is to open the panel and take the tab, and this
module is the flow that does it. It is deliberately NOT built on
`/session/probe`, which can also click those tabs:

* that path exists to *measure*, and its report redacts long digit runs — which
  is exactly the shape of a `music_id`, so the one field this feature is about
  would come back masked;
* its activation vocabulary is a closed allow-list whose stated purpose is
  keeping recon from touching anything that commits. Widening it to carry a
  product feature's traffic would erode a safety boundary to save writing a
  flow.

What was measured, and what follows from it
===========================================
[实测 2026-08-19, one run against a live creator account]

* opening the panel fires **two** requests: `music/category` (the tab list) and
  `music/list` for 推荐. So the first chart costs no extra click.
* taking each of 热门榜 / 飙升榜 / 原创榜 / 收藏 / 推荐 fired exactly one
  `music/list` apiece, carrying that tab's own `category_id` + `type`.
* **the tab row only exists before a search.** Once a query has been typed the
  dialog shows results and the captions are gone (they measured `matches=0`).
  Hence this flow never types anything.
* 收藏 came back with a 137-byte body and no `songs`. That is a real state —
  the account has no favourites — and it is reported as an EMPTY chart, never
  as a failed fetch. The two are different facts and only one of them is worth
  retrying.
* 「推荐」 and 「收藏」 share `category_id="1"` and are told apart by `type`
  only, so a store keyed on the id alone silently merges them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

logger = logging.getLogger("nous_browser.douyin_music_charts")

#: The panel's two endpoints [实测 2026-08-19]. Matched as substrings of the
#: full URL: the query carries a signature we neither read nor reproduce.
CATEGORY_URL_FRAGMENT = "/web/api/media/music/category"
LIST_URL_FRAGMENT = "/web/api/media/music/list"

#: Response bodies above this are not parsed. The measured ones were ~1 KB
#: (categories) and ~7 KB (a 20-track chart).
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024

#: How many charts one harvest will take. The measured tab row had 12 entries;
#: the ceiling is not a prediction that it stays 12, it is a refusal to let a
#: tab row that grew to fifty turn one harvest into fifty page interactions.
MAX_CHARTS = 16


@dataclass(frozen=True)
class MusicCategory:
    """One tab, as the panel's own category endpoint described it.

    `kind` is `type` in the payload — `recommend` / `rank` / `fav` /
    `category`. It is carried and stored because **it is load-bearing, not
    decoration**: 推荐 and 收藏 both answer `category_id="1"` and differ only
    here, so anything keyed on the id alone merges two different charts into
    one.
    """

    category_id: str
    name: str
    kind: str

    @property
    def key(self) -> str:
        """The identity a store must key on. Never the id by itself."""
        return f"{self.kind}:{self.category_id}"


@dataclass(frozen=True)
class ChartSong:
    """One track from a chart list.

    A DIFFERENT type from `douyin_music_catalog.CatalogSong` on purpose. The
    two come from two endpoints with two field vocabularies (`music_name` here,
    `title` there) and two purposes: that one exists to identify a row we are
    about to click, this one exists to be shown to a user who is choosing. One
    struct spanning both would have to make every field of each optional, and
    "which of these fields is actually populated" would move from the type into
    the reader's head.

    `user_count` is `None` for "the payload did not say", never `0` — zero is a
    real catalogue value and a track with exactly that produced a production
    refusal on 2026-08-17. Same rule the row parser already follows.
    """

    music_id: str
    name: str
    author: str = ""
    duration_s: int = 0
    user_count: int | None = None
    cover_url: str = ""
    play_url: str = ""


@dataclass
class ChartRead:
    """One chart, plus whether we actually read it.

    `ok=True, songs=()` and `ok=False` are DIFFERENT and both happen: the first
    is 收藏 on an account with no favourites (measured: a 137-byte body with no
    `songs` key), the second is a fetch that never landed. Collapsing them would
    make an empty favourites list look like a broken harvest forever, and a
    broken harvest look like an empty chart — the second being the one that
    would quietly ship a blank tab to the user.
    """

    category: MusicCategory
    songs: tuple[ChartSong, ...] = ()
    ok: bool = False
    #: Why not, when `ok` is false. Free text, short, never a page string.
    error: str = ""
    #: The platform's own paging cursor, carried so a later "load more" has
    #: somewhere to start. Empty when the chart did not say.
    cursor: str = ""
    has_more: bool = False


@dataclass
class HarvestResult:
    charts: list[ChartRead] = field(default_factory=list)
    categories: tuple[MusicCategory, ...] = ()
    #: Set when the category endpoint itself never answered — which is not
    #: "there are no charts" but "we do not know what the charts are", and the
    #: caller must not overwrite a stored tab list with an empty one on it.
    categories_error: str = ""


def read_categories(payload: Any) -> list[MusicCategory]:
    """Category response → the tabs it listed, in the order it listed them.

    Pure and total. An entry missing its id, its name **or** its type is
    dropped: without `type` the entry cannot be told apart from the other one
    sharing its id, and a half-identified tab is worse than a missing one
    because it looks addressable.
    """
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("categories")
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[MusicCategory] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        category_id = str(item.get("category_id") or "").strip()
        name = str(item.get("category_name") or "").strip()
        kind = str(item.get("type") or "").strip()
        if not category_id or not name or not kind:
            continue
        out.append(MusicCategory(category_id=category_id, name=name, kind=kind))
    return out


def read_chart_songs(payload: Any) -> list[ChartSong]:
    """List response → its tracks, in chart order.

    Pure and total. The wire shape is the measured one: `music_id` arrives as a
    JSON **string** here (unlike the search endpoint, which ships both a lossy
    `id` and an `id_str`), so it is read as written and never coerced through a
    number.
    """
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("songs")
    if not isinstance(raw, (list, tuple)):
        return []

    out: list[ChartSong] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        music_id = str(item.get("music_id") or "").strip()
        name = str(item.get("music_name") or "").strip()
        if not music_id or not name:
            continue
        try:
            duration_s = max(0, int(item.get("duration") or 0))
        except (TypeError, ValueError):
            duration_s = 0
        # `raw.get(...) or 0` would erase the distinction this field exists to
        # keep: a track really can be used by zero people.
        raw_count = item.get("user_count")
        try:
            user_count = None if raw_count is None else max(0, int(raw_count))
        except (TypeError, ValueError):
            user_count = None
        out.append(
            ChartSong(
                music_id=music_id,
                name=name,
                author=str(item.get("music_author") or "").strip(),
                duration_s=duration_s,
                user_count=user_count,
                cover_url=str(item.get("cover_url") or "").strip(),
                play_url=str(item.get("play_url") or "").strip(),
            )
        )
    return out


def read_paging(payload: Any) -> tuple[str, bool]:
    """`(cursor, has_more)` off a list response. Pure and total."""
    if not isinstance(payload, Mapping):
        return "", False
    cursor = payload.get("cursor")
    return (
        "" if cursor is None else str(cursor).strip(),
        bool(payload.get("has_more")),
    )


def payload_is_a_chart(payload: Any) -> bool:
    """Did this body come back as a chart at all. Pure.

    `songs` present — even as an empty list — means the platform answered the
    question. Its ABSENCE is what separates "no favourites" from "this response
    is not a chart", and 收藏's measured 137-byte body has neither key, so it
    reads as an empty chart via `status_code` instead.
    """
    if not isinstance(payload, Mapping):
        return False
    if "songs" in payload:
        return True
    # A well-formed refusal still carries the platform's own status. `0` is
    # success, and a successful response with no `songs` is an empty chart.
    try:
        return int(payload.get("status_code", -1)) == 0
    except (TypeError, ValueError):
        return False


class MusicChartRecorder:
    """Collects the panel's two response kinds off the page. Impure, bounded.

    Unlike `MusicCatalogRecorder`, which only ever needs the newest search, this
    one has to attribute each list response to **the tab that caused it** — so
    the caller marks a phase before clicking and the response lands under it.
    Order alone would not do: a tab that fires nothing must stay empty rather
    than silently inherit the previous tab's list, which is the single most
    plausible way this harvest could publish one chart's songs under another
    chart's name.
    """

    def __init__(self) -> None:
        self.category_payload: Any = None
        self._lists: dict[str, Any] = {}
        self._phase: str = ""
        self._pending: list[Any] = []
        self._page: Any = None

    def attach(self, page: Any) -> None:
        self._page = page
        page.on("response", self._schedule)

    def detach(self) -> None:
        page, self._page = self._page, None
        if page is None:
            return
        try:
            page.remove_listener("response", self._schedule)
        except Exception as exc:  # noqa: BLE001 - detaching must never fail a run
            logger.debug("chart listener detach failed: %s", type(exc).__name__)

    def phase(self, name: str) -> None:
        """Everything captured from now on belongs to this tab."""
        self._phase = name

    def _schedule(self, response: Any) -> None:
        try:
            url = str(getattr(response, "url", "") or "")
        except Exception:  # noqa: BLE001
            return
        if CATEGORY_URL_FRAGMENT not in url and LIST_URL_FRAGMENT not in url:
            return
        import asyncio

        task = asyncio.ensure_future(self._absorb(response, url, self._phase))
        self._pending.append(task)

    async def _absorb(self, response: Any, url: str, phase: str) -> None:
        try:
            if int(getattr(response, "status", 0)) != 200:
                return
            body = await response.body()
            if body is None or len(body) > MAX_PAYLOAD_BYTES:
                return
            payload = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            logger.debug("chart body unreadable: %s", type(exc).__name__)
            return
        if CATEGORY_URL_FRAGMENT in url:
            self.category_payload = payload
            return
        # Last write wins WITHIN a phase: a tab that re-queries (paging, a
        # retry) leaves the newest list, which is the one on screen.
        self._lists[phase] = payload

    async def settle(self) -> None:
        if not self._pending:
            return
        import asyncio

        pending, self._pending = self._pending, []
        await asyncio.gather(*pending, return_exceptions=True)

    def list_payload(self, phase: str) -> Any:
        return self._lists.get(phase)


def build_chart(category: MusicCategory, payload: Any) -> ChartRead:
    """One captured list response → a `ChartRead`. Pure.

    `payload is None` means the tab was taken and nothing came back, which is
    reported as a failed read rather than an empty chart — see `ChartRead`.
    """
    if payload is None:
        return ChartRead(category=category, ok=False, error="no response captured")
    if not payload_is_a_chart(payload):
        return ChartRead(category=category, ok=False, error="response was not a chart")
    cursor, has_more = read_paging(payload)
    return ChartRead(
        category=category,
        songs=tuple(read_chart_songs(payload)),
        ok=True,
        cursor=cursor,
        has_more=has_more,
    )


def plan_tabs(
    categories: Sequence[MusicCategory], *, opened_with: str, limit: int = MAX_CHARTS
) -> list[MusicCategory]:
    """Which tabs still have to be clicked, in order. Pure.

    `opened_with` is the `key` of the chart the panel already delivered on open
    (推荐, measured) — clicking it again would spend an interaction to re-fetch
    what we have. Everything else is taken once, capped, and the cap is the
    caller's to report: a harvest that silently stopped at sixteen would look
    exactly like a platform that only has sixteen tabs.
    """
    out: list[MusicCategory] = []
    seen = {opened_with}
    for category in categories:
        if category.key in seen:
            continue
        seen.add(category.key)
        out.append(category)
        if len(out) >= max(0, limit):
            break
    return out


__all__ = [
    "CATEGORY_URL_FRAGMENT",
    "LIST_URL_FRAGMENT",
    "MAX_CHARTS",
    "ChartRead",
    "ChartSong",
    "HarvestResult",
    "MusicCategory",
    "MusicChartRecorder",
    "build_chart",
    "payload_is_a_chart",
    "plan_tabs",
    "read_categories",
    "read_chart_songs",
    "read_paging",
]
