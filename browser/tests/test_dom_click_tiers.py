"""`dom.click_element` degrades through three tiers. Which one won is recorded.

The escalation itself is right and is **not** changed here: some of this
platform's controls carry `pointer-events: none` and some are
`visibility: hidden`, and a click that refuses to try harder simply fails on
them. What was wrong is that every tier swallowed its own failure without a
trace, which makes one sentence true and nobody had noticed it:

    **"the publish succeeded, so the browser is healthy" has never followed.**

A Chromium whose ordinary clicks all fail keeps publishing — tier 2 or 3 picks
up every click, each after burning a full click timeout first — and looks
identical to a healthy one apart from being slow. That state could have been
live for months without producing a single signal.

What is asserted below: the tally is accurate, the *behaviour* (return value
and the order things are attempted) is untouched, and two concurrent publishes
never see each other's counts.
"""

from __future__ import annotations

import asyncio

import pytest

from app import dom

pytestmark = pytest.mark.unit


class ScriptedLocator:
    """A locator whose tiers fail on demand, recording what was attempted.

    `attempts` is the falsifiable half: an "improvement" that stopped trying
    `force` before JS would still produce a plausible tally, and only the
    attempt log catches it.
    """

    def __init__(self, *, plain: bool = True, force: bool = True, js: bool = True):
        self._plain = plain
        self._force = force
        self._js = js
        self.attempts: list[str] = []

    async def click(self, timeout=None, force: bool = False) -> None:
        self.attempts.append("force" if force else "direct")
        if force and not self._force:
            raise TimeoutError("forced click timed out")
        if not force and not self._plain:
            raise TimeoutError("element intercepts pointer events")

    async def evaluate(self, _expression: str) -> None:
        self.attempts.append("js")
        if not self._js:
            raise RuntimeError("no such element")


async def test_an_ordinary_click_is_recorded_as_the_first_tier():
    locator = ScriptedLocator()
    with dom.collecting_clicks() as tally:
        assert await dom.click_element(locator, 10) is True

    assert (tally.direct, tally.force, tally.js, tally.failed) == (1, 0, 0, 0)
    assert tally.degraded is False
    # It stopped at the first tier — no wasted timeout.
    assert locator.attempts == ["direct"]


async def test_a_click_that_only_lands_with_force_says_so():
    """The state that publishes fine and is not fine. Before this, the only
    difference between here and the test above was seconds on a clock nobody
    was reading."""
    locator = ScriptedLocator(plain=False)
    with dom.collecting_clicks() as tally:
        assert await dom.click_element(locator, 10) is True

    assert (tally.direct, tally.force, tally.js) == (0, 1, 0)
    assert tally.degraded is True
    assert locator.attempts == ["direct", "force"]


async def test_a_click_that_needs_javascript_is_the_third_tier():
    locator = ScriptedLocator(plain=False, force=False)
    with dom.collecting_clicks() as tally:
        assert await dom.click_element(locator, 10) is True

    assert (tally.force, tally.js) == (0, 1)
    assert tally.degraded is True
    assert locator.attempts == ["direct", "force", "js"]


async def test_a_click_nothing_could_land_is_counted_but_is_not_degradation():
    """`failed` is its own column, and it does NOT set `degraded`.

    Degradation is "a lower tier won" — a fault that success hides. A click
    that landed nowhere is already visible to its caller as `False`, and every
    call site turns that into a typed failure. Folding the two together would
    make the alert fire on the case that was never silent.
    """
    locator = ScriptedLocator(plain=False, force=False, js=False)
    with dom.collecting_clicks() as tally:
        assert await dom.click_element(locator, 10) is False

    assert (tally.failed, tally.total) == (1, 1)
    assert tally.degraded is False


async def test_counting_changes_nothing_about_how_a_click_behaves():
    """The escalation is the right behaviour; only its silence was wrong. Same
    return values and the same attempts with the counter and without it."""
    cases = [
        {},
        {"plain": False},
        {"plain": False, "force": False},
        {"plain": False, "force": False, "js": False},
    ]
    for kwargs in cases:
        counted = ScriptedLocator(**kwargs)
        uncounted = ScriptedLocator(**kwargs)
        with dom.collecting_clicks():
            counted_result = await dom.click_element(counted, 10)
        # No enclosing block at all: this is how login and probing call it.
        uncounted_result = await dom.click_element(uncounted, 10)

        assert counted_result == uncounted_result
        assert counted.attempts == uncounted.attempts


async def test_nothing_accumulates_outside_a_collecting_block():
    """No process-lifetime counter. Outside a block there is nothing to write
    to, which is also what keeps login clicks out of a publish's numbers."""
    assert dom._click_tally.get() is None
    await dom.click_element(ScriptedLocator(), 10)
    assert dom._click_tally.get() is None


async def test_two_publishes_at_once_never_share_a_tally():
    """**Why a ContextVar and not a module global.** This service publishes
    several accounts concurrently, each in its own task. One shared counter
    would blend them, and the resulting number — "some clicks somewhere needed
    force" — cannot be acted on by anybody.

    Swap the ContextVar for a module-level `ClickTally()` and this goes red.
    """

    async def one_publish(*, plain: bool, clicks: int) -> dom.ClickTally:
        with dom.collecting_clicks() as tally:
            for _ in range(clicks):
                await dom.click_element(ScriptedLocator(plain=plain), 10)
                # Yield, so the two tasks really do interleave rather than
                # running to completion one after the other.
                await asyncio.sleep(0)
            return tally

    healthy, degraded = await asyncio.gather(
        one_publish(plain=True, clicks=3),
        one_publish(plain=False, clicks=2),
    )

    assert (healthy.direct, healthy.force) == (3, 0)
    assert healthy.degraded is False
    assert (degraded.direct, degraded.force) == (0, 2)
    assert degraded.degraded is True


def test_the_rendering_is_counts_and_nothing_else():
    """It lands in logs and in a publish's `detail`, and this repository is
    public: no selector, no page text, no URL can ever reach it."""
    rendered = dom.ClickTally(direct=12, force=1, js=0, failed=2).render()
    assert rendered == "direct=12 force=1 js=0 fail=2"
