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


class BrowserHealthResponse(BaseModel):
    """Can the QR-login channel work *right now* — read by the binding entry
    point before it offers the button (D1).

    Why this exists as its own endpoint: every QR binding runs inside
    ``nous-browser``. While that container is restarting (a deploy) or down,
    the click is a guaranteed failure, but the user only found out after the
    modal opened and sat on "Starting..." until it timed out. This turns that
    into an upfront, typed statement.

    ⚠️ Deliberately **not** folded into ``/api/v1/readyz``. That probe is the
    deploy smoke gate (``deploy-gpu.yml``): making backend-readiness depend on
    a sibling container would mark the backend not-ready every time the browser
    restarts, and smoke failure auto-rolls-back the release. Same family as the
    ``long_running`` misuse the repo already paid for.

    The fields mirror ``BrowserClient.health()``'s typed result rather than
    re-deriving a verdict here — ``ok`` is true only when the browser container
    answered AND reported a real Chromium launch (``browser_ready``), so there
    is no code path that can report healthy without a probe having succeeded.
    """

    ok: bool
    # ``SessionErrorKind`` value when unhealthy (``unreachable`` / ``timeout``
    # / ``not_configured`` / ``server_error`` / …), None when ok. str, not the
    # enum, for the same reason ``auth_type`` is: a kind added later must not
    # 500 this endpoint.
    error_kind: Optional[str] = None
    # Diagnostic detail, same pass-through posture as ``SessionOpResponse``.
    # The UI shows its own translated copy — it must not print this.
    message: str = ""


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


class SessionPhoneRequest(BaseModel):
    """只在 ``login_method == "sms"`` 的平台上用得到（当前只有小红书）。

    与 nous-browser 的 ``PhoneNumberRequest`` **逐字相同**（纯数字、6–20 位），
    理由同 ``SessionSmsRequest``：宽一格就会让一个输入错误在浏览器侧变成 422，
    再被 ``BrowserClient`` 读成传输层失败，最后以"基建故障"的面目出现在用户
    面前。

    ⚠️ 位数刻意不钉死 11 位。那是中国大陆手机号的形状，把一个国家的号码格式焊进
    通道契约，与"把一个平台的登录形态套到所有平台"是同一类错误。

    手机号**不入库、不进日志**：它只经这一跳送进那一页的输入框。
    """

    phone: str = Field(min_length=6, max_length=20, pattern=r"^\d+$")


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


# ── 能力下发（图集设计 §2 D1） ────────────────────────────────────


class PlatformCapability(BaseModel):
    """一个平台**现在**能发什么 —— 由 ``PlatformSessionProfile`` 投影而来。

    这个端点存在的理由是消灭第三份声明。曾经"能不能发图集"写在三个地方：
    浏览器的 ``publish.py``、后端 profile、前端
    ``components/Distribution/capabilities.ts``。三份靠**注释里的纪律**同步，
    结果是前端给了 Images tab、后端放行、浏览器拒 —— 用户填完整个表单、提交、
    排队，最后一步才拿到 ``unsupported_content_type``。

    现在链条是：``browser/app/capabilities.py``（唯一真相，CI 守卫
    ``test_capability_matches_browser.py`` 钉住"后端不得声明浏览器没实现的
    内容类型"）→ 后端 profile → **本模型下发** → 前端只读。前端不再持有任何
    能力常量，所以 T7 翻转声明时前端**不需要发版**。

    ⚠️ 所有集合字段都是 ``sorted()`` 后的 list。源头是 ``frozenset``，而
    frozenset 的迭代顺序随进程 hash 种子变 —— 不排序的话同一份代码每次重启
    返回的 JSON 都不同，缓存和快照测试全部不稳定。
    """

    platform: str
    # 这个平台怎么登录：``"qrcode"`` / ``"sms"``。前端据此决定画扫码框还是画
    # 手机号+验证码表单。
    #
    # ⚠️ 它**不受 ``is_placeholder`` 清空**（见 ``project_capability``）：登录
    # 与发布是两件事，而"能绑不能发"的平台恰恰只有登录这一件事可做。小红书就是
    # 这一档 —— 在这个字段存在之前，前端对所有平台一律画二维码占位框，而后端
    # 对它根本不会产出二维码，那条绑定链一次都没跑通过。
    login_method: str
    # 能不能发布。False 时下面的能力字段**全部置空**，见 ``is_placeholder``。
    supports_publishing: bool
    # 占位声明：``supports_publishing=False`` 的平台，profile 里的
    # content_types / 扩展名是"等实现时对着平台实测填准"的占位值（
    # session_adapter.py 里那段注释的原话）。把占位值原样下发，等于用一个
    # 看起来权威的 API 响应给猜测背书 —— 正是本次要消灭的病。所以这一档
    # 的能力字段一律清空，并显式说明"这里没有事实"。
    is_placeholder: bool = False

    content_types: list[str] = Field(default_factory=list)
    video_extensions: list[str] = Field(default_factory=list)
    image_extensions: list[str] = Field(default_factory=list)
    min_images: Optional[int] = None
    max_images: Optional[int] = None
    max_title_len: Optional[int] = None
    max_topics: Optional[int] = None
    # 派生字段，不是 profile 上的列：profile 用 ``schedule_min_lead`` /
    # ``schedule_max_ahead`` 两个 timedelta 表达定时窗口，两个都是 None 就是
    # "没接定时"。前端要的是一个布尔 + 两个秒数，换算在这里做一次，而不是让
    # 每个调用方各自解释 None 的含义。
    supports_scheduling: bool = False
    schedule_min_lead_seconds: Optional[int] = None
    schedule_max_ahead_seconds: Optional[int] = None
    self_declarations: list[str] = Field(default_factory=list)
    supports_collection: bool = False
    supports_music: bool = False


class CapabilitiesResponse(BaseModel):
    """``GET /distribution/capabilities`` 的响应。

    ``platforms`` 是 map 而不是 list：调用方永远是"这个账号的平台能不能干
    这件事"，按 key 取比在数组里找快也更难写错。
    """

    platforms: dict[str, PlatformCapability]
