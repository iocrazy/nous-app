"""SessionAdapter —— 会话通道 (spec 2026-08-04) 的 backend 侧适配器。

与 ``DouyinAdapter``(OAuth) **并列**，不替换：``official``/``h5`` 走官方
开放平台，``session`` 走"平台 web 会话 + nous-browser 容器"。

为什么不继承 ``PlatformAdapter``
================================
``PlatformAdapter`` 的抽象方法全是 OAuth 形状（``get_auth_url`` /
``exchange_token`` / ``refresh_token`` / ``get_user_info(access_token,
open_id)``）。会话通道一个都没有：它没有 authorize URL，没有 code
换 token，凭证是扫码得来的 storage_state。硬套会得到一排
``NotImplementedError``，把"这两条通道形状不同"这个事实藏起来。两者的
共同点是"能发布"，而不是"能 OAuth"，所以它们是**兄弟**而非父子；
``registry.resolve_adapter()`` 负责按 ``auth_type`` 把调用方分流。

为什么没有 ``DouyinSessionAdapter``
===================================
spec §6.1 三条硬要求的直接后果。第一个平台约 80% 的工作量是平台无关的
基建，而**平台差异几乎全部落在浏览器容器里**（DOM 选择器 / 签名函数 /
外包 CLI）。backend 这一侧的工作只有：解密 → 组装平台无关的意图 → 内网
HTTP → 结果映射，逐字相同。因此这里是**一个泛型 ``SessionAdapter(platform)``**，
不存在按平台分的子类：

a) 返回契约用 ``SessionStatus``（通道级枚举），不出现平台专属状态值；
b) 平台无关逻辑（加密存储、素材传递、结果映射、fail-fast）全在本模块，
   没有一行落在 ``douyin_*`` 命名的地方；
c) **给「档位 2」留位置**：本模块从不描述"怎么发"，只描述"发什么"
   —— 见 ``PublishIntent`` 与 ``publish()`` 的注释。

给「档位 2」留的三个位置 (spec §1.3 / §6.1 c)
=============================================
小红书那一档是"浏览器只当签名机（``window._webmsxyw`` 算 ``x-s``/``x-t``）
+ 上传走裸 HTTP"，与抖音的纯 DOM 木偶戏完全不同。若抽象假设"发布 == DOM
操作序列"，接它就要返工基建。所以：

1. **意图而非机制**。``PublishIntent`` 描述的是"发什么"（素材、标题、
   话题、可见性），没有任何"点哪个按钮 / 等哪个选择器"。执行档位是浏览器
   侧按 ``platform`` 选的策略，backend 不知道也不需要知道。
2. **会话物料是不透明的**。``storage_state`` 全程当作黑盒 JSON 对象透传，
   本模块**不检查内部结构**。小红书的会话主要在 localStorage/origins 而
   非 cookies，任何 ``storage_state["cookies"]`` 断言都会挡死它。
3. **素材以 URL 交付，不以"上传动作"交付**。``PublishMedia.url`` 是
   presigned URL：DOM 档需要它下载到 /tmp 再 ``set_input_files``，裸 HTTP
   档直接拿它做流式 PUT，CLI 档（B 站 biliup）拿它当参数。同一个字段服务
   三档，没有一档被特殊照顾。

另外 ``PublishOutcome.updated_storage_state`` 是所有档位共有的（平台会话
滑动续期，spec §4.2 第 6 步），所以它在信封里而不是某一档的副作用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional

from loguru import logger

from app.services.distribution.browser_client import (
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE_ID,
    BrowserClient,
    SessionEnvironment,
    SessionErrorKind,
    SessionOpResult,
    SessionStatus,
    VerifyResult,
    is_infra_failure,
)
from app.services.distribution.publish_options import (
    DOUYIN_SELF_DECLARATIONS,
    MAX_COLLECTION_NAME_LEN,
    MAX_MUSIC_NAME_LEN,
    SCHEDULE_MAX_AHEAD,
    SCHEDULE_MIN_LEAD,
    validate_scheduled_at,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    # 只在类型检查时导入。运行时的导入在 ``project_capability`` 内部，这样
    # service 层不在 import 期依赖 schema 层，layering 与既有模块保持一致。
    from app.schemas.distribution import PlatformCapability

AUTH_TYPE_SESSION = "session"

# 业务原因（非基建失败）—— 走 detail["reason"]，与 detail["error_kind"] 分开。
# 两者混用会让巡检把"容器挂了"误判成"账号掉线"，见 browser_client 的说明。
REASON_NO_SESSION_STATE = "no_session_state"
REASON_MALFORMED_SESSION_STATE = "malformed_session_state"
REASON_AUTH_TYPE_MISMATCH = "auth_type_mismatch"
# fail-fast 拦下的参数错误（§7.7）。是业务原因而非基建失败 —— 但它**不该**
# 让账号进 needs_relogin：会话好得很，是这一次的入参不合法。调用方按
# status=failed 处理（见 publish() 的说明）。
REASON_INVALID_INTENT = "invalid_publish_intent"
# 该平台只做到"绑定账号 + 会话保活",发布未实现。与上面几个的区别:会话是
# 好的、账号是活的 —— 所以**绝不能**让它把账号标成 needs_relogin。
REASON_PUBLISHING_NOT_IMPLEMENTED = "publishing_not_implemented"


class SessionStateError(RuntimeError):
    """明文 ``session_state`` 解析不出 storage_state。

    只携带 ``reason``（业务原因，账号该重扫码）—— 解密失败**不走这里**，
    它发生在 repository，由 ``decrypt_failure_result()`` 转成基建失败。
    两者严格分开正是为了不让"读不出来"被误判成"账号掉线"。
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# ── 发布意图（平台无关、执行档位无关） ──────────────────────────


