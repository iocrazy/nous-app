"""Distribution — publish task schemas (PR-D2).

Ported from the media-router prototype (models/schemas.py TaskCreate /
TaskResponse) and rewritten to mediahub conventions: BIGINT ids serialize as
str (JS 2^53), multi-channel ('official'|'h5'|'session'), visibility triad
matching the publish_tasks CHECK constraint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.distribution.publish_options import (
    MAX_MUSIC_NAME_LEN,
    SELF_DECLARATIONS,
    normalize_collection,
    normalize_music,
    validate_scheduled_at,
)

ContentType = Literal["video", "images", "article"]
# 「自主声明」的六个合法值 —— 直接由 publish_options 的原文元组构造，所以
# 白名单只有一份。``Literal[tuple]`` 在运行时与逐个写出来完全等价（下标本来
# 就收一个 tuple），静态检查器不认，故 type: ignore。这样 OpenAPI 里就带上了
# enum，前端下拉与后端校验共用同一份契约。
SelfDeclaration = Literal[SELF_DECLARATIONS]  # type: ignore[valid-type]
Visibility = Literal["public", "friends", "private"]
DistributionMode = Literal["broadcast", "one_to_one"]
# 'session' (spec 2026-08-04 §4.2) — publish by driving the platform's own web
# UI with the account's stored browser session. Migration 403 already accepts it
# on publish_task_accounts.channel; without it here the request schema silently
# rejects the only value that reaches the session publish path, leaving the whole
# channel unreachable from the API.
Channel = Literal["official", "h5", "session"]

# Topics == Douyin hashtags (话题). The UI collects them as chips and the
# workflow delivers them to Douyin (H5 hashtag_list JsonArray / official post
# text `#tag `). Bound so a single batch can't carry an absurd hashtag wall.
MAX_TOPICS = 20
MAX_TOPIC_LEN = 50

# 中立硬顶：与平台无关，任何 content_type=images 的请求都过这一道（gate ①）。
#
# 这个数曾经是**猜的** —— 原注释写着「H5 分享文档没明说上限，这里沿用 app 已知
# 的图集上限」。[实测 2026-08-11]（图集设计 §3.4 V2）抖音图文上传页原文
# `最多支持上传35张图片，图片格式不支持gif格式`（exact=1），**猜对了**；但在此
# 之前它的状态是「无人验证的常量」，现在才是事实。
#
# ⚠️ 数字与 ``PlatformSessionProfile.max_images``（douyin=35）相同**纯属巧合**，
# 两者不许合并：这一道是"一条笔记不能带一堵图墙"的防御性中立上界，那一道是
# "抖音的上限"。换个平台两者就分开了。
MAX_IMAGES = 35


def normalize_topics(topics: Optional[list[str]]) -> list[str]:
    """Sanitize user-entered topics into bare hashtag words: strip surrounding
    whitespace and leading '#' characters, drop empties (a stray '#' or a
    trailing comma must not 422 the whole publish), reject over-long tags, and
    cap the count. Returns a fresh list (never mutates the input)."""
    if not topics:
        return []
    out: list[str] = []
    for raw in topics:
        tag = raw.strip().lstrip("#").strip()
        if not tag:
            continue
        if len(tag) > MAX_TOPIC_LEN:
            raise ValueError(f"topic exceeds {MAX_TOPIC_LEN} characters: {tag!r}")
        out.append(tag)
    if len(out) > MAX_TOPICS:
        raise ValueError(f"at most {MAX_TOPICS} topics allowed")
    return out


class TopicRef(BaseModel):
    """一个话题名 → 平台话题实体 id 的绑定（mig 426）。

    ``topic_id`` 是抖音建议接口回的 ``cid``。它**现在还没有被发布链使用** ——
    发布时话题依然是打进描述框的 `#词`，这一版刻意不改（见
    ``services/distribution/topic_suggest.py`` 模块头的边界说明）。

    那为什么现在就存？因为它是"这条话题绑到了平台上哪个实体"的**唯一凭据**，
    而且只在用户从下拉里选中的那一刻存在 —— 事后无法补。等到要回答"带 cid
    发布和纯打字发布出来的作品有没有区别"时，没有这份记录就只能重新做一遍
    实验。存下来，将来才有东西可比。

    ``topics``（纯名字列表）保持不变，仍是发布链读的那一份；``topic_refs`` 是
    平行的、可缺失的装饰记录，两者不互相依赖。
    """

    name: str
    topic_id: str = ""
    # 选中当刻的累计播放量。同样是"事后补不回来"的量 —— 它随时间涨，一个月后
    # 再查到的数字回答不了"用户当时看到的是什么"。
    view_count: int = 0

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        cleaned = normalize_topics([v])
        if not cleaned:
            raise ValueError("topic ref name is empty")
        return cleaned[0]


def normalize_topic_refs(refs: Optional[list["TopicRef"]]) -> list["TopicRef"]:
    """去掉没有 ``topic_id`` 的条目（它们不带任何 ``topics`` 之外的信息）、按
    name 去重（大小写不敏感，与前端 addTopic 同口径）、封顶 ``MAX_TOPICS``。

    ⚠️ 超上限**截断而不是 raise**：``topic_refs`` 是装饰记录，让它把一次本来
    合法的发布 422 掉是本末倒置 —— ``topics`` 自己已经有硬上限了。
    """
    if not refs:
        return []
    seen: set[str] = set()
    out: list[TopicRef] = []
    for ref in refs:
        if not ref.topic_id:
            continue
        key = ref.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out[:MAX_TOPICS]


class MusicRef(BaseModel):
    """从平台曲库里选中的那一首的**结构化身份**（mig 429）。

    为什么曲名不能当身份（实测 2026-08-15）
    ======================================
    搜「起风了」一页里 **5 条标题完全相同**、id 各异、使用量 0～30023 不等；
    而分类榜里的曲子按曲名去搜 3/3 都搜不到那一首，其中一条还返回了一个
    **标题一模一样但 id 不同**的歌。按名匹配在那一条上会判「精确命中」、点击
    成功、读回校验也过 —— 每一道关卡都亮绿灯，发出去的是另一首歌，而配乐发出
    去之后平台不让换。

    所以选择器这条路径存的是指纹，不是名字：``music_name`` 仍是浏览器侧填进
    平台搜索框的关键词，而 (名, 作者, 时长) 三元组是"哪一行才是用户点的那张
    卡片"的判据，``music_id`` 是唯一真正的身份。

    ⚠️ ``music_id`` 必须是抖音搜索结果的 **``id_str``**，不是同一条里的 ``id``。
    上游同时给两个字段：``id`` 是 JSON number 且超过 2^53（实测
    ``6953836671917951012``），经 JSON 进前端就精度丢失。两者并存是真实形状，
    不是可以"统一"的漂移（CLAUDE.md「边界 mock 必须用真实 JSON 形状」）。
    这里声明为 ``str``，pydantic v2 不做 int→str 隐式转换，所以误传 ``id``
    会 422 —— 这正是我们想要的响：一个静默降精度的 id 比拒收坏得多。
    """

    music_id: str = Field(min_length=1, max_length=64)
    music_name: str = Field(min_length=1, max_length=MAX_MUSIC_NAME_LEN)
    # 作者与时长是三元组的另外两维。作者可能为空串（平台偶有无作者的曲目），
    # 那不是缺数据 —— 浏览器侧对齐时会自动跳过它拿不到的那一维，宁可判
    # ambiguous 也不猜。
    music_author: str = Field(default="", max_length=200)
    #: 秒。0 = 上游没给（同上，缺一维而不是错一维）。
    duration: int = Field(default=0, ge=0, le=24 * 3600)
    #: 选中当刻的使用人数。事后补不回来 —— 它一直在涨，而"用户挑的是 3 万人
    #: 用的那一首"正是他挑它的理由。纯记录，不参与匹配。
    user_count: int = Field(default=0, ge=0)
    cover_url: str = Field(default="", max_length=2000)

    @field_validator("music_id", "music_name", "music_author", "cover_url")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()

    @field_validator("music_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("music ref name is empty")
        return v.strip()


class AccountConfigOverride(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    topics: Optional[list[str]] = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        return None if v is None else normalize_topics(v)


class PublishTaskCreate(BaseModel):
    # 这一批属于哪个 workspace。**字符串**（Snowflake），全程不 Number() 化。
    #
    # `publish_tasks.team_id` 这一列从建表起就在，缺的一直是把值送到那里的
    # 字段 —— 于是每一行都落 NULL，从它镜像出的 issue 也就没有 team，而待办
    # 列表的每一种 scope 都带 `team_id` 过滤（连 `my` 都带），那些 issue 被
    # AND 掉，用户永远看不见自己发布失败的工单。
    #
    # 省略 = 「没说」，不是「没有」：路由回落到调用者的个人 team（个人空间
    # 本身就是一行 `teams.kind='personal'`）。这里不写默认值，是因为 schema
    # 拿不到调用者身份，猜不出该填谁 —— 归属由路由解析并**校验成员资格**，
    # 客户端传什么都不能直接当授权用。
    team_id: Optional[str] = None
    content_type: ContentType = "video"
    resource_ids: list[str] = Field(default_factory=list)
    title: str = Field(min_length=1, max_length=500)
    description: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
    # 与 topics 平行的实体绑定记录（mig 426）。缺失完全合法 —— 手打的话题本来
    # 就没有 cid，只有从建议下拉里选中的才有。
    topic_refs: list[TopicRef] = Field(default_factory=list)
    visibility: Visibility = "public"
    ai_content: bool = False
    allow_download: bool = True
    distribution_mode: DistributionMode = "broadcast"
    channel: Channel = "h5"
    account_ids: list[str] = Field(min_length=1)
    account_configs: dict[str, AccountConfigOverride] = Field(default_factory=dict)
    cover_vertical_resource_id: Optional[str] = None
    cover_horizontal_resource_id: Optional[str] = None
    # ── 抖音发布页的平台原生字段（mig 407） ──
    # self_declaration 是「自主声明」下拉的**原文**（六个之一）。None = 不碰
    # 那个控件；与 ai_content 的关系（自动映射 + 允许覆盖）见
    # services/distribution/publish_options.py::resolve_self_declaration。
    self_declaration: Optional[SelfDeclaration] = None
    collection_name: Optional[str] = None
    # ── 「选择音乐」（mig 425） ──
    # 曲名原文。None = 不碰音乐控件 = 平台默认（原声），也就是这个字段存在
    # 之前每条作品的行为。没有白名单：曲名不是封闭词表，存在性由浏览器侧在
    # 平台自己的搜索结果里判定，搜不到那一行失败（**不**静默发一条没配乐的
    # 作品）。
    music_name: Optional[str] = None
    # ── 配乐的结构化身份（mig 429） ──
    # 从曲库选择器里点中一张卡片时带上。``None`` = 手打曲名的老路径（行为不变）。
    # 两者共存时 ``music_ref`` 为准：``music_name`` 仍是浏览器侧填进平台搜索框
    # 的关键词，而三元组决定"结果里哪一行才是他点的那一首"，不唯一命中就失败
    # （``music_ambiguous``），绝不"挑使用量最高的继续发"。
    music_ref: Optional[MusicRef] = None
    # 定时发布。必须带时区（naive 会被拒），窗口 2h~14d —— 见 _validate_content。
    scheduled_at: Optional[datetime] = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, v: list[str]) -> list[str]:
        return normalize_topics(v)

    @field_validator("topic_refs")
    @classmethod
    def _clean_topic_refs(cls, v: list[TopicRef]) -> list[TopicRef]:
        return normalize_topic_refs(v)

    @field_validator("collection_name")
    @classmethod
    def _clean_collection(cls, v: Optional[str]) -> Optional[str]:
        return normalize_collection(v)

    @field_validator("music_name")
    @classmethod
    def _clean_music(cls, v: Optional[str]) -> Optional[str]:
        return normalize_music(v)

    @model_validator(mode="after")
    def _music_name_follows_the_reference(self) -> "PublishTaskCreate":
        """选了具体一首歌时，``music_name`` 由 ``music_ref`` 派生，不由客户端说了算。

        两个字段各写各的会漂：浏览器侧用 ``music_name`` 当搜索关键词、用
        ``music_ref`` 当对齐指纹，一旦名字不是那首歌的名字，搜索结果里根本不会
        有它 —— 表现为"选了歌却发布失败"，而根因是两份真相。
        派生而不是校验，是因为这里没有"用户真的想让它们不同"的合法情形。
        """
        if self.music_ref is not None:
            object.__setattr__(self, "music_name", self.music_ref.music_name)
        return self

    @model_validator(mode="after")
    def _validate_content(self) -> "PublishTaskCreate":
        if self.content_type in ("video", "images") and not self.resource_ids:
            raise ValueError("resource_ids required for video/images content")
        if self.content_type == "images" and len(self.resource_ids) > MAX_IMAGES:
            raise ValueError(f"at most {MAX_IMAGES} images allowed")
        # one_to_one round-robins a resource per account — that's a VIDEO
        # semantic. An images task is one note carrying every image (broadcast
        # to each account, never split), so the count check doesn't apply.
        if (
            self.content_type == "video"
            and self.distribution_mode == "one_to_one"
            and len(self.resource_ids) < len(self.account_ids)
        ):
            raise ValueError("one_to_one needs at least as many resources as accounts")
        # 定时窗口在**提交这一刻**就拒（§7.7 fail-fast 的第一道）。让用户填完
        # 一切、点了发布、等浏览器起来、传完几百 MB 视频才被平台拒绝，是这条
        # 路径上最差的体验；而这一道只要一次纯计算。
        problem = validate_scheduled_at(self.scheduled_at)
        if problem:
            raise ValueError(problem)
        return self


class TaskAccountOut(BaseModel):
    id: str
    account_id: str
    username: str
    avatar_url: Optional[str] = None
    channel: str
    status: str
    error_message: Optional[str] = None
    published_url: Optional[str] = None
    platform_item_id: Optional[str] = None
    published_at: Optional[datetime] = None
    # ── 回读（``publish_readback``）────────────────────────────────
    # ``status='success'`` 只说明**我们这边**传完了。这一条真的在平台上线了
    # 没有，是回读那一轮才知道的事，而在这两列被下发之前，前端根本无从区分
    # "发完了" 与 "发完了但平台还没确认" —— 用户看到的都是一个 Published。
    #
    # ⚠️ ``not_live``（我们查过了，它没上线）与 ``pending``（这一轮没问出来）
    # 必须一路保持可分辨，直到画到屏幕上为止。合并它们是很自然的手滑，代价
    # 却极不对称：一次容器宕机会被显示成"平台拒了你的稿"。原话见
    # ``publish_readback`` 的模块 docstring。
    #
    # NULL = 还没查过（等于 pending 的起点），不是"不需要查"。
    verify_state: Optional[str] = None
    # 类型化原因（``[rejected] ...`` / ``[under_review] ...`` /
    # ``[verification_abandoned] ...``）。前端按方括号里的 reason 键控翻译，
    # 后面那段 prose 是写给日志的 —— 同 ``PUBLISH_NOTE_KEYS`` 的口径。
    verify_detail: Optional[str] = None


class PublishTaskOut(BaseModel):
    id: str
    content_type: str
    title: str
    description: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
    # 回显（同 scheduled_at / self_declaration 的理由）：写进去却读不回来，就等
    # 于没人能证明它真的存下来了。
    topic_refs: list[TopicRef] = Field(default_factory=list)
    visibility: str = "public"
    distribution_mode: str = "broadcast"
    status: str  # aggregated display status (see aggregate_task_status)
    created_at: datetime
    # 回显新表单字段：记录页要能说清"这一批到底定时到了什么时候、声明选了
    # 哪一项"。写进去却读不回来，是 CLAUDE.md「触发路径必须类型化回显」在
    # 表单字段上的同族问题 —— ai_content 正是这么静默了两个版本。
    scheduled_at: Optional[datetime] = None
    # 这一批的定时**现在**还能不能兑现（``SCHEDULE_STATE_*``）。
    #
    # 派生字段，服务端算：判据是 ``SCHEDULE_MIN_LEAD``，而那个常量属于后端。
    # 让前端拿 ``scheduled_at`` 自己减一个它猜的下限，就是把同一条规则抄成
    # 第二份 —— 而且抄的那一份没有任何东西能钉住它别漂。
    schedule_state: str = "none"
    self_declaration: Optional[str] = None
    collection_name: Optional[str] = None
    music_name: Optional[str] = None
    # 回显（同 topic_refs 的理由）：写进去却读不回来，就没人能证明它真的存下来
    # 了。这一条尤其要读得回来 —— 它是"发出去的到底是哪一首"的唯一凭据。
    music_ref: Optional[MusicRef] = None
    accounts: list[TaskAccountOut] = Field(default_factory=list)


class PublishTaskListResponse(BaseModel):
    tasks: list[PublishTaskOut]


#: 重投一批的三种意图。默认 ``as_scheduled`` —— **不许**默认改写用户当初选的
#: 时间：把一条本该 15:20 上线的稿子改成"现在就发"，是一个不可撤销的动作，
#: 而"再试一次"这个词从来不包含它。
RetryMode = Literal["as_scheduled", "now"]


class PublishTaskRetryRequest(BaseModel):
    """``POST /tasks/{id}/retry`` 的可选请求体（不传 = ``as_scheduled``）。

    ``now`` 是用户在界面上**另外**点的那个按钮（"Publish now"）：它显式清掉
    ``scheduled_at``，也就显式承认"我知道这不再是定时发布了"。所以它不是
    retry 的一个参数细节，而是一个不同的意图，必须由调用方写出来。
    """

    mode: RetryMode = "as_scheduled"


class ShareSchemaResponse(BaseModel):
    schema_url: str
    share_id: str


class PublishSmsStateResponse(BaseModel):
    """ "这次发布此刻在不在等验证码"—— 前端渲染输入框的唯一依据。

    ``waiting`` 语义很窄：它表示**有一个发布协程此刻正停在 await 上**，不是
    "页面上看见了验证码输入框"。放宽它就会给一个没人在等的用户弹输入框，那是
    登录侧曾经犯过的错的镜像版本（当时是在没人发码时告诉用户"码已发出"）。

    ``outcome`` 只在挑战结束后才有值，用来说明它是怎么结束的（收了码 / 超时 /
    次数用尽）。挑战结束却不说一声，输入框就会挂在屏幕上，用户往里输的每一个
    码都会石沉大海。
    """

    waiting: bool
    account_id: Optional[int] = None
    platform: Optional[str] = None
    attempts_left: int = 0
    max_attempts: int = 0
    seconds_remaining: float = 0.0
    outcome: Optional[str] = None
    message: str = ""


class PublishSmsVerdictResponse(BaseModel):
    """平台对这个码做了什么。**提交动作的当场回执**。

    ``outcome`` 是闭集（见 browser/app/publish_sms.py）：``accepted`` /
    ``rejected`` / ``exhausted`` / ``expired`` / ``abandoned`` / ``not_pending``
    / ``unreachable``。用类型化 outcome 而不是布尔成功位，是因为 ``rejected``
    （码不对，重输）和 ``unreachable``（没送到，等一下再试）要用户做的事正好
    相反，塌缩成一个布尔就会让一次容器抖动去污蔑一个正确的验证码。

    ``retryable`` 由浏览器算好透传，不在任何一层重新推导 —— 重推一次就是多一次
    和浏览器分歧的机会，而分歧的形态会是"界面说还能再试、发布其实已经放弃了"。
    """

    outcome: str
    message: str
    attempts_left: int = 0
    retryable: bool = False
