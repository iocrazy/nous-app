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
actually writes:

    tag                              a
    [class*="x"] / [class^="x"]      attribute contains / starts-with
    [href*="x"]                      same, on any attribute
    div:has(> a[href*="/video/"])    element with a matching DIRECT child

Anything outside that grammar raises, loudly. A shim that silently returned
"no matches" for a selector it did not understand would turn a broken test into
a passing one — the exact failure mode these tests exist to catch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

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
    def __init__(self, nodes: list[Node]):
        self._nodes = nodes

    async def count(self) -> int:
        return len(self._nodes)

    def nth(self, index: int) -> "FakeLocator":
        return FakeLocator(self._nodes[index : index + 1])

    @property
    def first(self) -> "FakeLocator":
        return FakeLocator(self._nodes[:1])

    def locator(self, selector: str) -> "FakeLocator":
        out: list[Node] = []
        for node in self._nodes:
            out.extend(d for d in node.descendants() if _matches(d, selector))
        return FakeLocator(out)

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

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(
            [n for n in self.root.descendants() if _matches(n, selector)]
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
        return FakeLocator(out)
