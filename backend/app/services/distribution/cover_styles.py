"""封面风格 = category='cover' 的 skill。

内置风格 ``viral-video-cover`` 的两段骨架已经转录进 ``cover_prompt.py``（逐句可测）。
用户再导入别的封面 skill 时，走这里的**通用驱动**：从 SKILL.md 里抠出
``## Prompt Skeleton`` / ``## Four-Draft Preview Prompt Skeleton`` 两段围栏文本，填
``[topic]`` / ``[headline]``，其余方括号占位行整行丢掉并明说"细节你自己定"，再按画幅
换词。这样"风格"真的是 skill 正文在决定模型看到什么，而不是我们替每个 skill 各写一份。

一个 skill 要能当封面风格，只需要三件事：``category='cover'``、正文里有上面两个标题
下的 ```text 围栏。缺骨架的 skill 会在生成时 422 点名，不会静默退回内置风格。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from uuid import UUID

from app.services.distribution.cover_prompt import (
    COVER_ASPECTS,
    CoverPromptInput,
    _aspect_words,
    _labels_clause,
    _with_instructions,
)

BUILTIN_SLUG = "viral-video-cover"
BUILTIN_NAME = "Viral Video Cover"
BUILTIN_DESCRIPTION = (
    "Dark background, one huge headline, one strong face. Four drafts in one "
    "grid, then the picked one redrawn full size."
)
COVER_CATEGORY = "cover"

_STAGE1_HEADING = "Four-Draft Preview Prompt Skeleton"
_STAGE2_HEADING = "Prompt Skeleton"
_PLACEHOLDER = re.compile(r"\[[^\]\n]+\]")
# 骨架里的小标签禁令，两种原文措辞。按词匹配、空白宽容 —— skill 正文是人写的
# markdown，同一句话在文件里换行很正常，不能因为换了行就摘不掉。
_SMALL_LABEL_BANS = tuple(
    re.compile(r"\s+".join(map(re.escape, phrase.split())))
    for phrase in (
        "no small platform-style labels, no search UI, no recording marks, "
        "no badges or corner tags,",
        "Do not add any other small labels, badges, corner tags, recording marks, "
        "search UI, watermarks, or platform-style stickers.",
    )
)


@dataclass(frozen=True)
class CoverStyle:
    slug: str
    name: str
    description: str
    requires_person: bool
    builtin: bool
    body_md: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "requires_person": self.requires_person,
            "aspects": list(COVER_ASPECTS),
            "builtin": self.builtin,
        }


BUILTIN_STYLE = CoverStyle(
    slug=BUILTIN_SLUG,
    name=BUILTIN_NAME,
    description=BUILTIN_DESCRIPTION,
    requires_person=True,
    builtin=True,
)


def requires_person(body_md: str) -> bool:
    """这套 skill 要不要人物参考。骨架里点名了 character reference 就是要。"""
    return "character reference" in (body_md or "").lower()


def style_from_skill(row: dict[str, Any]) -> CoverStyle:
    body = row.get("body_md") or row.get("content_md") or ""
    slug = str(row.get("slug") or "")
    return CoverStyle(
        slug=slug,
        name=str(row.get("name") or slug),
        description=str(row.get("description") or ""),
        requires_person=requires_person(body),
        builtin=slug == BUILTIN_SLUG,
        body_md=body,
    )


def merge_styles(rows: Iterable[dict[str, Any]]) -> list[CoverStyle]:
    """可见的封面 skill → 风格列表。内置风格永远在最前，且只出现一次。

    内置那条的 slug 与用户导入的 skill 同名时，名字/描述用 skill 行的（那是用户
    看得见、改得了的），但生成仍走代码里转录好的骨架。
    """
    out: list[CoverStyle] = []
    seen: set[str] = set()
    builtin = BUILTIN_STYLE
    for row in rows:
        if (row.get("category") or "") != COVER_CATEGORY:
            continue
        st = style_from_skill(row)
        if not st.slug or st.slug in seen:
            continue
        if st.slug == BUILTIN_SLUG:
            builtin = CoverStyle(
                slug=BUILTIN_SLUG,
                name=st.name,
                description=st.description or BUILTIN_DESCRIPTION,
                requires_person=True,
                builtin=True,
            )
            seen.add(BUILTIN_SLUG)
            continue
        seen.add(st.slug)
        out.append(st)
    return [builtin, *out]


async def list_cover_styles(user_id: str) -> list[CoverStyle]:
    """当前用户看得见的封面风格（内置 + category='cover' 的可见 skill）。

    可见性沿用 AI Library 的口径（公开 / 自己的 / 所在团队 / 所在项目），所以直接
    借它的三个 helper —— 两处各写一套"谁能看到哪些 skill"必然漂移。
    """
    from app.api.ai_library_router import (
        _coerce_user_uuid,
        _fetch_user_project_ids,
        _fetch_user_team_ids,
        _repos,
    )

    _, skill_repo = _repos()
    uid: UUID = _coerce_user_uuid(str(user_id))
    team_ids = await _fetch_user_team_ids(uid)
    project_ids = await _fetch_user_project_ids(uid)
    rows = await skill_repo.list_accessible(
        user_id=uid, team_ids=team_ids, project_ids=project_ids
    )
    return merge_styles(rows)


async def resolve_cover_style(user_id: str, slug: str) -> Optional[CoverStyle]:
    for st in await list_cover_styles(user_id):
        if st.slug == slug:
            return st
    return None


# ── generic skeleton driver ──────────────────────────────────────────────


def extract_skeleton(body_md: str, heading: str) -> Optional[str]:
    """``## <heading>`` 标题下的第一个围栏代码块正文；没有就 None。"""
    if not body_md:
        return None
    pat = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$(.*?)(?=^##\s|\Z)",
        re.M | re.S,
    )
    m = pat.search(body_md)
    if not m:
        return None
    fence = re.search(r"```[a-zA-Z]*\n(.*?)```", m.group(1), re.S)
    return fence.group(1).strip() if fence else None


