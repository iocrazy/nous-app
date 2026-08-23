"""封面工作室的两阶段 prompt 组装。

这些是纯函数，所以是钉住"模型到底收到什么"最便宜的地方。测的五件事：

1. **主题真的进去了**，而且两个阶段都进。
2. **小标签开关两侧都成立** —— 而且开关打开时，阶段二 Safety 句里夹带的那几条
   禁令必须**同时**摘掉。一句允许、一句禁止同进 prompt，模型收到的是自相矛盾的
   指令，用户看到的只是"开关没生效"。
3. **阶段一不替模型填四个方向**。骨架里 `Draft 1: [headline A]…` 那四行是模型的
   活；把方括号占位符原样发出去才是真的 bug。
4. **阶段二必须点名编号**，且编号缺失/越界要抛错而不是随便挑一格 —— 后者会让用户
   拿到一张他没选的封面，还看不出哪里错了。
5. **空主题抛错**。骨架整个围绕 topic 展开，缺了它模型会自己编一个，产出的封面跟
   用户的视频无关。
"""

from __future__ import annotations

import pytest

from app.services.distribution.cover_prompt import (
    COVER_ASPECT,
    STAGE1_IMAGE_COUNT,
    CoverPromptInput,
    build_stage1_prompt,
    build_stage2_prompt,
)

TOPIC = "三分钟看懂内容差"


def _s1(**kw) -> str:
    return build_stage1_prompt(CoverPromptInput(topic=TOPIC, **kw))


def _s2(**kw) -> str:
    kw.setdefault("selected_draft", 2)
    return build_stage2_prompt(CoverPromptInput(topic=TOPIC, **kw))


class TestShape:
    def test_the_style_is_portrait_only(self):
        # 用户已拍板：这个 skill 只管竖版。横版不是"暂未支持"——整套视觉语法是为
        # 手机竖屏信息流写的。
        assert COVER_ASPECT == "3:4"

    def test_stage_one_is_a_single_image(self):
        """四个草案共用一张 2x2 网格 —— 一轮想法只花一次额度，这是 skill 的
        Core Rule，不是我们的优化。"""
        assert STAGE1_IMAGE_COUNT == 1

    def test_both_stages_state_the_aspect_in_words(self):
        # codex 那条链路上 --size 不被采纳（2026-08-23 实测），画幅只有写进 prompt
        # 才算数。
        assert "3:4" in _s1()
        assert "3:4" in _s2()


class TestTopic:
    def test_the_topic_reaches_both_stages(self):
        assert TOPIC in _s1()
        assert TOPIC in _s2()

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
    def test_a_blank_topic_raises_rather_than_letting_the_model_invent_one(self, bad):
        with pytest.raises(ValueError, match="topic"):
            build_stage1_prompt(CoverPromptInput(topic=bad))
        with pytest.raises(ValueError, match="topic"):
            build_stage2_prompt(CoverPromptInput(topic=bad, selected_draft=1))


class TestStageOne:
    def test_it_asks_for_a_2x2_grid_of_four_complete_covers(self):
        p = _s1()
        assert "2x2" in p
        assert "1, 2, 3, and 4" in p

    def test_it_does_NOT_ship_the_skeleton_placeholders(self):
        """骨架里 `Draft 1: [headline A], [composition A]…` 是给模型填的槽。
        把方括号原样发出去，模型会照着 "[headline A]" 画字。"""
        p = _s1()
        for marker in ("[headline", "[composition", "[background", "[emotion", "[topic"):
            assert marker not in p, f"占位符 {marker} 漏进了 prompt"

    def test_it_tells_the_model_to_invent_the_four_directions(self):
        # 与上一条成对：不填占位符不能变成"没人负责想这四个方向"。
        p = _s1()
        assert "Invent the four directions yourself" in p
        assert "clearly different" in p

    def test_it_pins_the_character_across_all_four(self):
        """"保持同一个人"是这套风格的一半。四格里换脸就等于风格坏了。"""
        p = _s1()
        assert "same person and core facial features" in p

    def test_it_carries_the_grammar_limits(self):
        # 来自 cover-grammar.md：大标题 4-12 汉字、最多 3 层视觉信息。
        p = _s1()
        assert "4-12 Chinese characters" in p
        assert "3 visual layers" in p


