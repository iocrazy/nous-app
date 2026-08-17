"""Small, defensive readers for a live page.

Both the session validator and the login flow have to answer "what is actually
on screen right now", and they must answer it the same way - a second, subtly
different visibility check is how one code path ends up condemning sessions the
other considers healthy (spec 7.1 applied to the helpers, not just the top-level
function).

Everything here fails soft: a locator that races a re-render returns "not
found", never an exception. These readers are called while judging page state,
where raising would turn a transient repaint into a typed failure.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Sequence


async def visible_marker_texts(
    page: Any, markers: Sequence[str], *, exact: bool = False
) -> list[str]:
    """Which of `markers` are *visible* on the page right now.

    `count()` alone is not enough: platforms keep hidden login nodes in the DOM
    of the authenticated app shell, so a count-based check reports a perfectly
    good session as logged out.

    `exact` picks which way a miss fails, and the right answer depends entirely
    on what the caller does with the result — which is why it is a parameter
    and not a house style:

      * Default (substring) suits the LOGIN markers. Failing to spot "logged
        out" leaves us believing a dead session is alive, so these must match
        generously through decoration ("手机号登录 >").
      * `exact=True` suits markers whose presence *licenses a conclusion about
        the user's content* — an empty-state caption, a status word. There the
        generous direction is the dangerous one, because 「允许」 is a substring
        of 「不允许」 and an unrelated caption can then vote on a verdict. A
        miss under `exact=True` degrades to "we did not learn anything", which
        is recoverable; a false positive is not.
    """
    found: list[str] = []
    for marker in markers:
        locator = page.get_by_text(marker, exact=exact).first
        try:
            if await locator.count() and await locator.is_visible():
                found.append(marker)
        except Exception:
            # A locator that races with a re-render is not evidence of anything.
            continue
    return found


async def describe_controls(
    page: Any,
    selector: str,
    *,
    attributes: Sequence[str] = (),
    limit: int = 8,
    text_len: int = 60,
) -> tuple[int, list[dict[str, Any]]]:
    """(how many exist, a capped description of the first few). Never raises.

    For diagnostics, not for driving: when a click "landed" and the page did
    not move, the first question is *what was on that page at all*, and a list
    of the inputs and buttons answers it in a way a screenshot cannot be
    (screenshots do not survive a JSON column).

    Invisible nodes are listed too, with `visible: False`. That is not noise —
    "the code field exists but is hidden" and "there is no code field" are
    different findings with different fixes, and dropping the invisible ones
    would make them look identical.
    """
    items: list[dict[str, Any]] = []
    try:
        locator = page.locator(selector)
        total = int(await locator.count())
    except Exception:
        return 0, items

    for index in range(min(total, limit)):
        item: dict[str, Any] = {"selector": selector, "index": index}
        try:
            node = locator.nth(index)
        except Exception:
            continue
        try:
            item["visible"] = bool(await node.is_visible())
        except Exception:
            # None, not False: "we could not tell" must not read as "hidden".
            item["visible"] = None
        try:
            text = (await node.inner_text() or "").strip()
        except Exception:
            text = ""
        if text:
            item["text"] = text[:text_len]
        for attribute in attributes:
            try:
                value = await node.get_attribute(attribute)
            except Exception:
                value = None
            if value:
                item[attribute] = value[:text_len]
        items.append(item)
    return total, items


async def scroll_into_view(locator: Any, timeout_ms: int) -> bool:
    """Best-effort `scrollIntoViewIfNeeded`. False = could not, or not needed.

    Playwright's own click already scrolls, so this is not a duplicate of it:
    it runs *before* the click so that the actionability facts we record
    alongside a failed click (in-viewport, covered-by) describe the element as
    the click would have found it, rather than one that was simply off screen.
    """
    scroll = getattr(locator, "scroll_into_view_if_needed", None)
    if scroll is None:
        return False
    try:
        await scroll(timeout=timeout_ms)
        return True
    except Exception:
        return False


async def is_selector_visible(page: Any, selector: str) -> bool:
    if not selector:
        return False
    try:
        locator = page.locator(selector).first
        return bool(await locator.count()) and await locator.is_visible()
    except Exception:
        return False


async def first_visible_attribute(
    page: Any, selectors: Sequence[str], attribute: str
) -> str | None:
    """Value of `attribute` on the first selector that is present and non-empty.

    Selector *lists* rather than one selector are deliberate. Douyin's creator
    centre has already moved its QR node twice (the `aria-label="二维码"` hook
    the reference implementation relies on is gone from the current layout), and
    a single selector turns any redesign into a hard outage. Order is priority:
    most specific first, historical hooks last.
    """
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if not await locator.count():
                continue
            value = await locator.get_attribute(attribute)
        except Exception:
            continue
        if value:
            return value
    return None


_REMOVE_NODES_JS = """
(selectors) => {
  let removed = 0;
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      node.remove();
      removed += 1;
    }
  }
  return removed;
}
"""


async def remove_nodes(page: Any, selectors: Sequence[str]) -> int:
    """Delete matching nodes outright. Returns how many went.

    For overlays that *intercept clicks* rather than merely sit on the page -
    an onboarding coach-mark, a topic autocomplete dropdown. Playwright's
    actionability check reports these as "element intercepts pointer events"
    after burning the full click timeout, and no amount of waiting clears them
    because they are waiting on the user (design doc 7.4).

    Removing beats clicking through with `force`: a forced click still lands on
    whatever is underneath the overlay, which on a publish page is another
    control.
    """
    if not selectors:
        return 0
    try:
        return int(await page.evaluate(_REMOVE_NODES_JS, list(selectors)))
    except Exception:
        # The overlay may simply not be there. Never a reason to fail a publish.
        return 0


@dataclass
class ClickTally:
    """How many clicks each escalation tier won, over one operation.

    **Why counting is worth code at all.** `click_element` degrades silently,
    so a browser whose *first* tier never works goes on publishing perfectly —
    tier 2 or 3 picks every click up, each one costing a full click timeout
    first. Nothing anywhere records that this happened. The consequence is a
    sentence worth stating plainly: **"it published, so the browser is fine" has
    never been a valid inference**, and a stack that had degraded to JS clicks
    months ago would look exactly like a healthy one, only slower.

    Counts, never selectors and never page text: this rides into logs.
    """

    direct: int = 0
    force: int = 0
    js: int = 0
    failed: int = 0

    def record(self, tier: str) -> None:
        setattr(self, tier, getattr(self, tier) + 1)

    @property
    def total(self) -> int:
        return self.direct + self.force + self.js + self.failed

    @property
    def degraded(self) -> bool:
        """Did anything win below tier 1. The one bit worth alerting on."""
        return bool(self.force or self.js)

    def render(self) -> str:
        return (
            f"direct={self.direct} force={self.force} "
            f"js={self.js} fail={self.failed}"
        )


# Ambient rather than threaded through ~20 call sites, and a ContextVar rather
# than a module global **because concurrency is real here**: the service
# publishes several accounts at once, each in its own asyncio task, and a
# module-level counter would blend them into one meaningless number. A task
# copies the context when it is created, so each publish gets its own tally and
# no publish can see another's.
_click_tally: ContextVar[ClickTally | None] = ContextVar("nous_click_tally", default=None)


@contextmanager
def collecting_clicks() -> Iterator[ClickTally]:
    """Count click tiers for the duration of this block. Never affects clicking.

    Outside such a block the counter is `None` and `click_element` does not
    record — login and probing keep behaving exactly as before, with no
    accumulator quietly growing for the life of the process.
    """
    tally = ClickTally()
    token = _click_tally.set(tally)
    try:
        yield tally
    finally:
        _click_tally.reset(token)


def _record_click(tier: str) -> None:
    tally = _click_tally.get()
    if tally is not None:
        tally.record(tier)


async def click_element(locator: Any, timeout_ms: int) -> bool:
    """Click through three escalating strategies. False = none of them worked.

    The escalation is not superstition; each step answers a specific observed
    behaviour:

    1. a plain click, which also waits for actionability;
    2. `force`, for controls the platform styles as non-interactive while still
       wiring a handler to them (Semi's `.semi-radio-addon` carries
       `pointer-events: none` and a plain click sits there until it times out);
    3. a DOM `click()` via JS, for controls that are `visibility: hidden` and
       therefore never actionable at all - Playwright refuses, the browser does
       not (the reference project's "use this BGM" button is the known case).

    Strategy 3 skips every actionability guarantee, which is why it is last:
    it will happily click something that is covered, disabled or off-screen.

    **Which tier won is recorded** (`ClickTally`). The escalation itself is
    unchanged and deliberately so — degrading is the right behaviour, doing it
    without a trace is not. A caller inside `collecting_clicks()` ends up able
    to say "every click on this publish needed `force`", which is a browser
    fault that a successful publish had been hiding.
    """
    try:
        await locator.click(timeout=timeout_ms)
        _record_click("direct")
        return True
    except Exception:
        pass

    try:
        await locator.click(timeout=timeout_ms, force=True)
        _record_click("force")
        return True
    except Exception:
        pass

    try:
        await locator.evaluate("el => el.click()")
        _record_click("js")
        return True
    except Exception:
        _record_click("failed")
        return False


async def click_first(
    root: Any, selectors: Sequence[str], *, timeout_ms: int
) -> str | None:
    """Click the first selector that resolves to something. Returns which one.

    `root` is a page or a locator, so a modal can be searched without leaking
    into identically-classed nodes on the page behind it.
    """
    for selector in selectors:
        try:
            locator = root.locator(selector).first
            if not await locator.count():
                continue
        except Exception:
            continue
        if await click_element(locator, timeout_ms):
            return selector
    return None


async def first_text(page: Any, selectors: Sequence[str]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if not await locator.count():
                continue
            value = await locator.inner_text()
        except Exception:
            continue
        if value and value.strip():
            return value.strip()
    return None