@dataclass(frozen=True)
class PublishMedia:
    """一个待发素材。

    ``url`` 是浏览器容器可直接取到的 presigned URL —— 容器**不挂载任何
    存储卷**（spec §4.2 第 4 步），素材只经 HTTP 流转。
    """

    kind: str  # "video" | "image"
    url: str
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "url": self.url,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class PublishIntent:
    """一次发布要表达的全部内容 —— **只说发什么，不说怎么发**。

    ``visibility`` 刻意用语义词（``public``/``private``/``friends``）而不是
    抖音的 ``private_status`` 整数枚举：那个 0/1/2 是平台私有的，而且抖音
    自己的官方 create API 与 H5 schema 就有两套不同的 ``download_type``
    映射（见 ``douyin_adapter``）。把 int 枚举放进通道契约，等于把抖音焊死
    进基建（违反 §6.1 a/b）。翻译成平台原生值是浏览器侧 uploader 的职责。

    ``platform_options`` 是逃生舱：平台独有且无法通用化的字段（图文笔记
    类型、封面选择策略、合集 id 等）放这里，键名由各平台 uploader 自定，
    backend 只透传不解释。目前抖音用两个键（跨服务契约，browser 侧按同名取值）：

    - ``self_declaration``：「自主声明」下拉的**原文**（六个之一，见
      ``publish_options.SELF_DECLARATIONS``）。缺省/None = 不碰那个控件。
    - ``collection``：「合集」名称，按名匹配账号已有的合集；缺省 = 不选。
    - ``music``：「选择音乐」的曲名。浏览器在发布页的音乐弹窗里搜这个名字并
      选中；缺省 = 不碰那个控件（平台默认原声）。

    ``music`` 刻意留在 platform_options 而不是升成通道级字段：按名字在平台
    自己的曲库里搜一首歌是抖音发布页的具体操作，只有这一个平台实现了它。
    升上去等于宣称这是通道级能力，而且会绕过下面 ``supports_music`` 那道
    ——「这个平台没有音乐选择器」就会从"类型化拒绝"退化成"字段被默默丢掉"。

    "只透传不解释"指的是**语义**：backend 不知道这两个键会被点在页面的哪里。
    但**取值合法性**仍然要在起浏览器前拦（§7.7），因为一次浏览器 + 一次上传
    的代价是分钟级 —— 这个校验按 ``PlatformSessionProfile`` 的数据表驱动，
    不写成 if platform == 'douyin' 的分支。

    ``scheduled_at`` 是通道级字段（不进 platform_options）：定时发布不是抖音
    独有的概念，各平台都有，差别只在窗口大小 —— 那个差别正是 profile 里的
    ``schedule_min_lead`` / ``schedule_max_ahead`` 两个数。
    """

    content_type: str  # "video" | "images"
    media: tuple[PublishMedia, ...]
    title: str
    description: Optional[str] = None
    topics: tuple[str, ...] = ()
    visibility: str = "public"
    allow_download: bool = True
    cover: Optional[PublishMedia] = None
    scheduled_at: Optional[datetime] = None
    platform_options: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "content_type": self.content_type,
            "media": [m.to_payload() for m in self.media],
            "title": self.title,
            # 空描述发 ``""`` 而不是 ``null``：浏览器侧把它声明为
            # ``description: str = ""``（非 Optional），送 null 会被 pydantic
            # 拒成 422 —— 而 422 回的是 FastAPI 的 detail 形状而非 SessionResult，
            # 于是"没写简介"这种最常见的批次会被记成基建失败。两端语义等价
            # （空串即没有简介），所以在发出前抹平，而不是让契约留一个只在
            # 少数字段上成立的可空性。
            "description": self.description or "",
            "topics": list(self.topics),
            "visibility": self.visibility,
            "allow_download": self.allow_download,
            "cover": self.cover.to_payload() if self.cover else None,
            "scheduled_at": (
                self.scheduled_at.isoformat() if self.scheduled_at else None
            ),
            "platform_options": dict(self.platform_options),
        }


@dataclass(frozen=True)
class PublishOutcome:
    """S3 发布的返回信封 —— ``SessionOpResult`` 加两组发布专属字段。

    ``updated_storage_state`` 是会话寿命的决定因素（spec §4.2 第 6 步）：
    平台会话滑动续期，每次使用后服务端下发新 cookie，不回写等于一直在消耗
    初始那份的剩余寿命。它属于**所有**执行档位，所以在信封里。
    """

    result: SessionOpResult
    platform_item_id: Optional[str] = None
    published_url: Optional[str] = None
    updated_storage_state: Optional[Mapping[str, Any]] = None


# ── 每平台的 fail-fast 画像（数据，不是代码分支） ─────────────────


@dataclass(frozen=True)
class PlatformSessionProfile:
    """会话通道支持哪些平台、各自的 fail-fast 约束 (spec §7.7)。

    参数校验必须在**起浏览器之前**完成：开一次有头浏览器 + 传一个几百 MB
    的视频要几十秒到几分钟，标题超长这种错误不该等到那时才发现。

    数值上界故意留 ``None`` = 不设限：S1 阶段没有实测过抖音创作页的真实
    上限，凭空写一个数字会静默拒掉合法内容（比放行更糟）。S3 接 DOM 时
    照实填。**只校验确定的事**是这里的口径。
    """

    platform: str
    # 这个平台**怎么登录**：``"qrcode"``（渲染二维码，用户拿 app 扫）或
    # ``"sms"``（没有码可扫，用户填手机号 + 平台短信下发的验证码）。
    #
    # 与下面所有字段不同，它跟"能不能发布"无关，所以 ``supports_publishing=False``
    # 的平台**也必须填准** —— 绑定恰恰是这些平台唯一做得到的事。小红书就是这个
    # 位置的由来：它的创作平台根本没有扫码登录（[实测 2026-08-08] 含
    # 扫码/二维码/QR 的可点元素 0 个），而 UI 照着抖音的形状给它画了一个二维码
    # 占位框 —— 用户永远等不到那张图，因为后端从来就不会产出它。
    #
    # 唯一真相在 ``browser/app/capabilities.py::PLATFORM_LOGIN_METHODS``（那边
    # 又被测试钉在各平台真实的 ``LoginFlowSpec`` 上）。本字段是它在后端的镜像，
    # ``backend/tests/test_capability_matches_browser.py`` 断言两者逐平台相等。
    login_method: str
    content_types: frozenset[str]
    video_extensions: frozenset[str]
    image_extensions: frozenset[str]
    max_title_len: Optional[int] = None
    max_topics: Optional[int] = None
    max_images: Optional[int] = None
    # 图集张数**下界**。None = 没实测过 → 不设限（同 max_images 的口径：
    # 凭空写个数字会静默拒掉合法内容）。加在这里而不是等 T4 才加，是因为
    # ``/capabilities`` 端点是这张画像的**投影**，投影不该手写画像里没有的
    # 字段 —— 那就又是一份要人工同步的声明。实测值由 T0 的 V2 填入。
    min_images: Optional[int] = None
    # 定时窗口。两个都是 None = 该平台还没接定时 → 带 scheduled_at 的意图直接
    # 拒（而不是静默立即发出去：早发十二小时不比不发轻）。
    schedule_min_lead: Optional[timedelta] = None
    schedule_max_ahead: Optional[timedelta] = None
    # 该平台「自主声明」的合法取值（原文）。空 = 平台没有这个概念 →
    # 带 self_declaration 的意图会被拒，而不是被浏览器侧静默丢掉。
    self_declarations: frozenset[str] = frozenset()
    supports_collection: bool = False
    # 能不能在发布页上挑配乐。默认 False，与 supports_collection 同口径：
    # 缺失往安全方向塌陷 —— 漏填只会让带 music 的意图被类型化拒绝（可见、
    # 可查），填成 True 却没实现，才会让请求一路走到浏览器上瞎试。
    supports_music: bool = False
    # 能不能**发布**。默认 False —— 绑定账号和发布是两件事,新平台通常先有
    # 前者:浏览器侧的会话校验 + 扫码登录是通用机制,而发布流程是一整套只能
    # 对着真实页面写的 DOM 操作。
    #
    # 默认 False 是刻意的:漏填只会让发布被类型化拒绝(可见、可查),填成
    # True 却没实现,才会让请求一路走到浏览器上瞎试。**缺失要往安全的方向
    # 塌陷。**
    supports_publishing: bool = False


