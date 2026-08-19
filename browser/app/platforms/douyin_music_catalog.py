"""The dialog's own search response, paired back onto the rows it rendered.

Why this module exists
======================
Picking the right track out of the 「选择音乐」 dialog has been decided by a
*fingerprint* — title, author, running time — scraped off the rendered rows.
That was the best identity available, and `MusicReference` says so in as many
words:

    `music_id` rides along because it is the only real identity: it cannot
    address a dialog row today (whether rows carry an id attribute has never
    been measured) ...

[实测 2026-08-19] That measurement now exists, and the answer is better than
the question assumed. The rows carry no id attribute — but the **response the
dialog rendered them from** carries one per track:

    GET tsearch.amemv.com/openapi/aweme/v1/music/search/?keyword=...
    → {"status_code": 0,
       "music": [{"id_str": "5000000000201577412", "title": "海阔天空",
                  "author": ..., "duration": 30, ...}, ...]}

So the identity is reachable without any DOM archaeology: listen for that
response, and row *i* is `music[i]`.

Why the pairing is proven rather than assumed
=============================================
"Row *i* is `music[i]`" is an assumption about rendering order, and an
assumption that silently drifts is exactly how this file's neighbours have gone
wrong before: a pinned result, an ad slot, or a row the parser dropped would
shift every index after it, and clicking the row **next to** the chosen one is
indistinguishable from clicking the right one until the post is live.

So `align_catalog` does not take the order on faith. It checks every rendered
row's title against the payload entry at the same index, and a single
disagreement withdraws the whole alignment. The result is three states that
stay tellable apart — aligned / captured-but-misaligned / never captured — for
the same reason `MusicRow.author` distinguishes "" from `None`: a mechanism
whose broken state looks like its negative answer is not evidence.

What this does NOT do
=====================
It does not widen what gets clicked. When the alignment holds and the id is
found, the click is *more* certain than the fingerprint could ever be. When it
does not hold, this module returns nothing at all and the existing fingerprint
judge decides exactly as it did before — including refusing. Falling back to a
looser rule on a failed alignment would reintroduce the "publish a guess" move
the whole path was built to refuse.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger("nous_browser.douyin_music_catalog")

#: The dialog's search endpoint [实测 2026-08-19]. Matched as a substring of the
#: full URL rather than by host+path equality: the host carries a shard prefix
#: that is not ours to predict, and the path is the stable half.
MUSIC_SEARCH_URL_FRAGMENT = "/openapi/aweme/v1/music/search"

#: How many payloads a single dialog session will hold. One search produces one
#: response; the ceiling exists so a dialog that re-queries on every keystroke
#: cannot grow this without bound, and it keeps the LAST ones because the last
#: search is the one whose rows are on screen.
MAX_CAPTURED_PAYLOADS = 4

#: Response bodies above this are not parsed. The measured one was 109 KB; the
#: ceiling is generous enough to leave that untouched while refusing to pull an
#: unbounded body into memory on a page we do not control.
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024

#: Alignment verdicts. Strings rather than an enum because they are rendered
#: into the diagnostic verbatim and read by a human in a stored error message.
ALIGN_OK = "aligned"
ALIGN_NOT_CAPTURED = "not_captured"
ALIGN_NO_ROWS = "no_rows"
ALIGN_SHORT = "short"
ALIGN_TITLE_MISMATCH = "title_mismatch"


@dataclass(frozen=True)
class CatalogSong:
    """One track exactly as the platform's own search response described it.

    Deliberately NOT a `MusicRow`: a row is what we managed to read off a
    screen, this is what the platform said. Keeping the two types apart is what
    stops a later reader from assuming a value came from the page when it came
    from the payload, or the reverse.

    `duration_s` is `0` (not `None`) when the payload omitted it, matching
    `MusicReference.duration_s`'s contract — this side never *reads* a
    duration, it only carries what was given, so there is no "we could not
    read it" state to preserve.
    """

    index: int
    music_id: str
    name: str
    author: str = ""
    duration_s: int = 0


def read_search_catalog(payload: Any) -> list[CatalogSong]:
    """One search response → the tracks it listed, in the order it listed them.

    Pure and total: any shape that is not the measured one yields an empty
    list, and an empty list is reported by `align_catalog` as
    ``not_captured`` — never as "the platform listed nothing", which is a
    different fact that only the row reader can establish.

    Only the ``music`` key is read. The payload also carries
    ``music_info_list``, which was NOT inspected on 2026-08-19; reading a key
    whose shape we never measured is how a made-up selector gets into this
    codebase, and the one thing worse than missing a track here is inventing
    one.

    Entries without an ``id_str`` **or** without a title are dropped rather
    than filled in, because both are load-bearing: the id is the whole reason
    this module exists, and the title is what proves the alignment. Dropping
    shifts the index, which `align_catalog` then catches as a mismatch — the
    failure is loud, not silent.
    """
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("music")
    if not isinstance(raw, (list, tuple)):
        return []

    out: list[CatalogSong] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        # `id_str` and not `id`: the numeric form is a Snowflake-scale integer
        # and JSON numbers lose precision above 2^53 in every consumer this
        # value passes through. The platform ships both for exactly that
        # reason; taking the wrong one produces an id that is *almost* right.
        music_id = str(item.get("id_str") or "").strip()
        name = str(item.get("title") or "").strip()
        if not music_id or not name:
            continue
        try:
            duration_s = max(0, int(item.get("duration") or 0))
        except (TypeError, ValueError):
            duration_s = 0
        out.append(
            CatalogSong(
                index=index,
                music_id=music_id,
                name=name,
                author=str(item.get("author") or "").strip(),
                duration_s=duration_s,
            )
        )
    return out


@dataclass(frozen=True)
class CatalogAlignment:
    """Whether the payload can be trusted to address the rendered rows.

    `ok` is the only field a caller may branch on. `reason` and the two counts
    exist so a refusal can say **which** of the three states it was in, since
    "no id was found" and "we never had ids" call for opposite next moves and
    used to be the same observation.
    """

    songs: tuple[CatalogSong, ...] = ()
    ok: bool = False
    reason: str = ALIGN_NOT_CAPTURED
    #: How many rendered rows were checked against the payload.
    checked: int = 0
    #: The first row index whose title disagreed, or `None`. Carried as an
    #: INDEX, never as the two titles: a title is user-adjacent content and
    #: this reaches a stored, logged, publicly-reviewable string.
    mismatch_at: int | None = None


def align_catalog(
    rows: Sequence[Any], songs: Sequence[CatalogSong], *, canonical: Any
) -> CatalogAlignment:
    """Does payload entry *i* really describe rendered row *i*. Pure.

    `canonical` is `douyin_publish.canonical_music` passed in rather than
    imported, so this module stays free of a circular import and — more to the
    point — so the comparison uses the SAME normaliser the fingerprint judge
    uses. Two normalisers that disagree by a space would make an alignment fail
    for a reason no reader could see.

    Every row is checked, not a sample. A sample would pass on the exact shape
    this guard exists to catch: one inserted entry near the end shifts only the
    tail, and a head-only check would bless it.
    """
    if not songs:
        return CatalogAlignment(reason=ALIGN_NOT_CAPTURED)
    ordered = tuple(songs)
    if not rows:
        # The payload arrived and the page rendered nothing we could read. That
        # is a real state and it is NOT an alignment — there is nothing to
        # address — but it is also not "no payload", so it gets its own name.
        return CatalogAlignment(songs=ordered, reason=ALIGN_NO_ROWS)

    checked = 0
    for row in rows:
        index = int(getattr(row, "index", -1))
        name = str(getattr(row, "name", "") or "")
        if index < 0 or not name.strip():
            # A row the parser could not name cannot testify either way. Skip
            # it rather than fail on it: it is not evidence of a shift, and
            # treating it as one would withdraw the id path over a defect that
            # the fingerprint path already reports through `dims=`.
            continue
        if index >= len(ordered):
            return CatalogAlignment(
                songs=ordered,
                reason=ALIGN_SHORT,
                checked=checked,
                mismatch_at=index,
            )
        if canonical(ordered[index].name) != canonical(name):
            return CatalogAlignment(
                songs=ordered,
                reason=ALIGN_TITLE_MISMATCH,
                checked=checked,
                mismatch_at=index,
            )
        checked += 1

    if checked == 0:
        # Nothing testified. Every row was unnamed, so the order is unproven
        # and this must not read as `ok` — an alignment nobody checked is the
        # assumption this function exists to replace.
        return CatalogAlignment(songs=ordered, reason=ALIGN_NO_ROWS)
    return CatalogAlignment(songs=ordered, ok=True, reason=ALIGN_OK, checked=checked)


def find_song_index(alignment: CatalogAlignment, music_id: str) -> int | None:
    """Which rendered row IS the track with this id. Pure. **Never guesses.**

    `None` on every uncertain outcome, and the caller then runs the existing
    fingerprint judge unchanged. The uncertain outcomes:

    * the alignment did not hold — the index would address the wrong row;
    * no entry carries the id — the picked track is not in these results, which
      the fingerprint judge will report in its own vocabulary;
    * **more than one** entry carries it. That should be impossible and is
      checked anyway: an id that appears twice means the assumption "an id
      identifies a track" is wrong on this platform, and the correct response
      to a broken assumption is to stop using it, not to take the first hit.
    """
    if not alignment.ok:
        return None
    key = (music_id or "").strip()
    if not key:
        return None
    hits = [song.index for song in alignment.songs if song.music_id == key]
    if len(hits) != 1:
        return None
    return hits[0]


def describe_catalog(alignment: CatalogAlignment) -> str:
    """One clause for the refusal message. Pure.

    This is the clause that retires the `attrs=` census: that census asked
    "is there an id on the row to click by?", and the answer measured on
    2026-08-19 is "not on the row — in the response". So what a refusal needs
    to report is no longer *whether an id exists* but whether we managed to
    attach it to the rows this time.

    Renderings, each of which a reader can act on differently:

    * ``ids=20/20`` — the payload was captured and proven to address the rows.
      An id-based refusal after this is about the track, not the plumbing.
    * ``ids=none`` — no search response was captured. Our listener, not the
      platform: check the URL fragment before suspecting the catalogue.
    * ``ids=misaligned@3`` — captured, but row 3's title did not match the
      payload's third entry. The rendering order is not the payload order and
      the pairing assumption is dead until re-measured.
    * ``ids=short@21`` — captured, but the page rendered a row past the end of
      the payload; a second response probably replaced the first.
    * ``ids=norows`` — captured, but nothing readable was rendered. The row
      parser is the thing to look at, not this module.
    """
    if alignment.reason == ALIGN_NOT_CAPTURED:
        return "ids=none"
    if alignment.reason == ALIGN_NO_ROWS:
        return "ids=norows"
    if alignment.reason == ALIGN_SHORT:
        return f"ids=short@{alignment.mismatch_at}"
    if alignment.reason == ALIGN_TITLE_MISMATCH:
        return f"ids=misaligned@{alignment.mismatch_at}"
    return f"ids={alignment.checked}/{len(alignment.songs)}"


class MusicCatalogRecorder:
    """Collects the dialog's search responses off the page. Impure, bounded.

    Modelled on `probe._Recorder` rather than invented: responses arrive on a
    sync callback, their bodies are async, and reading them inline would block
    the event loop the page is running on. So each body read is scheduled and
    `settle()` waits for the in-flight ones before the caller reads.

    Every failure path here returns nothing and logs at DEBUG. That is
    deliberate and it is NOT the "silent swallow" this codebase refuses
    elsewhere: a body we could not read produces `ids=none`, which the refusal
    message states outright, so the absence is *reported* — just not as an
    exception that would fail a publish over a diagnostic.
    """

    def __init__(self, *, fragment: str = MUSIC_SEARCH_URL_FRAGMENT) -> None:
        self._fragment = fragment
        self._payloads: list[Any] = []
        self._tasks: set[asyncio.Task[Any]] = set()
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
        except Exception as exc:  # noqa: BLE001 - detaching must never fail a publish
            logger.debug("music catalog listener detach failed: %s", type(exc).__name__)

    def _schedule(self, response: Any) -> None:
        try:
            url = str(getattr(response, "url", "") or "")
        except Exception:  # noqa: BLE001
            return
        if self._fragment not in url:
            return
        task = asyncio.ensure_future(self._absorb(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _absorb(self, response: Any) -> None:
        try:
            if int(getattr(response, "status", 0)) != 200:
                return
            body = await response.body()
            if body is None or len(body) > MAX_PAYLOAD_BYTES:
                return
            payload = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            logger.debug("music catalog body unreadable: %s", type(exc).__name__)
            return
        self._payloads.append(payload)
        # Keep the LAST N: the rows on screen came from the most recent
        # response, so an overflow must drop the oldest, not refuse the newest.
        if len(self._payloads) > MAX_CAPTURED_PAYLOADS:
            del self._payloads[0 : len(self._payloads) - MAX_CAPTURED_PAYLOADS]

    async def settle(self) -> None:
        """Let in-flight body reads finish before the caller reads `songs()`."""
        if not self._tasks:
            return
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def songs(self) -> list[CatalogSong]:
        """The most recent response that parsed into tracks.

        Latest-first rather than merged: two responses are two different
        result lists, and concatenating them would build an index space that
        matches no rendering at all — the precise error `align_catalog` is
        here to catch, introduced by the very function meant to feed it.
        """
        for payload in reversed(self._payloads):
            songs = read_search_catalog(payload)
            if songs:
                return songs
        return []


def merge_alignment_ids(songs: Iterable[CatalogSong]) -> dict[int, str]:
    """`{row index: music id}`. Pure; convenience for callers that only need
    the mapping (a caller that wants the *reason* must read the alignment)."""
    return {song.index: song.music_id for song in songs}


__all__ = [
    "ALIGN_NOT_CAPTURED",
    "ALIGN_NO_ROWS",
    "ALIGN_OK",
    "ALIGN_SHORT",
    "ALIGN_TITLE_MISMATCH",
    "CatalogAlignment",
    "CatalogSong",
    "MAX_CAPTURED_PAYLOADS",
    "MUSIC_SEARCH_URL_FRAGMENT",
    "MusicCatalogRecorder",
    "align_catalog",
    "describe_catalog",
    "find_song_index",
    "merge_alignment_ids",
    "read_search_catalog",
]
