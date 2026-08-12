"""A tiny stand-in for a Playwright `Page`, backed by real HTML.

Why this exists rather than a mock
==================================
`read_work_cards` is the half of the read-back that touches a DOM, and mocking
it out would leave the interesting parts untested: which selector wins, whether
nested wrappers get de-duplicated, whether a card's item id is actually pulled
off its anchor. Those are the things that break.

Driving a real Chromium would be better still, but the browsers are a separate
install (`patchright install chromium`) that CI for this package does not do —
`test_integration_browser.py` is skipped wherever they are missing, so a
browser-only test would produce no evidence on most machines. This shim runs
everywhere.

It implements exactly the surface `douyin_verify` uses — `locator(css)`,
`count()`, `nth(i)`, `inner_text()`, `get_attribute()`, plus `get_by_text()`
for the empty-state check — over the small selector grammar that module
actually writes.

Since 2026-08-12 it also carries `click()` / `scroll_into_view_if_needed()`,
for the login flow's page-evidence capture. The document is **immutable**: a
click is recorded and changes nothing, which is not a shortcut but the exact
shape of the failure being tested (the click landed, the platform stayed put).
Selector grammar:

    tag                              a
    [class*="x"] / [class^="x"]      attribute contains / starts-with
    [href*="x"]                      same, on any attribute
    div:has(> a[href*="/video/"])    element with a matching DIRECT child
    [class*="x"]:not([class*="x"] *) match with no matching ANCESTOR

Anything outside that grammar raises, loudly. A shim that silently returned
"no matches" for a selector it did not understand would turn a broken test into
a passing one — the exact failure mode these tests exist to catch.

The last form is the one `douyin_verify.outermost_only` builds, and it is the
difference between reading works and reading the nodes a work is made of. It
is supported here because it is supported by the real engine: the read-only
inspect endpoint counts with `page.locator`, and against the live console it
returned 12 for `[class*="video-card"]:not([class*="video-card"] *)` where the
unscoped form returned 72 ([实测 2026-08-11]).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

_VOID = {"img", "br", "hr", "input", "meta", "link", "source"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text: str = ""
    parent: "Node | None" = None

    def inner_text_value(self) -> str:
        """Approximates `innerText`: descendant text, block-separated.

        Newline-joined rather than space-joined because that is what Playwright
        returns for block elements, and the marker matching downstream is a
        plain `in` test over the result — joining differently would change
        which markers can span a boundary.
        """
        parts: list[str] = []
        if self.text.strip():
            parts.append(self.text.strip())
        for child in self.children:
            value = child.inner_text_value()
            if value:
                parts.append(value)
        return "\n".join(parts)

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node(tag="#root")
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(
            tag=tag,
            attrs={k: (v or "") for k, v in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(
            tag=tag,
            attrs={k: (v or "") for k, v in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        if data.strip():
            holder = Node(tag="#text", text=data, parent=self._stack[-1])
            self._stack[-1].children.append(holder)


def parse_html(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    return builder.root


_SIMPLE = re.compile(
    r"^(?P<tag>[a-zA-Z][\w-]*)?"
    r"(?P<attrs>(?:\[[^\]]+\])*)$"
)
_ATTR = re.compile(r"\[(?P<name>[\w-]+)(?P<op>[*^$]?)=\"(?P<value>[^\"]*)\"\]")
_HAS_CHILD = re.compile(r"^(?P<base>[^:]*):has\(>\s*(?P<child>.+)\)$")
# `X:not(Y *)` — matches X, provided no ANCESTOR matches Y. Deliberately only
# the descendant-combinator form: that is the one `outermost_only` emits, and a
# general `:not()` implementation here would be shim behaviour nobody has
# checked against the real engine.
_NOT_DESCENDANT_OF = re.compile(r"^(?P<base>.+?):not\((?P<ancestor>.+?)\s+\*\)$")


class UnsupportedSelector(ValueError):
    """Raised rather than returning nothing — see the module docstring."""


def _match_attrs(node: Node, spec: str) -> bool:
    for match in _ATTR.finditer(spec):
        name, op, value = match.group("name"), match.group("op"), match.group("value")
        actual = node.attrs.get(name)
        if actual is None:
            return False
        if op == "*" and value not in actual:
            return False
        if op == "^" and not actual.startswith(value):
            return False
        if op == "$" and not actual.endswith(value):
            return False
        if op == "" and actual != value:
            return False
    return True


def _matches(node: Node, selector: str) -> bool:
    selector = selector.strip()
    outermost = _NOT_DESCENDANT_OF.match(selector)
    if outermost:
        if not _matches(node, outermost.group("base")):
            return False
        ancestor = node.parent
        while ancestor is not None and ancestor.tag != "#root":
            if _matches(ancestor, outermost.group("ancestor")):
                return False
            ancestor = ancestor.parent
        return True

    has = _HAS_CHILD.match(selector)
    if has:
        if not _matches(node, has.group("base") or "*"):
            return False
        return any(_matches(child, has.group("child")) for child in node.children)

    if selector == "*":
        return node.tag != "#text"

    simple = _SIMPLE.match(selector)
    if not simple:
        raise UnsupportedSelector(selector)
    tag = simple.group("tag")
    attrs = simple.group("attrs") or ""
    if not tag and not attrs:
        raise UnsupportedSelector(selector)
    if tag and node.tag != tag:
        return False
    if node.tag == "#text":
        return False
    return _match_attrs(node, attrs)


class FakeLocator:
    def __init__(self, nodes: list[Node], page: "FakePage | None" = None):
        self._nodes = nodes
        self._page = page

    async def count(self) -> int:
        return len(self._nodes)

    def nth(self, index: int) -> "FakeLocator":
        return FakeLocator(self._nodes[index : index + 1], self._page)

    @property
    def first(self) -> "FakeLocator":
        return FakeLocator(self._nodes[:1], self._page)

    def locator(self, selector: str) -> "FakeLocator":
        # `xpath=…` is the escalation target's grammar (`ancestor-or-self`),
        # and this shim has no XPath engine. Answering "no matches" would be a
        # lie of the kind the module docstring forbids, so it is answered
        # honestly as "unsupported here" — the escalation is exercised against
        # the scripted fake in `tests/fakes.py`, where the target can be
        # declared, and this shim's job is the HTML-shaped evidence.
        if selector.startswith("xpath="):
            return FakeLocator(self._button_ancestors(selector), self._page)
        out: list[Node] = []
        for node in self._nodes:
            out.extend(d for d in node.descendants() if _matches(d, selector))
        return FakeLocator(out, self._page)

    def _button_ancestors(self, selector: str) -> list[Node]:
        """The one XPath this shim understands: the escalation's target.

        Compared against the production constant rather than re-spelt, so that
        changing the escalation's XPath makes this raise (loudly, per the module
        docstring) instead of quietly resolving to nothing — a shim that
        answered "no such ancestor" for a selector it no longer recognised would
        turn "the escalation stopped working" into a passing test.
        """
        from app.login import BUTTON_ANCESTOR_XPATH

        if selector != BUTTON_ANCESTOR_XPATH:
            raise UnsupportedSelector(selector)
        out: list[Node] = []
        for node in self._nodes:
            current: Node | None = node
            while current is not None and current.tag != "#root":
                if current.tag == "button" or current.attrs.get("role") == "button":
                    out.append(current)
                    break
                current = current.parent
        return out

    async def click(self, timeout: Any = None, force: bool = False) -> None:
        """Records the click. The document does **not** change as a result.

        That is the point rather than a shortcut: a fixture whose DOM never
        moves is exactly the observed failure — the click landed, the platform
        stayed put — and it is the one shape a mock cannot fake convincingly.
        """
        if not self._nodes:
            raise RuntimeError("locator resolved to nothing")
        if self._page is not None:
            self._page.clicks.append(self._nodes[0])

    async def scroll_into_view_if_needed(self, timeout: Any = None) -> None:
        return None

    async def inner_text(self) -> str:
        if not self._nodes:
            raise RuntimeError("locator resolved to nothing")
        return self._nodes[0].inner_text_value()

    async def get_attribute(self, name: str) -> str | None:
        if not self._nodes:
            raise RuntimeError("locator resolved to nothing")
        return self._nodes[0].attrs.get(name)

    async def is_visible(self) -> bool:
        # The fixture has no styling, so everything present is visible. The
        # real page's hidden-node problem is the validator's concern (see
        # `dom.visible_marker_texts`), not the works list's.
        return bool(self._nodes)


class FakePage:
    """`locator` / `get_by_text` over a parsed HTML document."""

    def __init__(self, html: str, url: str = "https://creator.douyin.com/creator-micro/content/manage"):
        self.root = parse_html(html)
        self.url = url
        # Nodes that were clicked, in order. The document is immutable, so this
        # is the only record that anything happened to it.
        self.clicks: list[Node] = []

    def locator(self, selector: str) -> FakeLocator:
        if selector.startswith("xpath="):
            raise UnsupportedSelector(selector)
        return FakeLocator(
            [n for n in self.root.descendants() if _matches(n, selector)], self
        )

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        out: list[Node] = []
        for node in self.root.descendants():
            if node.tag == "#text":
                continue
            # Only leaf-ish nodes, so an ancestor does not report every string
            # on the page as its own text — which would make `exact=True`
            # meaningless, and `exact=True` is precisely what the empty-state
            # check depends on.
            own = "".join(
                c.text for c in node.children if c.tag == "#text"
            ).strip()
            if not own:
                continue
            if (own == text) if exact else (text in own):
                out.append(node)
        return FakeLocator(out, self)