SESSION_PLATFORM_PROFILES: dict[str, PlatformSessionProfile] = {
    "douyin": PlatformSessionProfile(
        platform="douyin",
        # 扫码：登录页渲染二维码，用户拿抖音 app 扫。
        login_method="qrcode",
        # video + images —— 这是对浏览器侧实现的忠实记录，不是产品决定。
        # 唯一真相是 ``browser/app/capabilities.py`` 的
        # ``PLATFORM_CONTENT_TYPES["douyin"]``，本字段必须是它的子集。
        #
        # ``"images"`` 是 spec T7 加回来的，在 ``douyin_publish.py::_drive_images``
        # （T3）真的能驱动图集之后。**这一行曾经骗过人**：早先它写着
        # ``{"video", "images"}`` 而浏览器侧只认 video，于是三层声明打架 ——
        # 前端能选、后端放行、浏览器拒，用户填完整个表单、提交、排队，直到最后
        # 一步才拿到 ``unsupported_content_type``。**宣称一个不存在的能力比不
        # 宣称糟得多** —— 拒绝本身是对的，晚了才是缺陷。
        #
        # **这条已经不靠自觉了** —— ``backend/tests/test_capability_matches_browser.py``
        # 断言本字段 ⊆ browser 的 ``PLATFORM_CONTENT_TYPES``，只改这一处会红 CI。
        content_types=frozenset({"video", "images"}),
        video_extensions=frozenset({".mp4", ".mov", ".webm"}),
        # 保持原样：这是**逐个 media 条目**的扩展名白名单（``_extension_problems``
        # 按 ``kind`` 选表），跟"能不能发图集"是两件事，删掉只会让 P2-1 步骤 2
        # 多一次考古。
        image_extensions=frozenset({".jpg", ".jpeg", ".png"}),
        # [实测 2026-08-11]（图集设计 §3.4 V2，勘探端点在真实图文发布页上读到
        # 的原文）：`最多支持上传35张图片，图片格式不支持gif格式`（exact=1），
        # 且只传 1 张时编辑器完整渲染、页面上没有任何"至少 N 张"的提示
        # （`至少` exact=0）。所以上下界都是**测出来的**，不是沿用的猜测。
        #
        # ⚠️ 与 ``distribution_publish.MAX_IMAGES``（同样是 35）的分工：那个是
        # 与平台无关的中立硬顶（防御性，任何 content_type=images 的请求都过它），
        # 这里才是"抖音的上限"。数字碰巧相同不代表可以合并——换个平台就分开了。
        max_images=35,
        min_images=1,
        # 下面三项同样是 2026-08-06 在真实发布页上探过的，属于"确定的事"：
        # 定时只接受 2 小时后 ~ 14 天内；自主声明是六选一的固定下拉；有合集。
        schedule_min_lead=SCHEDULE_MIN_LEAD,
        schedule_max_ahead=SCHEDULE_MAX_AHEAD,
        self_declarations=DOUYIN_SELF_DECLARATIONS,
        supports_collection=True,
        # [实测 2026-08-12, T0] 发布页上有「选择音乐」区块 + 弹窗（搜索框
        # 「搜索音乐」+ 推荐/热门榜/… 分页 + 结果列表）。不填时预览显示
        # 「…创作的原声」，即平台默认，不阻塞发布。
        supports_music=True,
        supports_publishing=True,
    ),
    # 小红书 / B 站：**只做到账号绑定 + 会话保活**，发布未实现。
    #
    # 浏览器侧同样只注册了 validator 和 login flow，没有 publisher（见
    # browser/app/platforms/__init__.py 末尾的说明）。两侧一致地把"能绑账号"
    # 和"能发布"分开，所以发布请求在两个层面都会被类型化拒绝，不可能走到
    # 浏览器上照着未写完的流程瞎试。
    #
    # 下面这些 content_types / 扩展名是**占位**，等真正实现发布时要对着平台
    # 实测填准；在 supports_publishing=False 的前提下它们不会被用到。
    "xiaohongshu": PlatformSessionProfile(
        platform="xiaohongshu",
        # ⚠️ **不是**扫码。创作平台只有「手机号 + 验证码」（[实测 2026-08-08]：
        # 大图 0 个、canvas 0 个、含扫码/二维码/QR 的可点元素 0 个）。这一行是
        # 前端画哪种登录界面的唯一依据 —— 在它存在之前，UI 一律照抖音画二维码，
        # 于是小红书那条绑定链一次都没跑通过。
        login_method="sms",
        content_types=frozenset({"video", "images"}),
        video_extensions=frozenset({".mp4", ".mov"}),
        image_extensions=frozenset({".jpg", ".jpeg", ".png", ".webp"}),
        supports_publishing=False,
    ),
    "bilibili": PlatformSessionProfile(
        platform="bilibili",
        login_method="qrcode",
        content_types=frozenset({"video"}),
        video_extensions=frozenset({".mp4", ".mov", ".flv"}),
        image_extensions=frozenset({".jpg", ".jpeg", ".png"}),
        supports_publishing=False,
    ),
}


def supported_session_platforms() -> frozenset[str]:
    """能通过会话通道**绑定账号**的平台。

    注意这是"能绑账号",不是"能发布" —— 见 ``publishable_session_platforms``。
    绑定端点用这个,因为扫码登录 + 会话校验是平台无关的通用机制,新平台先
    有它是常态。
    """
    return frozenset(SESSION_PLATFORM_PROFILES)


