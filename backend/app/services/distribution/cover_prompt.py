"""封面工作室的两阶段 prompt 组装。

skill `viral-video-cover` 的两个骨架原样落到这里，一句都不即兴发挥：

    阶段一  Four-Draft Preview Prompt Skeleton → 一张 2x2 网格，四个草案
    阶段二  Prompt Skeleton                    → 选中那格的全尺寸精修

**为什么两阶段是 skill 的要求而不是我们的优化**：一轮想法只花一次额度（四个草案
共用一张图），而且只有被选中的那一个才会被重画成全尺寸。见 skill 的 Core Rule。

⚠️ 骨架里 `Draft 1..4: [headline A], [composition A]…` 那四行**故意不填**。那是模型
的活 —— 让它自己想四个方向，正是"四个草案"的意思。我们只保证"四个必须明显不同"
这条约束原样传达。

⚠️ **画幅不靠 aspect 参数**。codex 那条链路上 `--size` 不被采纳（2026-08-23 实测，
见 `codex_cli.py` 里 `_ASPECT_TO_PHRASE` 上方的证据块），画幅只有写在 prompt 里才
算数。骨架第一句本来就在说 3:4，这里保持原样即可 —— provider 侧还会再追加一句。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 这个 skill 只管竖版 3:4（用户已拍板）。横版不是"暂未支持"，是这套视觉语法本身
# 就是为手机竖屏信息流写的，换个画幅整套构图规则都不成立。
COVER_ASPECT = "3:4"

# 四个草案共用一张 2x2 网格图，所以阶段一只生成 1 张。
STAGE1_IMAGE_COUNT = 1


@dataclass(frozen=True)
class CoverPromptInput:
    """组装一条封面 prompt 需要的全部输入。"""

    topic: str
    # ⚠️ 两份原文在这一点上互相矛盾：SKILL.md 第 4 步禁止小标签（REC / 搜索框 /
    # 角标），而 cover-grammar.md 明确允许，且那份文件标题就叫「小标签仍允许」。
    # 没有替用户决定 —— 做成开关，**默认关**，即跟随 SKILL.md 的禁止。
    #
    # （设计稿把这条注解写成 "Off follows the newer file" 是反的：允许小标签的恰恰
    # 是较新的 grammar 那份。界面文案按本注释写，别照抄设计稿。）
    allow_small_labels: bool = False
    # 阶段二：用户选中的草案编号（1-4）。
    selected_draft: Optional[int] = None
    # 阶段二：从阶段一那张网格图上，用户看到的那格的标题。可空 —— 用户不一定读得清
    # 网格里的小字，逼他抄一遍不如让模型自己从参考图上读。
    headline: str = ""
    # 创作者写给模型的一句话（设计稿的「提示词」框）。可空。放在骨架末尾、Safety
    # 句之后，并明说不得覆盖安全条款 —— 自由文本进 prompt 的唯一入口。
    instructions: str = ""


def _labels_clause(allow: bool) -> str:
    """小标签那一句。两种措辞都来自原文，不是我们编的。"""
    if allow:
        # cover-grammar.md 原文：「可用小标签增强平台感…但必须做成通用原创元素，
        # 不可复制真实品牌」。允许的同时把那个限定条件一起带上 —— 只说"可以加"
        # 会把原文的安全边界丢掉。
        return (
            "Small platform-style labels (a recording mark, a search bar, a corner "
            "tag) are allowed to add platform feel, but they must be generic and "
            "original — never a copy of a real brand, logo, or app UI."
        )
    return (
        "Do not add any other small labels, badges, corner tags, recording marks, "
        "search UI, watermarks, or platform-style stickers."
    )


def build_stage1_prompt(data: CoverPromptInput) -> str:
    """四草案网格。骨架来自 skill 的 Four-Draft Preview Prompt Skeleton。"""
    topic = (data.topic or "").strip()
    if not topic:
        # 空主题不是一条可以往下走的路径：整个骨架都围绕 topic 展开，缺了它模型
        # 会自己编一个，而用户拿到的封面跟他的视频无关。skill 自己也说了
        # "Ask a question only if the topic is empty"。
        raise ValueError("cover prompt needs a topic")

    parts = [
        (
            "Create one 2x2 preview grid image. The overall image is 3:4 "
            "vertical. Each cell is a complete 3:4 Chinese viral short-video "
            f"cover draft for the same topic: {topic}."
        ),
        (
            "Label the four cells only with clear numbers 1, 2, 3, and 4 for "
            f"selection. {_labels_clause(data.allow_small_labels)}"
        ),
        (
            "All four drafts must preserve the same reference character from "
            "the supplied image: the same person and core facial features. "
            "Change expression, gesture, lighting, and scene per draft."
        ),
        (
            "Invent the four directions yourself: make the four drafts clearly "
            "different in headline wording, character expression, gesture, "
            "composition, background mood, and color accents."
        ),
        (
            "Default to black or dark backgrounds, white/yellow main "
            "typography, bold strokes, strong shadows, crisp cutouts, and "
            "mobile-first readability. Use concrete cinematic scenes instead "
            "of generic vector cards, flat template illustrations, or "
            "decorative abstract panels. Keep the biggest headline to 4-12 "
            "Chinese characters and at most 3 visual layers."
        ),
        (
            "Safety/IP: no copied sample faces, no copyrighted characters, no "
            "exact UI screenshots, no unrelated brand logos or trademarked "
            "marks; use generic original symbols."
        ),
    ]
    return "\n\n".join(_with_instructions(parts, data))


def build_stage2_prompt(data: CoverPromptInput) -> str:
    """选中草案的全尺寸精修。骨架来自 skill 的 Prompt Skeleton。"""
    topic = (data.topic or "").strip()
    if not topic:
        raise ValueError("cover prompt needs a topic")
    draft = data.selected_draft
    if draft not in (1, 2, 3, 4):
        # 阶段二的全部意义就是"把选中的那一个重画一遍"。编号缺失或越界时继续下去，
        # 等于随便挑一格重画 —— 用户会拿到一张他没选的封面，而且看不出哪里错了。
        raise ValueError(f"selected_draft must be 1-4, got {draft!r}")

    headline_line = (
        f'Main text on image: "{data.headline.strip()}" in mainly white and yellow '
        "Chinese display type; avoid orange and green unless requested."
        if data.headline.strip()
        else (
            "Main text on image: keep the headline wording of the selected draft, "
            "in mainly white and yellow Chinese display type; avoid orange and "
            "green unless requested."
        )
    )

    parts = [
        ("Create an original 3:4 vertical Chinese viral short-video cover " "image."),
        f"Topic: {topic}.",
        (
            "The supplied 2x2 grid image is a set of four drafts. Redraw "
            f"draft number {draft} — the one labelled {draft} — as a single "
            "full-size cover. Preserve that draft's headline direction, "
            "character emotion, composition, background mood, and colour "
            "logic."
        ),
        (
            "Remove the selection number from the final cover. The final "
            "image is one cover, not a grid."
        ),
        headline_line,
        (
            "Character reference: use the supplied character image; preserve "
            "the same person and core facial features while changing "
            "expression and gesture for the topic."
        ),
        (
            "Composition: huge bold headline in the top third, expressive "
            "human/character cutout in the lower half, oversized symbolic "
            "object or dramatic scene behind them, strong depth, clean "
            "readable layout."
        ),
        (
            "Style: black or dark cinematic background, high contrast, bold "
            "white/yellow Chinese display typography, thick black/white "
            "strokes, strong drop shadows, crisp edges, mobile-first "
            "readability, energetic short-video thumbnail, concrete "
            "photographic/cinematic background instead of flat vector "
            "templates."
        ),
        _safety_clause(data.allow_small_labels),
    ]
    return "\n\n".join(_with_instructions(parts, data))


def _safety_clause(allow_small_labels: bool) -> str:
    """阶段二的 Safety/IP 句。

    ⚠️ 原文这一句里**夹带了小标签禁令**（"no small platform-style labels, no search
    UI, no recording marks, no badges or corner tags"）。开关打开时必须把那几项从这
    句里摘掉，否则一句允许、一句禁止同时进 prompt，模型收到的是自相矛盾的指令 ——
    而用户看到的只是"开关没生效"。
    """
    base = (
        "Safety/IP: no copied sample faces, no copyrighted characters, no exact "
        "UI screenshots, "
    )
    if not allow_small_labels:
        base += (
            "no small platform-style labels, no search UI, no recording marks, "
            "no badges or corner tags, "
        )
    return base + (
        "no unrelated brand logos or trademarked marks; use generic original "
        "symbols."
    )


def _with_instructions(parts: list[str], data: CoverPromptInput) -> list[str]:
    """把创作者的自由文本挂在骨架末尾。

    放在 Safety 句**之后**并明说优先级：模型收到的最后一句是"照创作者说的做，
    但别越过上面的安全规则"，而不是让一句自由文本有机会读成对安全条款的覆盖。
    空白即不加 —— 不为空指令多发一句话。
    """
    text = (data.instructions or "").strip()
    if not text:
        return parts
    return parts + [
        "Extra direction from the creator (follow it unless it conflicts with "
        f"the rules above): {text}"
    ]
