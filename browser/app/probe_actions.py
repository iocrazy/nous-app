"""The one place on the recon path that may activate a control — and it may
only activate things named in a closed list written in this file.

Why this module exists
======================
`probe.py` answers "what does the page fetch while someone types". A whole
class of platform data is one step further away: the Douyin 「选择音乐」 panel
does not exist until its entry point is pressed, and its 推荐 / 热门榜 / 收藏 /
飙升榜 / 原创榜 tabs each fetch their own list only when the tab is taken. No
amount of typing reaches any of it.

The guarantee that was there before, and what replaced it
=========================================================
Until now the safety property was stated as a grep: `probe.py` contains no
activation vocabulary at all, therefore recon *mechanically* cannot hand
anything to a platform. That is a strong property and it is not being weakened
into a promise. It is being replaced by three mechanical properties that
together are at least as strong:

1. **One activation call on the whole recon surface.** `probe.py` and
   `inspect.py` still contain no activation call of any kind — activation is
   delegated here, exactly the way file seeding is delegated to
   `inspect.seed_file_input`. `tests/test_probe_actions_units.py` reads all
   three files and fails if `.click(` / `.press(` appears anywhere but in the
   two functions below, once each.

2. **A closed vocabulary, verified twice.** `PROBE_LABELS` is a literal tuple
   in this file. `label_refusal` rejects anything outside it *before the page
   is touched at all*, and `ProbeActivationStep` rejects it again at the
   schema layer, so a caller cannot even express "activate 发布" — the request
   is a 422 before a browser process exists. The test asserts the list carries
   no submission vocabulary and that the refusal path performs zero page
   operations.

3. **The node has to still say so.** A locator is not a promise about what it
   resolved to. Before anything is activated, the candidate's own rendered
   text is read back and must equal the allow-listed label exactly (whitespace
   collapsed). A node captioned 发布 therefore cannot be activated through
   this function even if a selector somehow resolved to it, because 发布 is
   not in the list and the equality check would fail anyway.

Two further bounds, because a closed list is not the only risk:

* **Exact text matching only.** Callers never pass a CSS selector to activate.
  They pass a label, and `get_by_text(..., exact=True)` resolves it — `exact`
  is load-bearing on this platform (「允许」 is a substring of 「不允许」).
* **Keys are a closed list too, and only on text fields.** Some dialogs only
  search on Enter. `press_confirmed_key` accepts one key and refuses to send
  it to anything that is not an `input` / `textarea`, verified by reading the
  live node's `tagName`. Enter on a text field can at worst submit that
  field's form; Enter on a focused button is a second way to press it, and
  that is the door this check closes.

What is deliberately *not* in the list
======================================
Nothing that commits: no 确定 / 完成 / 使用 / 提交, and nothing that publishes.
Recon reads what the panel lists; applying a choice is the publish driver's
job and has its own audited path. Adding a label here is a source change that
shows up in review, which is the point.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger("nous_browser.probe_actions")

# The closed vocabulary. Every entry was measured on the live image-post editor
# (see `platforms/douyin_publish.py`'s music section): 「选择音乐」 opens the
# panel, the rest are its tab captions.
#
# ⚠️ Anything added here becomes activatable on a real, logged-in creator
# account. The rule is: only controls that *reveal* a list, never controls that
# apply, confirm, submit or publish anything.
PROBE_LABELS: tuple[str, ...] = (
    "选择音乐",
    "推荐",
    "热门榜",
    "收藏",
    "飙升榜",
    "原创榜",
    "卡点",
)

# Upper bound on the list itself. A guard that only checks the *contents* of a
# list can be defeated by a list that grew to cover half the page; this makes
# "the vocabulary stayed small" a test failure rather than a code review habit.
MAX_PROBE_LABELS = 12

# The only key this module will send, and the only elements it will send it to.
PROBE_KEYS: tuple[str, ...] = ("Enter",)
PRESSABLE_TAGS: tuple[str, ...] = ("INPUT", "TEXTAREA")

# How many identically-captioned nodes are tried for one label. 「选择音乐」 was
# measured at exact=2 on the live page (the block heading and the button), and
# the heading is inert — so "take the first match" is a coin toss. The ceiling
# keeps that from turning into "try everything with this caption".
MAX_LABEL_CANDIDATES = 6

_WHITESPACE = re.compile(r"\s+")


def normalise_label(text: str) -> str:
    """Collapse whitespace so a rendered caption can be compared to a literal.

    Pure. Rendered text arrives with newlines and padding that are styling, not
    identity; nothing else is normalised, because case-folding or punctuation
    stripping would make two different captions compare equal, which is the one
    failure mode this comparison exists to prevent.
    """
    return _WHITESPACE.sub("", str(text or ""))


def label_refusal(label: str) -> str | None:
    """`None` if this label may be activated, else why not. Pure.

    Called before any page operation, and again by the schema. Two checks of
    the same rule is deliberate: the schema one gives the caller a 422 with no
    browser started, this one holds even if someone constructs the request
    object in code.
    """
    if not label:
        return "an empty label cannot be activated"
    if label not in PROBE_LABELS:
        return (
            f"label {label!r} is not in the recon allow-list; "
            f"allowed: {', '.join(PROBE_LABELS)}"
        )
    return None


@dataclass
class Activation:
    """What happened to one label. Total — every path returns one of these."""

    label: str
    matches: int = 0
    index: int = -1
    activated: bool = False
    #: Empty string = nothing went wrong. Non-empty is reported to the caller
    #: verbatim, so a run that could not open a panel says *which* step failed
    #: rather than looking like a platform that fetches nothing.
    error: str = ""
    #: Which candidates were skipped because their rendered text did not equal
    #: the label. A non-empty list plus `activated=False` is the signature of
    #: "the caption moved", not "the control is gone".
    text_mismatches: list[str] = field(default_factory=list)


async def _visible(page: Any, selector: str) -> bool:
    try:
        return bool(await page.locator(selector).first.is_visible())
    except Exception:  # noqa: BLE001 - a bad selector is a caller mistake, not a crash
        return False


async def until_satisfied(page: Any, selectors: Sequence[str]) -> bool:
    """Did the thing the caller expected to appear, appear.

    An empty list means "no predicate", and then a successful activation is
    taken at face value. Read-only.
    """
    if not selectors:
        return True
    for selector in selectors:
        if await _visible(page, selector):
            return True
    return False


async def activate_label(
    page: Any,
    label: str,
    *,
    until_selectors: Sequence[str] = (),
    candidates: int = 4,
    timeout_ms: int = 8_000,
    settle_ms: int = 3_000,
) -> Activation:
    """Activate the node whose rendered text is exactly `label`. Total.

    The order of the checks is the safety property, so it is spelled out:

    1. the label must be in `PROBE_LABELS` — otherwise **the page is never
       touched**, not even to count matches;
    2. candidates are resolved by exact text, never by a caller-supplied
       selector;
    3. each candidate's live text is read back and must equal the label;
    4. only then is it activated, and only through the plain, actionability-
       checked call — no `force`, no DOM-level `el.click()`. Both of those
       exist in the publish driver for controls the platform styles as
       non-interactive, and both bypass exactly the guarantee that makes a
       recon activation safe: that the thing was really a visible, enabled
       control at the position we thought it was.
    """
    refusal = label_refusal(label)
    if refusal is not None:
        # Nothing above this line touched `page`, and nothing below runs.
        return Activation(label=label, error=refusal)

    try:
        locator = page.get_by_text(label, exact=True)
        matches = int(await locator.count())
    except Exception as exc:  # noqa: BLE001
        return Activation(label=label, error=f"could not resolve: {type(exc).__name__}")

    outcome = Activation(label=label, matches=matches)
    if matches <= 0:
        outcome.error = f"no node on this page renders exactly {label!r}"
        return outcome

    ceiling = max(1, min(int(candidates), MAX_LABEL_CANDIDATES))
    for index in range(min(matches, ceiling)):
        node = locator.nth(index)
        try:
            rendered = await node.inner_text(timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001 - unreadable candidate, try next
            outcome.text_mismatches.append(f"#{index}:<{type(exc).__name__}>")
            continue
        if normalise_label(rendered) != normalise_label(label):
            # The guarantee, restated at runtime: whatever this locator
            # resolved to, it is not the control we are allowed to press.
            outcome.text_mismatches.append(f"#{index}:{normalise_label(rendered)[:40]}")
            continue
        try:
            await node.click(timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001 - inert heading, covered node…
            outcome.text_mismatches.append(f"#{index}:<{type(exc).__name__}>")
            continue
        try:
            await page.wait_for_timeout(settle_ms)
        except Exception:  # noqa: BLE001
            pass
        if await until_satisfied(page, until_selectors):
            outcome.index = index
            outcome.activated = True
            return outcome

    outcome.error = (
        f"none of the {min(matches, ceiling)} nodes captioned {label!r} "
        "produced the expected result"
    )
    return outcome


async def press_confirmed_key(
    locator: Any, key: str, *, timeout_ms: int = 8_000
) -> str:
    """Send one allow-listed key to a node that is verifiably a text field.

    Returns `""` on success, else why not. Two closed sets, both checked here:
    the key, and the tag it may be sent to. The tag is read off the *live*
    node rather than inferred from the selector that produced it.
    """
    if key not in PROBE_KEYS:
        return f"key {key!r} is not in the recon allow-list"
    try:
        tag = str(await locator.evaluate("el => el.tagName") or "").upper()
    except Exception as exc:  # noqa: BLE001
        return f"could not read the target's tag: {type(exc).__name__}"
    if tag not in PRESSABLE_TAGS:
        return (
            f"refusing to send {key!r} to a <{tag.lower() or '?'}>; "
            f"allowed: {', '.join(t.lower() for t in PRESSABLE_TAGS)}"
        )
    try:
        await locator.press(key, timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        return f"key press failed: {type(exc).__name__}"
    return ""


__all__ = [
    "MAX_LABEL_CANDIDATES",
    "MAX_PROBE_LABELS",
    "PRESSABLE_TAGS",
    "PROBE_KEYS",
    "PROBE_LABELS",
    "Activation",
    "activate_label",
    "label_refusal",
    "normalise_label",
    "press_confirmed_key",
    "until_satisfied",
]