class TestStageTwo:
    def test_it_names_the_chosen_draft(self):
        p = build_stage2_prompt(
            CoverPromptInput(topic=TOPIC, selected_draft=3)
        )
        assert "draft number 3" in p
        assert "labelled 3" in p

    def test_it_removes_the_selection_number(self):
        assert "Remove the selection number" in _s2()

    @pytest.mark.parametrize("bad", [None, 0, 5, -1, "2"])
    def test_a_missing_or_out_of_range_draft_raises(self, bad):
        """继续下去等于随便挑一格重画 —— 用户拿到一张他没选的封面，且看不出错在
        哪。这是"挑 A 得到 B"的一种。"""
        with pytest.raises(ValueError, match="selected_draft"):
            build_stage2_prompt(
                CoverPromptInput(topic=TOPIC, selected_draft=bad)  # type: ignore[arg-type]
            )

    def test_a_supplied_headline_is_quoted_verbatim(self):
        p = build_stage2_prompt(
            CoverPromptInput(topic=TOPIC, selected_draft=1, headline="内容差的真相")
        )
        assert '"内容差的真相"' in p

    def test_without_a_headline_it_defers_to_the_draft(self):
        """用户不一定读得清网格里的小字。逼他抄一遍，不如让模型自己从参考图上读。"""
        p = _s2(headline="")
        assert "keep the headline wording of the selected draft" in p


class TestSmallLabelsToggle:
    """⚠️ 两份原文互相矛盾：SKILL.md 第 4 步禁止，cover-grammar.md 明确允许（标题
    就叫「小标签仍允许」）。没有替用户决定 —— 开关，默认关（跟随 SKILL.md）。"""

    def test_default_is_off(self):
        assert CoverPromptInput(topic=TOPIC).allow_small_labels is False

    def test_off_forbids_them_in_stage_one(self):
        p = _s1(allow_small_labels=False)
        assert "Do not add any other small labels" in p
        assert "are allowed to add platform feel" not in p

    def test_on_allows_them_in_stage_one(self):
        p = _s1(allow_small_labels=True)
        assert "are allowed to add platform feel" in p
        assert "Do not add any other small labels" not in p

    def test_on_keeps_the_source_text_limit_that_they_be_original(self):
        """允许的同时必须带上原文的限定条件。只说"可以加"会把安全边界丢掉 ——
        cover-grammar.md 原文是「必须做成通用原创元素，不可复制真实品牌」。"""
        p = _s1(allow_small_labels=True)
        assert "generic and original" in p
        assert "never a copy of a real brand" in p

    def test_off_keeps_the_ban_inside_stage_two_safety_clause(self):
        p = _s2(allow_small_labels=False)
        assert "no small platform-style labels" in p
        assert "no recording marks" in p

    def test_on_ALSO_strips_the_ban_hidden_in_the_safety_clause(self):
        """★ 这条是这组里最容易漏的。

        阶段二骨架的 Safety/IP 句里**夹带**了同一条禁令。只改前面那句而不动这句，
        prompt 里会同时出现"允许小标签"和"no small platform-style labels" ——
        模型收到自相矛盾的指令，而用户看到的只是"开关没生效"。
        """
        p = _s2(allow_small_labels=True)
        assert "no small platform-style labels" not in p
        assert "no search UI" not in p
        assert "no recording marks" not in p
        assert "no badges or corner tags" not in p
        # 其余 Safety 条款必须原样保留 —— 摘的是小标签那几项，不是整句。
        assert "no copied sample faces" in p
        assert "no copyrighted characters" in p
        assert "no unrelated brand logos" in p
