"""Distribution — schemas for platform account connect/list (PR-D1)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ConnectAccountRequest(BaseModel):
    platform: Literal["douyin"]
    scope_type: Literal["user", "team"]
    scope_id: str = Field(min_length=1, max_length=64)


class ConnectAccountResponse(BaseModel):
    auth_url: str


class SocialAccountOut(BaseModel):
    id: str  # Snowflake BIGINT → str（同 team.py 约定；repo 的 _public_row 已转）
    scope_type: str
    scope_id: str
    platform: str
    platform_user_id: str
    # mig 414 — 平台侧公开账号名（抖音号 / 小红书号）。展示用，可改名，因此
    # **不参与**账号唯一键。⚠️ 响应模型是白名单：漏掉这一行的话 repo 明明返回
    # 了它、前端却永远收不到，且不报错（与 auth_type 那次同款静默）。
    platform_handle: Optional[str] = None
    username: str
    avatar_url: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    # mig 398 — 'oauth' | 'session'. Kept as str (not Literal) so a row written
    # by a newer migration can never 500 the list endpoint; the frontend owns
    # the narrow union. Defaulted so pre-398 callers keep constructing this.
    auth_type: str = "oauth"
    # 'active' | 'expired' | 'needs_relogin' (mig 398).
    status: str
    # Last successful session validation; None for oauth accounts (mig 398).
    session_checked_at: Optional[datetime] = None
    created_at: datetime


class AccountListResponse(BaseModel):
    accounts: list[SocialAccountOut]


class AccountUsageResponse(BaseModel):
    """What unbinding this account would touch (P0-2 / mig 416).

    Exists purely so the confirmation dialog can quote a real number. The old
    Remove button had no dialog at all while ``publish_task_accounts`` cascaded
    off the row, so a click could take 10 publish records with it silently.
    Soft delete keeps them, and this endpoint is what lets the UI say so
    truthfully — including "0", which must be shown as 0 rather than dropped.

    Not folded into ``SocialAccountOut``: the list endpoint would then run a
    COUNT per account on every page load, to answer a question only the delete
    path asks.
    """

    publish_records: int


# ── 会话通道扫码登录（S2, spec §4.1） ──────────────────────────────


class SessionLoginRequest(BaseModel):
    """与 ``ConnectAccountRequest`` 同形状 —— 两种绑定方式（官方授权 / 扫码
    会话）对调用方只差一个端点，scope 语义完全一致。``platform`` 不写死
    Literal：会话通道支持哪些平台由 ``supported_session_platforms()`` 说了算
    （S1 的 ``SESSION_PLATFORM_PROFILES``），在两处维护同一份名单迟早对不上。
    """

    platform: str = Field(min_length=1, max_length=32)
    scope_type: Literal["user", "team"]
    scope_id: str = Field(min_length=1, max_length=64)


class SessionLoginResponse(BaseModel):
    """扫码在 workflow 里异步进行，端点立刻返回 task id。

    二维码与状态走 ``task_tracking`` 的 Supabase Realtime（``metadata.login``）
    —— 不新建 SSE 通道（spec §4.3 #5）。
    """

    task_id: str


class SessionSmsRequest(BaseModel):
    # 与 nous-browser 的 ``SmsCodeRequest`` **逐字相同**（纯数字、4–8 位）。
    # 这里放宽一格的代价不是"多转发一次"：浏览器侧会 422，而 422 在
    # BrowserClient 眼里是传输层失败，用户会看到一句 "browser service
    # returned HTTP 422" —— 把一个输入错误报成基建故障。边界上先挡住。
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


class SessionOpResponse(BaseModel):
    """spec §7.8 的类型化结果信封，原样透给前端。

    前端按 ``status`` 分支给可操作提示（CLAUDE.md「触发路径必须类型化失败
    回显」）；``detail.error_kind`` 存在表示是基建失败（容器不可达 / 没配
    token），与"验证码错了"是两回事。
    """

    success: bool
    status: str
    message: str
    detail: dict = Field(default_factory=dict)


class SessionLoginCancelResponse(BaseModel):
    """取消不复用 ``SessionOpResponse``：``status`` 那个字段的取值域是 §7.8
    的通道枚举，而"取消"根本不是通道状态。硬塞一个 ``cancelled`` 进去会污染
    一份两个服务共享的契约，塞 ``success`` 又会和"登录成功"撞名。

    ``context_released`` 单独回显，是因为它可能为 false（浏览器容器此刻不可
    达）—— 任务确实取消了，但那个有头浏览器 context 要等 TTL 才消失。
    """

    cancelled: bool
    context_released: bool
    message: str