def publishable_session_platforms() -> frozenset[str]:
    """能通过会话通道**发布**的平台 —— 是上面那个集合的子集。

    分成两个函数而不是一个,是因为把"能绑账号"当成"能发布"会让一个只做了
    账号绑定的平台把发布请求一路放到浏览器上,照着根本没写的流程瞎点。
    浏览器侧有对称的保护(只注册 validator/login,不注册 publisher),这里
    是同一件事在 backend 侧的表达 —— 两层都拦,因为这条路径错一次的代价是
    往用户的真实账号上发出错东西。
    """
    return frozenset(
        name
        for name, profile in SESSION_PLATFORM_PROFILES.items()
        if profile.supports_publishing
    )


# ── 纯形状校验（图集设计 §2 D3） ──────────────────────────────
#
# 「纯形状」= 只看请求本身说了什么，不需要 session_state、不需要素材 URL、
# 不需要任何 IO。这些规则**在提交那一刻就能判**，所以它们前移到
# ``distribution_router.create_task``（D3 的关键改动：用户点 Publish 就
# 拿到红字，而不是等异步任务跑到一半才看到一批 failed 行）。
#
# 前移是**加一道**不是搬一道：``SessionAdapter.validate_publish_intent``
# 仍然调同一个函数（下面），workflow 里那道门原样保留。两处调的是同一份
# 实现，所以不存在"前面放行、后面拒绝"的夹缝 —— 那正是把规则复制一份到
# router 里会犯的错。


@dataclass(frozen=True)
class ShapeProblem:
    """一条形状问题：机器可读的 ``reason`` + 给人看的 ``message``。

    两个字段都要，缺一不可：只有 message 的话调用方要正则匹配英文句子才能
    分支（前端更没法 i18n）；只有 reason 的话具体数字（35 / 实际张数）就丢了。
    """

    reason: str
    message: str


# ShapeProblem.reason 的取值。前端/测试按这些常量分支，别按 message 文本。
SHAPE_UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
SHAPE_TITLE_EMPTY = "title_empty"
SHAPE_TITLE_TOO_LONG = "title_too_long"
SHAPE_TOO_MANY_TOPICS = "too_many_topics"
SHAPE_TOO_MANY_IMAGES = "too_many_images"
SHAPE_TOO_FEW_IMAGES = "too_few_images"
SHAPE_UNKNOWN_VISIBILITY = "unknown_visibility"
SHAPE_INVALID_SCHEDULE = "invalid_schedule"
SHAPE_SCHEDULE_UNSUPPORTED = "scheduling_not_supported"
SHAPE_DECLARATION_UNSUPPORTED = "self_declaration_not_supported"
SHAPE_DECLARATION_UNKNOWN = "unknown_self_declaration"
SHAPE_COLLECTION_UNSUPPORTED = "collections_not_supported"
SHAPE_COLLECTION_INVALID = "invalid_collection_name"
SHAPE_MUSIC_UNSUPPORTED = "music_not_supported"
SHAPE_MUSIC_INVALID = "invalid_music_name"
# D4：图集不接受独立封面。抖音图文页确实有「封面设置」，但 [实测 2026-08-11]
# （§3.4 V8）它是**从已上传的图片里挑一张**，不是视频那种在弹窗里独立上传的
# 第五个文件。所以带 cover 素材的图集请求是语义错误，不是可以忽略的多余字段。
SHAPE_COVER_NOT_SUPPORTED_FOR_IMAGES = "cover_not_supported_for_images"


