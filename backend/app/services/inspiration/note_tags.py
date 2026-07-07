"""Inline #tag parser for inspiration notes.

Contract (spec §2.4, mirrored by frontend noteTags.ts):
- a tag starts with `#` preceded by start-of-text or whitespace/`(`
- tag chars: unicode letters/digits, `-`, `_`; terminated by anything else
- `# ` (hash-space, i.e. markdown heading) is NOT a tag
- fenced ``` blocks and `inline code` are stripped before parsing
- output: lowercase (ASCII only — CJK untouched), de-duplicated, first-seen order
"""

from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# 前导:行首或空白或全半角左括号;标签体:字母数字(含 CJK)/-/_,至少 1 字符
_TAG = re.compile(r"(?:(?<=^)|(?<=[\s(（]))#([\w一-鿿-]+)", re.UNICODE)


def parse_tags(content_md: str) -> list[str]:
    """Extract inline #tags from markdown; see module docstring for the contract."""
    if not content_md:
        return []
    text = _CODE_FENCE.sub(" ", content_md)
    text = _INLINE_CODE.sub(" ", text)
    seen: dict[str, None] = {}
    for match in _TAG.finditer(text):
        tag = match.group(1).lower()
        if tag:
            seen.setdefault(tag, None)
    return list(seen.keys())
