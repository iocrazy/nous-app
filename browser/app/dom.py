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
