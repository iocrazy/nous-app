"""#tag parsing contract tests.

The SAME semantics will be mirrored in frontend/components/Inspiration/noteTags.ts
(P2) — if you change a rule here, change it there and in the spec §2.4.
"""

from app.services.inspiration.note_tags import parse_tags


def test_basic_tags_extracted_in_order():
    assert parse_tags("idea #hooks and #formats now") == ["hooks", "formats"]


def test_dedup_and_lowercase():
    assert parse_tags("#Hooks #hooks #HOOKS") == ["hooks"]


def test_cjk_and_hyphen_underscore_allowed():
    assert parse_tags("试试 #灵感 #short-form #a_b") == ["灵感", "short-form", "a_b"]


def test_trailing_punctuation_stripped():
    assert parse_tags("end #hooks. and (#formats)") == ["hooks", "formats"]


def test_not_a_tag_inside_word_or_url_fragment():
    assert parse_tags("c# is a language, see x.com/a#b") == []


def test_code_blocks_are_ignored():
    md = "text #real\n```\n# comment not a tag\nfoo #fake\n```\n`inline #fake2`"
    assert parse_tags(md) == ["real"]


def test_empty_and_bare_hash():
    assert parse_tags("") == []
    assert parse_tags("# heading text") == []  # markdown 标题不是 tag(# 后有空格)
