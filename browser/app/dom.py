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

from typing import Any, Sequence


async def visible_marker_texts(page: Any, markers: Sequence[str]) -> list[str]:
    """Which of `markers` are *visible* on the page right now.

    `count()` alone is not enough: platforms keep hidden login nodes in the DOM
    of the authenticated app shell, so a count-based check reports a perfectly
    good session as logged out.
    """
    found: list[str] = []
    for marker in markers:
        # Substring, not exact: platforms decorate button labels ("手机号登录 >",
        # trailing icons/whitespace) and an exact matcher silently stops finding
        # the marker after a copy tweak - failing open, which is the worse
        # direction here. Visibility is what does the real discriminating.
        locator = page.get_by_text(marker, exact=False).first
        try:
            if await locator.count() and await locator.is_visible():
                found.append(marker)
        except Exception:
            # A locator that races with a re-render is not evidence of anything.
            continue
    return found


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
    """
    try:
        await locator.click(timeout=timeout_ms)
        return True
    except Exception:
        pass

    try:
        await locator.click(timeout=timeout_ms, force=True)
        return True
    except Exception:
        pass

    try:
        await locator.evaluate("el => el.click()")
        return True
    except Exception:
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
