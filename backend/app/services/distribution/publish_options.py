"""发布表单里那些**平台原生**的选项：自主声明、定时窗口、合集。

为什么单独一个模块
==================
这三样东西同时被四处需要 —— 请求 schema（提交即拒）、``SessionAdapter``
的 fail-fast（起浏览器之前再拒一次）、workflow（组装 ``platform_options``）、
以及测试。写在其中任何一处，另外三处就得 import 一个层级不对的东西
（schema import workflow / workflow import schema）。这里只有常量与纯函数，
不 import 项目内任何模块，谁都可以依赖它。

值为什么是中文原文
==================
``self_declaration`` 的六个取值是**抖音创作页上的原文**，不是我们发明的枚举。
浏览器侧要用它做 DOM 文本匹配（`get_by_text("内容由AI生成")`），中间任何一次
翻译/编码转换都会让匹配失败，而失败形态是"选项没选中但发布成功了" ——
合规字段静默丢失是这里最坏的结果。所以 backend / browser / DB 三处存的都是
同一份原文，UI 上的英文只是 i18n 的显示层（CLAUDE.md「UI 语言规范」）。

注意这不违反 spec §6.1 a（通道枚举里不得有平台专属值）：那条约束的是
``SessionStatus``。平台独有字段的正规去处正是 ``PublishIntent.platform_options``
—— backend 只透传不解释，键名由各平台 uploader 自定。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# ── 自主声明（抖音「自主声明」下拉） ────────────────────────────────

SELF_DECLARATION_AI = "内容由AI生成"
SELF_DECLARATION_OPINION = "内容为个人观点或见解"
SELF_DECLARATION_REPOST = "内容为转载信息"
SELF_DECLARATION_MARKETING = "内容含营销推广信息"
SELF_DECLARATION_FICTION = "虚构演绎，仅供娱乐"
SELF_DECLARATION_NONE = "无需添加自主声明"

#: 抖音发布页的六个选项，**逐字**照抄，顺序与页面一致。
#: 改这里之前先去真实创作页核对 —— 平台改一个字，DOM 匹配就断，
#: 而断的形态是"声明没加上但发布成功"。
SELF_DECLARATIONS: tuple[str, ...] = (
    SELF_DECLARATION_AI,
    SELF_DECLARATION_OPINION,
    SELF_DECLARATION_REPOST,
    SELF_DECLARATION_MARKETING,
    SELF_DECLARATION_FICTION,
    SELF_DECLARATION_NONE,
)

DOUYIN_SELF_DECLARATIONS = frozenset(SELF_DECLARATIONS)


def resolve_self_declaration(
    *, ai_content: bool, self_declaration: Optional[str]
) -> Optional[str]:
    """``ai_content``(旧字段) + ``self_declaration``(新字段) → 实际要选的那一项。

    **产品决策，下一个人需要知道为什么**（团队约定 2026-08-06）：

    ``publish_tasks.ai_content`` 从 mig 356 起就存在，但从来没有被送到发布页上
    —— UI 上那句 "Douyin requires setting the AI label in-app" 就是在说"我们
    存了，但你得自己再去 App 里点一次"。会话通道能真的替用户点了，于是必须
    决定这两个字段谁说了算。选择是 **自动映射 + 允许覆盖**：

    - 显式选了 ``self_declaration`` → 用它，**即使与 ``ai_content`` 冲突**。
      用户在两个控件上给了两个答案时，后填的那个（声明下拉是新加的、更具体的
      合规控件）胜出。前端会在冲突时给出可见提示，但不阻止提交 —— 合规声明
      是用户的责任与选择，我们不替他改。
    - 没选，但 ``ai_content=True`` → 自动补 ``内容由AI生成``。这条是本函数
      存在的理由：它把一个存了两个版本、从没生效过的布尔位，变成页面上真实
      的一次点击。
    - 都没有 → ``None`` = **不碰**那个控件，保持平台默认。

    ``None`` 与 ``无需添加自主声明`` 刻意不等价：前者是"我们不表态"，后者是
    "用户显式选了不加声明"。浏览器侧据此决定是否真的去点一下那个选项。
    """
    if self_declaration:
        return self_declaration
    if ai_content:
        return SELF_DECLARATION_AI
    return None


def self_declaration_conflicts(
    *, ai_content: bool, self_declaration: Optional[str]
) -> bool:
    """标了 AI 内容，却显式声明成别的（含"无需添加"）。

    不是错误 —— 是**值得让用户看见**的一件事，UI 据此给提示。放在这里而不是
    前端，是为了让"什么算冲突"只有一个定义。
    """
    return bool(
        ai_content and self_declaration and self_declaration != SELF_DECLARATION_AI
    )


# ── 定时发布窗口 ────────────────────────────────────────────────

#: 抖音自己的底线：「2 小时后 ~ 14 天内」。两端都是闭区间。
SCHEDULE_PLATFORM_MIN_LEAD = timedelta(hours=2)
SCHEDULE_MAX_AHEAD = timedelta(days=14)

#: 上传余量。定时时间是在**视频传完之后**才填进创作页的，而一次上传是分钟级
#: 的。卡在 2h00m~2h10m 的请求会通过我们所有的校验，然后在传完几百 MB 之后被
#: 抖音自己拒掉 —— 最贵的一种发现方式。所以对用户暴露的下限比平台底线高一点。
#: 与 nous-browser 的 ``SCHEDULE_LEAD_SLACK`` 是同一个数（跨服务约定
#: 2026-08-06）：两侧不一致会造成"UI 放行、浏览器拒绝"的夹缝。
SCHEDULE_LEAD_SLACK = timedelta(minutes=10)

#: 实际生效的下限。三层校验（请求 schema / fail-fast / 前端）用的都是它。
SCHEDULE_MIN_LEAD = SCHEDULE_PLATFORM_MIN_LEAD + SCHEDULE_LEAD_SLACK

#: 上限不加余量：时间流逝只会让目标更近，不会让它更远。

SCHEDULE_TOO_SOON = (
    "scheduled_at must be at least 2 hours 10 minutes from now "
    "(the platform minimum is 2 hours; the extra margin covers the upload)"
)
SCHEDULE_TOO_FAR = "scheduled_at must be within 14 days from now"
SCHEDULE_NAIVE = "scheduled_at must include a timezone offset"


def validate_scheduled_at(
    scheduled_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    min_lead: timedelta = SCHEDULE_MIN_LEAD,
    max_ahead: timedelta = SCHEDULE_MAX_AHEAD,
) -> Optional[str]:
    """校验定时时间，返回问题描述（``None`` = 通过）。纯函数，``now`` 可注入。

    返回字符串而不是 raise：调用方有两种，一种要把它变成 422（请求 schema），
    另一种要把它并进 fail-fast 的 problems 列表（``validate_publish_intent``）。

    **拒绝 naive datetime**，不做"当成 UTC"的兜底：时区猜错的代价是帖子提前
    或推迟 8 小时发出去 —— 一个静默的、不可撤销的错误。宁可 422。

    窗口在**两层**各算一次（提交时 / 起浏览器前），因为两次之间隔着排队与
    几百 MB 的上传：提交时刚过线的任务，真到设置定时那一刻可能已经滑到线内，
    与其让抖音在最后一步拒掉，不如在起浏览器之前就说清楚。默认下限已经含了
    ``SCHEDULE_LEAD_SLACK``，见该常量。
    """
    if scheduled_at is None:
        return None
    if (
        scheduled_at.tzinfo is None
        or scheduled_at.tzinfo.utcoffset(scheduled_at) is None
    ):
        return SCHEDULE_NAIVE
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta = scheduled_at - ref
    if delta < min_lead:
        return SCHEDULE_TOO_SOON
    if delta > max_ahead:
        return SCHEDULE_TOO_FAR
    return None


# ── 合集 ────────────────────────────────────────────────────────

#: 合集名的长度上界。**不是**平台的真实上限（没实测过），只是一个
#: "明显不合理"的兜底 —— 与 ``PlatformSessionProfile`` 的口径一致：
#: 不确定的上限宁可不设，也不要凭空写一个数字去静默拒掉合法内容。
MAX_COLLECTION_NAME_LEN = 100


def normalize_collection(name: Optional[str]) -> Optional[str]:
    """去空白；空串 → ``None``（= 不选合集）。超长 raise。

    合集是**按名称选已有合集**（浏览器侧在下拉里找同名项），不是创建新合集。
    找不到同名的由浏览器侧回类型化失败，backend 无从校验存在性。
    """
    if name is None:
        return None
    trimmed = name.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_COLLECTION_NAME_LEN:
        raise ValueError(
            f"collection name exceeds {MAX_COLLECTION_NAME_LEN} characters"
        )
    return trimmed


# ── 配乐（抖音「选择音乐」） ─────────────────────────────────────

#: 曲名的长度上界。同 ``MAX_COLLECTION_NAME_LEN`` 的口径：不是平台实测上限，
#: 只是一个"明显不合理"的兜底。
MAX_MUSIC_NAME_LEN = 100


def normalize_music(name: Optional[str]) -> Optional[str]:
    """去空白；空串 → ``None``（= 不碰音乐控件 = 平台默认原声）。超长 raise。

    与 ``normalize_collection`` 同形，但语义上更严重一格：合集没挂上是归档
    问题（浏览器侧降级放行，事后能在平台补挂），配乐没选上是分发问题，而且
    作品发出去之后**换不了配乐**。所以浏览器侧那一步任何一种失配都直接失败，
    不降级 —— 详见 ``douyin_publish._set_music``。

    存在性 backend 无从校验（曲名不是封闭词表），由浏览器侧在平台自己的搜索
    结果里判定。
    """
    if name is None:
        return None
    trimmed = name.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_MUSIC_NAME_LEN:
        raise ValueError(f"music name exceeds {MAX_MUSIC_NAME_LEN} characters")
    return trimmed


__all__ = [
    "DOUYIN_SELF_DECLARATIONS",
    "MAX_COLLECTION_NAME_LEN",
    "MAX_MUSIC_NAME_LEN",
    "SCHEDULE_LEAD_SLACK",
    "SCHEDULE_MAX_AHEAD",
    "SCHEDULE_MIN_LEAD",
    "SCHEDULE_PLATFORM_MIN_LEAD",
    "SCHEDULE_NAIVE",
    "SCHEDULE_TOO_FAR",
    "SCHEDULE_TOO_SOON",
    "SELF_DECLARATIONS",
    "SELF_DECLARATION_AI",
    "SELF_DECLARATION_FICTION",
    "SELF_DECLARATION_MARKETING",
    "SELF_DECLARATION_NONE",
    "SELF_DECLARATION_OPINION",
    "SELF_DECLARATION_REPOST",
    "normalize_collection",
    "normalize_music",
    "resolve_self_declaration",
    "self_declaration_conflicts",
    "validate_scheduled_at",
]
