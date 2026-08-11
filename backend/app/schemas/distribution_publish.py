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
    SELF_DECLARATIONS,
    normalize_collection,
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


class AccountConfigOverride(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    topics: Optional[list[str]] = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        return None if v is None else normalize_topics(v)


class PublishTaskCreate(BaseModel):
    content_type: ContentType = "video"
    resource_ids: list[str] = Field(default_factory=list)
    title: str = Field(min_length=1, max_length=500)
    description: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
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
    # 定时发布。必须带时区（naive 会被拒），窗口 2h~14d —— 见 _validate_content。
    scheduled_at: Optional[datetime] = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, v: list[str]) -> list[str]:
        return normalize_topics(v)

    @field_validator("collection_name")
    @classmethod
    def _clean_collection(cls, v: Optional[str]) -> Optional[str]:
        return normalize_collection(v)

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


class PublishTaskOut(BaseModel):
    id: str
    content_type: str
    title: str
    description: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
    visibility: str = "public"
    distribution_mode: str = "broadcast"
    status: str  # aggregated display status (see aggregate_task_status)
    created_at: datetime
    # 回显新表单字段：记录页要能说清"这一批到底定时到了什么时候、声明选了
    # 哪一项"。写进去却读不回来，是 CLAUDE.md「触发路径必须类型化回显」在
    # 表单字段上的同族问题 —— ai_content 正是这么静默了两个版本。
    scheduled_at: Optional[datetime] = None
    self_declaration: Optional[str] = None
    collection_name: Optional[str] = None
    accounts: list[TaskAccountOut] = Field(default_factory=list)


class PublishTaskListResponse(BaseModel):
    tasks: list[PublishTaskOut]


class ShareSchemaResponse(BaseModel):
    schema_url: str
    share_id: str