def validate_intent_shape(
    profile: PlatformSessionProfile,
    *,
    content_type: str,
    title: str,
    topics: Iterable[str] = (),
    image_count: int = 0,
    visibility: str = "public",
    scheduled_at: Optional[datetime] = None,
    has_cover: bool = False,
    platform_options: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> list[ShapeProblem]:
    """一次发布请求的**纯形状**问题清单（空 = 通过）。纯函数、无 IO。

    参数是原始值而不是 ``PublishIntent``，因为两个调用点手上的东西不同：
    router 只有请求体（素材还没解析成 URL），workflow 有组装好的 intent。
    强行让 router 先造一个假 intent，就得为"素材 URL 还不知道"编造占位值 ——
    那是把校验绑死在一个它并不需要的数据结构上。

    ``now`` 只为测试注入。定时窗口在这里算的这一次与请求 schema 那一次不是
    冗余：两次之间隔着排队与调度（见 ``validate_scheduled_at`` 的说明）。
    """
    problems: list[ShapeProblem] = []
    topics = list(topics)
    if content_type not in profile.content_types:
        problems.append(
            ShapeProblem(
                SHAPE_UNSUPPORTED_CONTENT_TYPE,
                f"content_type {content_type!r} not supported on "
                f"{profile.platform} session channel",
            )
        )
    if not (title or "").strip():
        problems.append(ShapeProblem(SHAPE_TITLE_EMPTY, "title is empty"))
    if profile.max_title_len is not None and len(title) > profile.max_title_len:
        problems.append(
            ShapeProblem(
                SHAPE_TITLE_TOO_LONG,
                f"title exceeds {profile.max_title_len} characters ({len(title)})",
            )
        )
    if profile.max_topics is not None and len(topics) > profile.max_topics:
        problems.append(
            ShapeProblem(
                SHAPE_TOO_MANY_TOPICS,
                f"too many topics ({len(topics)} > {profile.max_topics})",
            )
        )
    if profile.max_images is not None and image_count > profile.max_images:
        problems.append(
            ShapeProblem(
                SHAPE_TOO_MANY_IMAGES,
                f"too many images ({image_count} > {profile.max_images})",
            )
        )
    # 下界只在**真的是图集**时判：视频批次的 image_count 恒为 0，拿 min_images
    # 去卡它会把每一条视频都拒掉。
    if (
        content_type == "images"
        and profile.min_images is not None
        and image_count < profile.min_images
    ):
        problems.append(
            ShapeProblem(
                SHAPE_TOO_FEW_IMAGES,
                f"too few images ({image_count} < {profile.min_images})",
            )
        )
    if content_type == "images" and has_cover:
        problems.append(
            ShapeProblem(
                SHAPE_COVER_NOT_SUPPORTED_FOR_IMAGES,
                "an image post takes its cover from the uploaded images; "
                "a separate cover asset is not supported",
            )
        )
    if visibility not in ("public", "private", "friends"):
        problems.append(
            ShapeProblem(SHAPE_UNKNOWN_VISIBILITY, f"unknown visibility {visibility!r}")
        )
    problems.extend(_schedule_shape_problems(profile, scheduled_at, now=now))
    problems.extend(_option_shape_problems(profile, platform_options or {}))
    return problems


def _schedule_shape_problems(
    profile: PlatformSessionProfile,
    scheduled_at: Optional[datetime],
    *,
    now: Optional[datetime],
) -> list[ShapeProblem]:
    """定时时间的窗口校验。平台没有定时能力时，带了时间直接拒。"""
    if scheduled_at is None:
        return []
    if profile.schedule_min_lead is None and profile.schedule_max_ahead is None:
        return [
            ShapeProblem(
                SHAPE_SCHEDULE_UNSUPPORTED,
                f"scheduled publishing is not supported on {profile.platform}",
            )
        ]
    problem = validate_scheduled_at(
        scheduled_at,
        now=now,
        min_lead=profile.schedule_min_lead or timedelta(0),
        max_ahead=profile.schedule_max_ahead or timedelta.max,
    )
    return [ShapeProblem(SHAPE_INVALID_SCHEDULE, problem)] if problem else []


def _option_shape_problems(
    profile: PlatformSessionProfile, opts: Mapping[str, Any]
) -> list[ShapeProblem]:
    """``platform_options`` 里我们**认识**的键的取值校验。

    只校验认识的键：``platform_options`` 是逃生舱，未知键照旧原样透传
    （否则每加一个平台专属字段都要先改 backend，逃生舱就不成其为逃生舱）。
    但认识的键必须拦 —— 一个拼错的自主声明送到浏览器侧，最好的结果是发布
    失败，最坏的结果是选项没选中而作品照发（合规字段静默丢失）。
    """
    problems: list[ShapeProblem] = []

    declaration = opts.get("self_declaration")
    if declaration is not None:
        if not profile.self_declarations:
            problems.append(
                ShapeProblem(
                    SHAPE_DECLARATION_UNSUPPORTED,
                    f"self declaration is not supported on {profile.platform}",
                )
            )
        elif declaration not in profile.self_declarations:
            problems.append(
                ShapeProblem(
                    SHAPE_DECLARATION_UNKNOWN,
                    f"unknown self declaration {declaration!r}",
                )
            )

    collection = opts.get("collection")
    if collection is not None:
        if not profile.supports_collection:
            problems.append(
                ShapeProblem(
                    SHAPE_COLLECTION_UNSUPPORTED,
                    f"collections are not supported on {profile.platform}",
                )
            )
        elif not isinstance(collection, str) or not collection.strip():
            problems.append(
                ShapeProblem(SHAPE_COLLECTION_INVALID, "collection name is empty")
            )
        elif len(collection) > MAX_COLLECTION_NAME_LEN:
            problems.append(
                ShapeProblem(
                    SHAPE_COLLECTION_INVALID,
                    f"collection name exceeds {MAX_COLLECTION_NAME_LEN} characters",
                )
            )

    # 配乐。同样只校验"形状"——曲名存不存在是平台搜索结果说了算，backend 无从
    # 判断。但平台有没有这个控件是我们知道的事，不拦就会变成：用户填了曲名、
    # 批次照发、作品静默走平台默认音乐。
    music = opts.get("music")
    if music is not None:
        if not profile.supports_music:
            problems.append(
                ShapeProblem(
                    SHAPE_MUSIC_UNSUPPORTED,
                    f"music selection is not supported on {profile.platform}",
                )
            )
        elif not isinstance(music, str) or not music.strip():
            problems.append(ShapeProblem(SHAPE_MUSIC_INVALID, "music name is empty"))
        elif len(music) > MAX_MUSIC_NAME_LEN:
            problems.append(
                ShapeProblem(
                    SHAPE_MUSIC_INVALID,
                    f"music name exceeds {MAX_MUSIC_NAME_LEN} characters",
                )
            )
    return problems


# ── 能力下发（图集设计 §2 D1） ────────────────────────────────


def _seconds(delta: Optional[timedelta]) -> Optional[int]:
    return None if delta is None else int(delta.total_seconds())


def project_capability(profile: PlatformSessionProfile) -> "PlatformCapability":
    """把一张 fail-fast 画像投影成前端能读的能力声明。

    **投影，不是第二份声明** —— 每个字段都从 ``profile`` 读，没有一个是在这里
    手写的常量。上一版把同一件事写在前端一张表里，靠"必须与后端同一个 PR 落地"
    的注释维持同步；那条纪律在它要防的第一次事故里就没拦住。

    两条口径写死在这里：

    * **集合一律 ``sorted()``**。源头是 frozenset，迭代顺序随进程 hash 种子变，
      不排序等于每次重启都换一份响应体。
    * **``supports_publishing=False`` 的平台，能力字段全部清空并标
      ``is_placeholder``**。这些平台的 profile 里写的是占位值（原注释：「等真正
      实现发布时要对着平台实测填准」），原样下发就是把猜测升格成看起来权威的
      API 响应 —— 那正是本次要消灭的病，换个地方犯不算修。
    """
    from app.schemas.distribution import PlatformCapability

    if not profile.supports_publishing:
        return PlatformCapability(
            platform=profile.platform,
            # ⚠️ 这一档也必须带 ``login_method``。它不是发布能力，是**绑定**
            # 能力，而绑定恰恰是这些平台唯一做得到的事 —— 把它跟着占位值一起
            # 清空，等于让唯一需要它的平台（小红书）拿不到它，前端只好退回
            # 猜测，也就是退回二维码。
            login_method=profile.login_method,
            supports_publishing=False,
            is_placeholder=True,
        )

    return PlatformCapability(
        platform=profile.platform,
        login_method=profile.login_method,
        supports_publishing=True,
        is_placeholder=False,
        content_types=sorted(profile.content_types),
        video_extensions=sorted(profile.video_extensions),
        image_extensions=sorted(profile.image_extensions),
        min_images=profile.min_images,
        max_images=profile.max_images,
        max_title_len=profile.max_title_len,
        max_topics=profile.max_topics,
        # 「有没有定时能力」在 profile 里是"两个窗口至少有一个不是 None"，
        # 与 ``_schedule_problems`` 的判定同源。前端不该重新推导这条规则。
        supports_scheduling=(
            profile.schedule_min_lead is not None
            or profile.schedule_max_ahead is not None
        ),
        schedule_min_lead_seconds=_seconds(profile.schedule_min_lead),
        schedule_max_ahead_seconds=_seconds(profile.schedule_max_ahead),
        self_declarations=sorted(profile.self_declarations),
        supports_collection=profile.supports_collection,
        supports_music=profile.supports_music,
    )


def platform_capabilities() -> dict[str, "PlatformCapability"]:
    """所有会话通道平台的能力声明，按平台名索引。

    包含 ``supports_publishing=False`` 的平台（置空 + ``is_placeholder``）而
    不是把它们过滤掉：前端要能区分「这个平台绑得上但发不了」和「这个平台不
    存在」，后者应该是 UI 里根本不出现的账号类型。
    """
    return {
        name: project_capability(profile)
        for name, profile in SESSION_PLATFORM_PROFILES.items()
    }


# ── 环境组装 ──────────────────────────────────────────────


def build_environment(row: Optional[Mapping[str, Any]]) -> SessionEnvironment:
    """把一行 ``account_environments``（spec §3.2）变成 ``SessionEnvironment``。

    ``row`` 来自 repository 对 ``social_accounts`` 的 LEFT JOIN，列名即
    §3.2 的建表列：``proxy_url`` / ``user_agent`` / ``locale`` /
    ``timezone_id`` / ``geo_lat`` / ``geo_lng``。

    解密职责划分（两处都有明确归属，不重叠）：

    - ``session_state`` 归 **repository** 解（与 ``access_token`` 同一条
      secret 边界），本模块只 ``json.loads`` —— 见 ``parse_session_state``。
    - ``proxy_url`` 归 **本函数** 解。它属于环境配置而非账号凭证，
      repository 保持密文原样返回，密钥仍然不出 backend（浏览器服务拿到
      的是明文代理串，但从不接触 Fernet 密钥）。

    解不开时降级为直连而不是整个失败：代理配错不该让一个健康账号连校验
    都做不了。降级会 warn，且**绝不打印密文/明文本身**。

    ``row`` 为 None（账号还没配环境）时返回全默认：直连 + zh-CN +
    Asia/Shanghai。S3 之前本来就不接代理 (spec §6)。
    """
    from app.core import secret_box

    if not row:
        return SessionEnvironment()
    proxy = row.get("proxy_url")
    if proxy:
        try:
            proxy = secret_box.decrypt(proxy)
        except Exception:
            # 只报"哪个账号的代理解不开"，绝不打印密文/明文本身。
            logger.warning(
                f"[session.env] account={row.get('account_id')} "
                "proxy_url decrypt failed — falling back to direct connection"
            )
            proxy = None
    lat = row.get("geo_lat")
    lng = row.get("geo_lng")
    # mig 424 —— 半套 viewport 一律当没有：只有宽或只有高无法交给
    # ``new_context(viewport=...)``，静默传半套的后果是浏览器侧忽略它，而库里
    # 那行看起来是"配过的"。DB 有 CHECK 挡，这里是第二道（读侧也要说得清）。
    vw = row.get("viewport_width")
    vh = row.get("viewport_height")
    if vw is None or vh is None:
        vw = vh = None
    return SessionEnvironment(
        proxy_url=proxy or None,
        user_agent=row.get("user_agent") or None,
        locale=row.get("locale") or DEFAULT_LOCALE,
        timezone_id=row.get("timezone_id") or DEFAULT_TIMEZONE_ID,
        geo_lat=float(lat) if lat is not None else None,
        geo_lng=float(lng) if lng is not None else None,
        viewport_width=int(vw) if vw is not None else None,
        viewport_height=int(vh) if vh is not None else None,
    )


def parse_session_state(account: Mapping[str, Any]) -> dict[str, Any]:
    """``account["session_state"]``（**已解密的明文 JSON 字符串**）→ dict。

    入参契约
    ========
    ``session_state`` 到这里时**必须已经是明文** —— 解密是 repository 的
    责任（``SocialAccountsRepository.get_with_session`` 的
    ``_decrypt_secret_cols``，与 ``access_token`` 同一条边界）。本函数只做
    ``json.loads`` + 结构校验，**不调 ``secret_box``**。

    为什么不在这里兜一层解密：``secret_box.decrypt`` 对不以 ``gAAAAA``
    开头的输入原样返回，那是 legacy 明文兼容分支，其注释明写 "Once all
    rows are encrypted, this branch never fires" —— 设计者认为它迟早被
    删。真在这里兜一层，删除那天会话通道会突然全线崩溃，而报错点在这里，
    排查要绕一圈。解密只在一处发生。

    两种失败（都是业务原因，账号该重扫码）：

    - 没有 session_state → ``no_session_state``：账号没绑过会话。
    - 不是非空 JSON 对象 → ``malformed_session_state``：数据坏了。

    解密失败（Fernet 密钥错配）不在本函数的责任范围 —— 它发生在
    repository，调用方捕获后映射成 ``decrypt_failure_result()``，见该函数。

    对 storage_state 的**内部结构零校验**（只要求是非空对象）—— 见模块
    docstring「给档位 2 留的位置」第 2 条。
    """
    raw = account.get("session_state")
    if not raw:
        raise SessionStateError(
            "account has no session_state", reason=REASON_NO_SESSION_STATE
        )
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as exc:
        raise SessionStateError(
            "session_state is not valid JSON", reason=REASON_MALFORMED_SESSION_STATE
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise SessionStateError(
            "session_state must be a non-empty JSON object",
            reason=REASON_MALFORMED_SESSION_STATE,
        )
    return parsed


def decrypt_failure_result(
    message: str, *, account_id: Optional[Any] = None
) -> dict[str, Any]:
    """repository 层 ``session_state`` 解密失败 → §7.8 信封。

    ``SessionErrorKind.DECRYPT_FAILED`` 的**唯一生产入口**。发布 step
    (S3) 与巡检 (S5) 调 ``get_with_session`` 时捕获解密异常后调用它。

    结论是 ``failed`` 而**不是** ``session_invalid``：密钥错配时平台会话
    本身可能完全健康，我们只是读不出来。标成 needs_relogin 会让用户白扫
    一次码，密钥轮换没做完时更是全量误伤。``is_infra_failure`` 为真。
    """
    logger.error(f"[session.state] account={account_id} decrypt_failed: {message}")
    return SessionOpResult(
        success=False,
        status=SessionStatus.FAILED.value,
        message=message,
        detail={"error_kind": SessionErrorKind.DECRYPT_FAILED.value},
    ).to_dict()


# ── Adapter ───────────────────────────────────────────────


class SessionAdapter:
    """会话通道适配器 —— 每个平台一个实例，逻辑同一份。

    职责边界刻意很窄：解析已解密的会话、组装平台无关的请求、调
    nous-browser、把结果映射成 §7.8 信封。平台差异（DOM / 签名 / CLI）全在
    浏览器容器里；``session_state`` 的解密在 repository 层。
    """

    auth_type = AUTH_TYPE_SESSION

    def __init__(
        self, platform: str, *, client: Optional[BrowserClient] = None
    ) -> None:
        profile = SESSION_PLATFORM_PROFILES.get(platform)
        if profile is None:
            raise ValueError(f"Session channel not supported for platform: {platform}")
        self.platform_name = platform
        self._profile = profile
        self._client = client or BrowserClient()

    @property
    def profile(self) -> PlatformSessionProfile:
        return self._profile

    # ── 会话校验 (S1) ───────────────────────────────────────

    async def validate_session(
        self,
        account: Mapping[str, Any],
        *,
        environment: Optional[SessionEnvironment] = None,
    ) -> dict[str, Any]:
        """校验一个账号的会话是否还活着。

        spec §7.1：每平台的会话校验必须是**唯一一个函数** —— 反面教材是
        sau 把同一功能写了两份（CLI/web），同一个 bug 只修了一半，web 端
        长期把好账号判死。这里的唯一实现是浏览器侧的
        ``/session/validate``，backend 只有这一个入口能到达它。

        返回 §7.8 信封 dict。``detail["error_kind"]`` 存在时表示基建失败
        （用 ``browser_client.is_infra_failure`` 判定），调用方**不得**据此
        改动 ``status`` / ``session_checked_at``。

        ``environment`` 未显式传入时取 ``account["environment"]``（S1-data
        的 join 结果），没有就用全默认（直连）。
        """
        auth_type = account.get("auth_type")
        if auth_type is not None and auth_type != AUTH_TYPE_SESSION:
            # 路由错了。不静默 no-op，也不 raise 打断巡检循环 —— 给类型化失败。
            logger.warning(
                f"[session.validate] account={account.get('id')} "
                f"routed to session channel but auth_type={auth_type!r}"
            )
            return SessionOpResult(
                success=False,
                status=SessionStatus.FAILED.value,
                message=f"account auth_type is {auth_type!r}, not 'session'",
                detail={"reason": REASON_AUTH_TYPE_MISMATCH},
            ).to_dict()

        try:
            storage_state = parse_session_state(account)
        except SessionStateError as exc:
            return self._session_state_failure(account, exc).to_dict()

        env = environment or build_environment(account.get("environment"))
        result = await self._client.validate_session(
            self.platform_name, storage_state, env
        )
        logger.info(
            f"[session.validate] platform={self.platform_name} "
            f"account={account.get('id')} status={result.status}"
        )
        return result.to_dict()

    @staticmethod
    def _session_state_failure(
        account: Mapping[str, Any], exc: SessionStateError
    ) -> SessionOpResult:
        """把 ``SessionStateError`` 映射成 §7.8 信封。

        缺失/损坏都是业务原因 → ``session_invalid`` + reason，账号该重扫码。
        密钥错配走的是另一条路（repository 抛 → ``decrypt_failure_result``），
        永远不会到这里。
        """
        logger.warning(f"[session.validate] account={account.get('id')} {exc}")
        return SessionOpResult(
            success=False,
            status=SessionStatus.SESSION_INVALID.value,
            message=str(exc),
            detail={"reason": exc.reason},
        )

    # ── fail-fast 参数校验 (§7.7) ───────────────────────────

    def validate_publish_intent(
        self, intent: PublishIntent, *, now: Optional[datetime] = None
    ) -> list[str]:
        """起浏览器**之前**跑的纯校验，返回问题清单（空 = 通过）。

        纯函数、无 IO —— 发布 step 应在调 ``publish()`` 前先跑它，把参数
        错误挡在几分钟的浏览器+上传开销之外。

        ``now`` 只为测试注入。定时窗口在这里**又算一次**（请求 schema 已经算
        过）不是冗余：两次之间隔着排队与调度，提交时刚过 2 小时线的批次，真
        到执行时可能已经滑进线内 —— 那时再被平台拒，用户已经等了一次上传。

        形状部分委托给模块级的 ``validate_intent_shape``，与
        ``distribution_router.create_task`` 的提交时那道门**共用同一份实现**
        （图集设计 D3）。这里只额外做"要有素材"和扩展名两件事 —— 它们需要
        已解析的 ``media``，提交那一刻还不存在。
        """
        p = self._profile
        images = [m for m in intent.media if m.kind == "image"]
        problems = [
            sp.message
            for sp in validate_intent_shape(
                p,
                content_type=intent.content_type,
                title=intent.title,
                topics=intent.topics,
                image_count=len(images),
                visibility=intent.visibility,
                scheduled_at=intent.scheduled_at,
                has_cover=intent.cover is not None,
                platform_options=intent.platform_options,
                now=now,
            )
        ]
        if not intent.media:
            problems.append("no media to publish")
        problems.extend(self._extension_problems(intent.media))
        return problems

    def _extension_problems(self, media: Iterable[PublishMedia]) -> list[str]:
        problems: list[str] = []
        for item in media:
            allowed = (
                self._profile.video_extensions
                if item.kind == "video"
                else self._profile.image_extensions
            )
            name = (item.filename or "").lower()
            if not any(name.endswith(ext) for ext in allowed):
                problems.append(
                    f"{item.kind} {item.filename!r} is not one of " f"{sorted(allowed)}"
                )
        return problems

    # ── 发布 (S3) ───────────────────────────────────────────

    async def publish(
        self,
        account: Mapping[str, Any],
        intent: PublishIntent,
        *,
        environment: Optional[SessionEnvironment] = None,
    ) -> PublishOutcome:
        """发布一次内容。**S3 实现**，签名在此定死。

        故意叫 ``publish`` 而不是 OAuth 那侧的 ``publish_video``：内容形态
        由 ``intent.content_type`` 承载（video / images / 将来的其它），
        一个方法覆盖全部，避免"每加一种内容形态就加一个方法"的形状僵化。

        S3 的实现是 ``POST /publish``，流程见 spec §4.2：
        解密 storage_state → 组装 environment → 浏览器侧校验会话（失败
        直接返回 ``session_invalid``，**不尝试发布**）→ 执行（档位由平台
        决定）→ 回传新的 storage_state 供 backend 重新加密入库。

        调用方拿到 ``PublishOutcome`` 后必须：
        1. 先看 ``result.status``；``session_invalid`` → 账号标 needs_relogin
        2. ``is_infra_failure(result.to_dict())`` 为真 → 账号状态一律不动
        3. ``updated_storage_state`` 非空 → 重新 Fernet 加密写回（§4.2 第 6 步）

        三道门在**起浏览器之前**依次拦：auth_type 路由错 → 会话读不出来 →
        参数不合法（§7.7 fail-fast）。三者都返回类型化信封而不抛异常，因为
        调用方是一个"一个账号失败不能拖垮整批"的循环（§7.3 / 路线 C）。

        ``validate_publish_intent`` 在这里**又跑了一遍**，尽管发布 step 已经
        先跑过：那一遍是为了给用户更早、更具体的报错，这一遍是为了让任何
        绕过 step 的调用方也不可能把非法参数送进浏览器。纯函数、无 IO，重复
        一次的代价是零。
        """
        # 第 0 道门:这个平台到底实现了发布吗。
        #
        # 排在最前是因为它最便宜(一次字典查找),而且拦的是最危险的一类:
        # 小红书 / B 站已经能绑账号了 —— 会话是真的、账号是活的、auth_type
        # 也对 —— 后面三道门全都会放行。少了这道,一个 platform='xiaohongshu'
        # 的发布请求会带着有效会话一路走到浏览器,照着**根本没写的流程**
        # 在用户的真实账号上瞎点。
        #
        # 浏览器侧有对称保护(只注册 validator/login,不注册 publisher),
        # 两层都拦是刻意的:这条路径错一次的代价是往真实账号发出错东西。
        if not self._profile.supports_publishing:
            logger.warning(
                f"[session.publish] account={account.get('id')} "
                f"platform={self.platform_name} has no publisher implemented"
            )
            return PublishOutcome(
                result=SessionOpResult(
                    success=False,
                    status=SessionStatus.FAILED.value,
                    message=(
                        f"publishing is not implemented for '{self.platform_name}'; "
                        "the account can be bound and kept alive, but not published to"
                    ),
                    detail={"reason": REASON_PUBLISHING_NOT_IMPLEMENTED},
                )
            )

        auth_type = account.get("auth_type")
        if auth_type is not None and auth_type != AUTH_TYPE_SESSION:
            logger.warning(
                f"[session.publish] account={account.get('id')} "
                f"routed to session channel but auth_type={auth_type!r}"
            )
            return PublishOutcome(
                result=SessionOpResult(
                    success=False,
                    status=SessionStatus.FAILED.value,
                    message=f"account auth_type is {auth_type!r}, not 'session'",
                    detail={"reason": REASON_AUTH_TYPE_MISMATCH},
                )
            )

        try:
            storage_state = parse_session_state(account)
        except SessionStateError as exc:
            return PublishOutcome(result=self._session_state_failure(account, exc))

        problems = self.validate_publish_intent(intent)
        if problems:
            logger.warning(
                f"[session.publish] account={account.get('id')} "
                f"intent rejected before opening a browser: {len(problems)} problem(s)"
            )
            return PublishOutcome(
                result=SessionOpResult(
                    success=False,
                    status=SessionStatus.FAILED.value,
                    message="publish intent rejected: " + "; ".join(problems),
                    detail={"reason": REASON_INVALID_INTENT, "problems": problems},
                )
            )

        env = environment or build_environment(account.get("environment"))
        published = await self._client.publish(
            self.platform_name, storage_state, intent.to_payload(), env
        )
        logger.info(
            f"[session.publish] platform={self.platform_name} "
            f"account={account.get('id')} status={published.status}"
        )
        return PublishOutcome(
            result=published.result,
            platform_item_id=published.platform_item_id,
            published_url=published.published_url,
            updated_storage_state=published.updated_storage_state,
        )

    # ── 发布回读 (P1-3) ─────────────────────────────────────

    async def verify_publish(
        self,
        account: Mapping[str, Any],
        title: str,
        *,
        environment: Optional[SessionEnvironment] = None,
    ) -> VerifyResult:
        """回创作者中心确认 ``title`` 这条作品是不是真的上线了。

        与 ``publish`` 走同样的三道门（能力 → auth_type → 会话可解密），顺序
        和理由都一样。唯一不同的是**门没过时返回什么**：

          * 平台没实现回读 → ``not_published`` + ``reason=not_supported``。
            这是关于**我们的覆盖范围**的真话，调用方据此记"这里无法核实"，
            而不是把待办永远挂在那儿等一个永远不会来的答案。
          * auth_type 不对 / 会话解不开 → 一律走 ``failed``（非结论），调用方
            重试。这类失败说的是账号或我们的密钥，**不是**作品的状态；让它
            走 ``not_published`` 会因为一次密钥错配把作品挂成事故。

        这个不对称是刻意的：``not_published`` 会让待办变 blocked（找人来看），
        所以只有"我们真的看过平台"才配返回它。
        """
        if not self._profile.supports_publishing:
            # 不能发布的平台自然也没有回读 —— 而且这条路径压根不该出现，因为
            # 没有它发出去的作品。返回 not_supported 而不是静默成功。
            return VerifyResult(
                result=SessionOpResult(
                    success=False,
                    status=SessionStatus.NOT_PUBLISHED.value,
                    message=(
                        f"publish read-back is not available for "
                        f"'{self.platform_name}'"
                    ),
                    detail={"reason": "not_supported"},
                )
            )

        auth_type = account.get("auth_type")
        if auth_type is not None and auth_type != AUTH_TYPE_SESSION:
            return VerifyResult(
                result=SessionOpResult(
                    success=False,
                    status=SessionStatus.FAILED.value,
                    message=f"account auth_type is {auth_type!r}, not 'session'",
                    detail={"reason": REASON_AUTH_TYPE_MISMATCH},
                )
            )

        try:
            storage_state = parse_session_state(account)
        except SessionStateError as exc:
            return VerifyResult(result=self._session_state_failure(account, exc))

        env = environment or build_environment(account.get("environment"))
        verified = await self._client.verify_publish(
            self.platform_name, storage_state, title, env
        )
        logger.info(
            f"[session.verify] platform={self.platform_name} "
            f"account={account.get('id')} status={verified.status} "
            f"reason={verified.reason!r}"
        )
        return verified


__all__ = [
    "AUTH_TYPE_SESSION",
    "REASON_AUTH_TYPE_MISMATCH",
    "REASON_INVALID_INTENT",
    "REASON_MALFORMED_SESSION_STATE",
    "REASON_NO_SESSION_STATE",
    "SESSION_PLATFORM_PROFILES",
    "PlatformSessionProfile",
    "PublishIntent",
    "PublishMedia",
    "PublishOutcome",
    "SessionAdapter",
    "SessionEnvironment",
    "SessionOpResult",
    "SessionStateError",
    "SessionStatus",
    "build_environment",
    "decrypt_failure_result",
    "parse_session_state",
    "is_infra_failure",
    "supported_session_platforms",
    "publishable_session_platforms",
]