def _fill(skeleton: str, data: CoverPromptInput, *, stage: int) -> str:
    topic = (data.topic or "").strip()
    aspect, orient = _aspect_words(data.aspect)
    text = skeleton.replace("[topic]", topic)
    headline = (data.headline or "").strip()
    text = text.replace(
        '"[headline]"',
        f'"{headline}"' if headline else "the headline wording of the selected draft",
    )
    if aspect != "3:4":
        text = text.replace("3:4", aspect).replace("vertical", orient)
    # 剩下的占位行（Draft 1: [headline A]… / Visual concept: [..]）整行丢掉，
    # 那是模型的活，把方括号原样发出去它会照着画字。
    kept = [ln for ln in text.splitlines() if not _PLACEHOLDER.search(ln)]
    dropped = len(text.splitlines()) - len(kept)
    text = "\n".join(kept).strip()
    if dropped:
        text += "\n\nInvent the unspecified details yourself" + (
            ": make the four drafts clearly different in headline wording, "
            "character expression, gesture, composition, background mood, "
            "and color accents."
            if stage == 1
            else " (visual concept, scene, and headline treatment)."
        )
    if data.allow_small_labels:
        for ban in _SMALL_LABEL_BANS:
            text = ban.sub("", text)
        text += "\n\n" + _labels_clause(True)
    return text


def build_from_skill(style: CoverStyle, data: CoverPromptInput) -> str:
    """用一个封面 skill 的骨架组装阶段一/二的 prompt。"""
    topic = (data.topic or "").strip()
    if not topic:
        raise ValueError("cover prompt needs a topic")
    body = style.body_md or ""
    if data.selected_draft is None:
        skeleton = extract_skeleton(body, _STAGE1_HEADING)
        if not skeleton:
            raise ValueError(
                f"cover style {style.slug!r} has no '## {_STAGE1_HEADING}' block"
            )
        text = _fill(skeleton, data, stage=1)
    else:
        if data.selected_draft not in (1, 2, 3, 4):
            raise ValueError(f"selected_draft must be 1-4, got {data.selected_draft!r}")
        skeleton = extract_skeleton(body, _STAGE2_HEADING)
        if not skeleton:
            raise ValueError(
                f"cover style {style.slug!r} has no '## {_STAGE2_HEADING}' block"
            )
        n = data.selected_draft
        text = _fill(skeleton, data, stage=2)
        text += (
            "\n\nThe supplied 2x2 grid image is a set of four drafts. Redraw "
            f"draft number {n} — the one labelled {n} — as a single full-size "
            "cover. Preserve that draft's headline direction, character emotion, "
            "composition, background mood, and colour logic. Remove the "
            "selection number from the final cover. The final image is one "
            "cover, not a grid."
        )
    return "\n\n".join(_with_instructions([text], data))
