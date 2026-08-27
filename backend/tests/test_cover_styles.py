"""封面风格 = category='cover' 的 skill；通用骨架驱动。

钉住的性质：
- 内置风格永远在列表最前、只出现一次；非 cover 类的 skill 不进列表。
- 同 slug 的用户 skill 只覆盖名字/描述，不会把内置的当成"通用 skill"去驱动。
- 骨架抽取：只取标题下的围栏正文；没有就 None → 生成时 ValueError 点名。
- 填槽：[topic] 进去、[headline] 进去；剩下的占位行整行丢掉并明说"细节你定"。
- 画幅：4:3 时 "3:4"/"vertical" 全换；3:4 时原样。
- 小标签开关打开时，骨架里的禁令被摘掉并追加允许句（不许自相矛盾）。
- 阶段二追加"重画第 N 格、去掉编号"。
- requires_person 由正文是否点名 character reference 决定。
"""

from __future__ import annotations

import pytest

from app.services.distribution.cover_prompt import CoverPromptInput
from app.services.distribution.cover_styles import (
    BUILTIN_SLUG,
    build_from_skill,
    extract_skeleton,
    merge_styles,
    requires_person,
    style_from_skill,
)

BODY = """# Neon Cover

## Core Rule
Two-stage. Use the supplied character reference image.

## Prompt Skeleton

```text
Create an original 3:4 vertical neon cover image.
Topic: [topic].
Main text on image: "[headline]" in neon type.
Visual concept: [one sentence scene].
Safety/IP: no copyrighted characters, no small platform-style labels, no search UI,
no recording marks, no badges or corner tags, no unrelated brand logos.
```

## Four-Draft Preview Prompt Skeleton

```text
Create one 2x2 preview grid image. The overall image is 3:4 vertical. Topic: [topic].
Label the four cells only with clear numbers 1, 2, 3, and 4.
Do not add any other small labels, badges, corner tags, recording marks,
search UI, watermarks, or platform-style stickers.
Draft 1: [headline A], [background A].
Draft 2: [headline B], [background B].
Neon palette, dark background.
```
"""

ROW = {"slug": "neon-cover", "name": "Neon Cover", "category": "cover", "body_md": BODY}
STYLE = style_from_skill(ROW)


def _in(**kw) -> CoverPromptInput:
    kw.setdefault("topic", "三分钟看懂内容差")
    return CoverPromptInput(**kw)


class TestList:
    def test_builtin_first_once_and_non_cover_skills_excluded(self):
        rows = [
            {
                "slug": "script-outline",
                "name": "x",
                "category": "script",
                "body_md": "",
            },
            ROW,
            {
                "slug": BUILTIN_SLUG,
                "name": "My Viral",
                "category": "cover",
                "body_md": BODY,
            },
            {"slug": "neon-cover", "name": "dup", "category": "cover", "body_md": BODY},
        ]
        styles = merge_styles(rows)
        assert [s.slug for s in styles] == [BUILTIN_SLUG, "neon-cover"]
        assert styles[0].builtin is True and styles[0].name == "My Viral"
        assert styles[0].body_md is None, "内置的不走通用驱动"
        assert styles[1].builtin is False

    def test_builtin_is_present_even_with_no_visible_skills(self):
        styles = merge_styles([])
        assert [s.slug for s in styles] == [BUILTIN_SLUG]
        assert styles[0].requires_person is True

    def test_requires_person_follows_the_body(self):
        assert requires_person(BODY) is True
        assert requires_person("# Plain\n\nno people here") is False
        assert STYLE.requires_person is True


class TestSkeleton:
    def test_extracts_only_the_fenced_text_under_the_heading(self):
        s1 = extract_skeleton(BODY, "Four-Draft Preview Prompt Skeleton")
        s2 = extract_skeleton(BODY, "Prompt Skeleton")
        assert s1.startswith("Create one 2x2 preview grid image")
        assert s2.startswith("Create an original 3:4 vertical neon cover")
        assert "Four-Draft" not in s2

    def test_missing_heading_is_none_and_build_names_it(self):
        assert extract_skeleton("# nothing", "Prompt Skeleton") is None
        bare = style_from_skill({**ROW, "body_md": "# nothing"})
        with pytest.raises(ValueError, match="Four-Draft Preview Prompt Skeleton"):
            build_from_skill(bare, _in())


class TestStageOne:
    def test_fills_topic_and_drops_placeholder_lines(self):
        p = build_from_skill(STYLE, _in())
        assert "Topic: 三分钟看懂内容差." in p
        assert "[headline A]" not in p and "Draft 1:" not in p
        assert "Invent the unspecified details yourself" in p
        assert "Neon palette, dark background." in p

    def test_keeps_3_4_by_default_and_swaps_for_4_3(self):
        assert "3:4 vertical" in build_from_skill(STYLE, _in())
        p = build_from_skill(STYLE, _in(aspect="4:3"))
        assert "4:3 horizontal" in p and "3:4" not in p and "vertical" not in p

    def test_small_labels_on_removes_the_ban_and_adds_the_allowance(self):
        p = build_from_skill(STYLE, _in(allow_small_labels=True))
        assert "Do not add any other small labels" not in p
        assert "are allowed to add platform feel" in p
        off = build_from_skill(STYLE, _in())
        assert "Do not add any other small labels" in off

    def test_blank_topic_raises(self):
        with pytest.raises(ValueError, match="topic"):
            build_from_skill(STYLE, _in(topic="  "))


class TestStageTwo:
    def test_names_the_draft_and_removes_the_number(self):
        p = build_from_skill(STYLE, _in(selected_draft=3))
        assert "draft number 3" in p and "labelled 3" in p
        assert "Remove the selection number" in p
        assert "Visual concept" not in p, "占位行要丢"

    def test_headline_quoted_or_deferred(self):
        assert '"内容差的真相"' in build_from_skill(
            STYLE, _in(selected_draft=1, headline="内容差的真相")
        )
        assert "the headline wording of the selected draft" in build_from_skill(
            STYLE, _in(selected_draft=1)
        )

    def test_small_labels_on_strips_the_ban_inside_the_safety_line(self):
        p = build_from_skill(STYLE, _in(selected_draft=1, allow_small_labels=True))
        assert "no small platform-style labels" not in p
        assert "no copyrighted characters" in p

    @pytest.mark.parametrize("bad", [0, 5])
    def test_out_of_range_draft_raises(self, bad):
        with pytest.raises(ValueError, match="selected_draft"):
            build_from_skill(STYLE, _in(selected_draft=bad))

    def test_instructions_ride_along_last(self):
        p = build_from_skill(STYLE, _in(selected_draft=1, instructions="人物指向右侧"))
        assert p.rstrip().endswith("人物指向右侧")
