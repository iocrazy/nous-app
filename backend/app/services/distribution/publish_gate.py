"""提交那一刻的发布校验（图集设计 2026-08-11 §2 D3「校验前移」）。

为什么这道门在 router 而不是只在 workflow
==========================================
``SessionAdapter.validate_publish_intent`` 一直存在，但它只在 workflow 里跑
（``publish_distribution._publish_one_account_session``）。后果是：用户填完表单、
点了 Publish、拿到一个任务行，然后要等异步任务跑起来才看到"标题超长 / 图片
36 张 / 这个账号根本发不了图集"。**任务行已经建出来了**，记录页上多一条失败
批次，用户还得自己去理解那是提交时就能知道的事。

所以这道门前移到 ``distribution_router.create_task``：任一目标账号不过 →
422 + 类型化 reason，**一行都不建**。

前移是「加一道」不是「搬一道」
==============================
workflow 里那道门原样保留（``publish_distribution.py`` 的
``adapter.validate_publish_intent(intent)``），理由与 ``publish()`` 内部还要
再跑一次同一个校验一样：提交与执行之间隔着排队与调度（定时窗口会滑），而且
任何绕过 router 的调用方（retry 重投、将来的内部触发）都不该能把非法参数送
进浏览器。两处调的是**同一份实现**（``session_adapter.validate_intent_shape``），
不存在"前面放行、后面拒绝"的夹缝——把规则抄一份到 router 里才会有那个夹缝。

这里只判「纯形状」
==================
纯形状 = 只看请求本身说了什么：内容类型、标题、话题数、图片张数、可见性、
定时、平台选项。它们在提交那一刻全都已知，不需要 session_state、不需要素材
URL、不需要任何 IO。

**扩展名不在这里判** —— 提交时只有 ``resource_ids``，文件名要等 workflow 解析
出可服务 URL 才知道（``_media_filename``）。那一道仍然只在 workflow 里，这是
"前移纯形状部分"的边界，不是遗漏。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from app.services.distribution.publish_options import resolve_self_declaration
from app.services.distribution.session_adapter import (
    AUTH_TYPE_SESSION,
    REASON_PUBLISHING_NOT_IMPLEMENTED,
    SESSION_PLATFORM_PROFILES,
    validate_intent_shape,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.schemas.distribution_publish import PublishTaskCreate

#: 请求要走会话通道，但这个账号不是扫码绑定的。``decide_channel`` 会把它**降级**
#: 到 h5（见下面 ``resolves_to_session`` 的说明），于是一个图集批次会一半走浏览器
#: 一半走 H5 分享交接 —— 用户看到的是「一半 pending_share 一半别的」这种没人能
#: 解释的批次。图集在提交时就拒掉整批，让用户去掉那个账号再发。
REASON_ACCOUNT_NOT_SESSION_BOUND = "account_not_session_bound"


# ``REASON_PUBLISHING_NOT_IMPLEMENTED`` 从 ``session_adapter`` 原样 re-export：
# 小红书 / B 站能绑账号但发不了，走到 workflow 也必然是一行 failed，没有任何
# 不确定性 —— 提交时就说清楚。两处必须是**同一个字符串**，所以不另起一个。


@dataclass(frozen=True)
class GateProblem:
    """一条拒绝理由：机器可读的 ``reason`` + 给人看的 ``message`` + 哪个账号。

    ``account_id`` 是 ``None`` 时表示这条与具体账号无关（整批的形状问题）。
    带上它是因为混选场景里用户最需要知道的就是"是哪个账号让整批发不出去"。
    """

    reason: str
    message: str
    account_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "message": self.message,
            "account_id": self.account_id,
        }


def resolves_to_session(requested_channel: str, account: Mapping[str, Any]) -> bool:
    """这一行最终会不会真的走会话通道。

    是 ``publish_distribution.decide_channel`` 第一个分支的同义改写，**只读
    ``auth_type``**：router 手上的账号来自 ``get_public``（token 列被剥掉），
    所以 official 那个分支在这里判不了；而 session 那个分支只看 auth_type，
    判得准。

    不直接 import ``decide_channel`` 是为了不让 service 层反向依赖 workflow 层；
    两者不许漂移这件事**由测试守住**（``test_publish_gate.py`` 逐个组合比对
    本函数与 ``decide_channel`` 的结论），不是靠注释里的自觉。
    """
    return (
        requested_channel == "session" and account.get("auth_type") == AUTH_TYPE_SESSION
    )


def _effective_title(body: "PublishTaskCreate", account_id: str) -> str:
    """这个账号实际会用的标题：账号覆盖 → 批次默认。

    与 ``publish_distribution._account_publish_opts`` 同口径。校验批次标题而
    放过一个 2000 字的账号级覆盖，等于这道门在最容易出错的地方漏判。
    """
    cfg = body.account_configs.get(account_id)
    if cfg is not None and cfg.title:
        return cfg.title
    return body.title


def _effective_topics(body: "PublishTaskCreate", account_id: str) -> list[str]:
    cfg = body.account_configs.get(account_id)
    if cfg is not None and cfg.topics is not None:
        return list(cfg.topics)
    return list(body.topics or [])


def _platform_options(body: "PublishTaskCreate") -> dict[str, Any]:
    """与 ``publish_distribution._build_publish_intent`` **同一份**组装逻辑。

    尤其是 ``resolve_self_declaration``：``ai_content=True`` 会自动补上
    ``内容由AI生成``。若这里只读 ``self_declaration`` 列，一个 ai_content 批次
    在提交时看不到声明、到了 workflow 才带上它 —— 两道门看到的不是同一个请求，
    那就是最难查的一类不一致。
    """
    opts: dict[str, Any] = {}
    declaration = resolve_self_declaration(
        ai_content=bool(body.ai_content),
        self_declaration=body.self_declaration,
    )
    if declaration:
        opts["self_declaration"] = declaration
    collection = (body.collection_name or "").strip()
    if collection:
        opts["collection"] = collection
    music = (body.music_name or "").strip()
    if music:
        opts["music"] = music
    return opts


def publish_request_problems(
    body: "PublishTaskCreate",
    accounts: Sequence[Mapping[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> list[GateProblem]:
    """提交时能判定的全部拒绝理由（空 = 放行）。纯函数、无 IO、可单测。

    ``accounts`` 是 ``_authorize_account`` 返回的行，顺序与 ``body.account_ids``
    一致。每行至少要有 ``id`` / ``platform`` / ``auth_type``；缺 platform 的行
    （测试里的假账号）只会跳过与平台相关的那部分判定，不会误拒。

    ``now`` 只为测试注入定时窗口的参考时间。
    """
    problems: list[GateProblem] = []
    is_images = body.content_type == "images"
    image_count = len(body.resource_ids) if is_images else 0
    has_cover = bool(
        body.cover_vertical_resource_id or body.cover_horizontal_resource_id
    )
    options = _platform_options(body)

    for account in accounts:
        account_id = str(account.get("id"))
        session_bound = resolves_to_session(body.channel, account)

        if is_images and body.channel == "session" and not session_bound:
            # 混选拦截。只在图集上拒：视频的 session→h5 降级是既有的、被明确
            # 设计过的行为（``decide_channel`` 的 docstring），本次不改它。
            # 图集不一样 —— official/h5 通道的图集从未验证过（设计 §0 范围外），
            # 让它悄悄降级下去就是把未验证路径当成了默认。
            problems.append(
                GateProblem(
                    REASON_ACCOUNT_NOT_SESSION_BOUND,
                    "an image post needs an account connected by QR code "
                    "(this one would fall back to the H5 share handoff)",
                    account_id,
                )
            )
            continue

        if not session_bound:
            # 这一行走 official / h5。那两条通道的形状规则不在 profile 里
            # （profile 描述的是会话通道），所以这里不判 —— 不是漏判，是
            # 别拿一张画像去判它没画的东西。
            continue

        profile = SESSION_PLATFORM_PROFILES.get(str(account.get("platform") or ""))
        if profile is None:
            continue
        if not profile.supports_publishing:
            problems.append(
                GateProblem(
                    REASON_PUBLISHING_NOT_IMPLEMENTED,
                    f"publishing is not implemented for '{profile.platform}'; "
                    "the account can be bound and kept alive, but not published to",
                    account_id,
                )
            )
            continue

        problems.extend(
            GateProblem(sp.reason, sp.message, account_id)
            for sp in validate_intent_shape(
                profile,
                content_type=body.content_type,
                title=_effective_title(body, account_id),
                topics=_effective_topics(body, account_id),
                image_count=image_count,
                visibility=body.visibility,
                scheduled_at=body.scheduled_at,
                has_cover=has_cover,
                platform_options=options,
                now=now,
            )
        )
    return problems


__all__ = [
    "REASON_ACCOUNT_NOT_SESSION_BOUND",
    "REASON_PUBLISHING_NOT_IMPLEMENTED",
    "GateProblem",
    "publish_request_problems",
    "resolves_to_session",
]
