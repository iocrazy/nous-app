"""nous-browser 内网客户端 —— backend → 浏览器容器的唯一出口 (spec S1)。

安全边界 (spec §7.6)
====================
nous-browser **不碰 DB、不持有 Fernet 密钥**。解密是 backend 的责任：
调用方从 ``social_accounts.session_state`` 读出密文、用 ``secret_box``
解开、把明文 storage_state 传进本模块。本模块负责把它送到内网 HTTP，
并且：

- 绝不把 storage_state / proxy_url 写进任何日志（只 log platform、
  status、error_kind、HTTP 状态码）
- 绝不把请求体或响应 detail 整体 log 出来

会话通道的结果契约 (spec §7.8)
==============================
本模块是通道的最底层，因此**通道级的结果类型定义在这里**
（``SessionStatus`` / ``SessionErrorKind`` / ``SessionOpResult`` /
``SessionEnvironment``），由 ``session_adapter`` 再导出给业务层。

两个正交的维度必须分开，混在一起会造成灾难性误判：

``status``
    面向 UI 的业务结论（§7.8 枚举）。``session_invalid`` 意味着"这个账号
    真的掉线了，请重新扫码"。
``detail["error_kind"]``
    基建失败的种类（``SessionErrorKind``）。存在即表示**我们根本没能问出
    结论** —— 浏览器容器宕机、token 没配、解密失败。

巡检 workflow (spec §4.4) 必须用 ``is_infra_failure()`` 判定：browser
容器挂掉的那一轮，一百个账号会全部拿到失败结果，若不区分就会把它们统统
标成 ``needs_relogin``，用户要重扫一百次码。基建失败下**不许改动账号
状态**。

平台无关 (spec §6.1 硬要求 b)
=============================
本模块不出现任何平台专属逻辑：``platform`` 只是透传给浏览器服务的一个
字符串，浏览器侧按它选执行档位（DOM 木偶戏 / 签名机+裸 HTTP / 外包 CLI）。
backend 永远不需要知道用的是哪一档。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

import httpx
from loguru import logger

# ── 超时 ─────────────────────────────────────────────────────
# 校验会真的开一个有头浏览器（Xvfb）、加载 storage_state、goto 平台页面并
# 重试 3 次（§7.1），90s 是给足余量后的**上界** —— 不是"等到好为止"。
# §7.2「所有轮询必须有上界」的同族纪律：任何等待都必须能超时收敛。
DEFAULT_VALIDATE_TIMEOUT_SECONDS = 90.0
DEFAULT_HEALTH_TIMEOUT_SECONDS = 5.0
# 扫码登录 (S2)。/start 要起 context + goto 平台页 + 等二维码渲染，与校验同量级；
# 其余三个只是查/写浏览器容器里那个**已经存在**的 login session，必须短 ——
# 状态轮询卡住 60s 会让整条轮询循环失去意义（§7.2 的上界纪律）。
DEFAULT_LOGIN_START_TIMEOUT_SECONDS = 90.0
DEFAULT_LOGIN_POLL_TIMEOUT_SECONDS = 20.0
DEFAULT_LOGIN_STATE_TIMEOUT_SECONDS = 30.0
DEFAULT_LOGIN_CLOSE_TIMEOUT_SECONDS = 15.0
# 发布 (S3)。一次调用里包含：起 context + 会话校验 + 下载素材到 /tmp +
# DOM 上传（几百 MB）+ 等发布页表单渲染（§7.4 实测约 40s，等待给到 120s）+
# 提交。因此它比其余端点大一个量级 —— 但**仍然有上界**（§7.2）：15 分钟没
# 回来就是卡住了，继续等只会把 DBOS step 也一起挂死。
DEFAULT_PUBLISH_TIMEOUT_SECONDS = 900.0
# 连接握手与业务处理分开设上界：容器没起来时应当 5s 内就报 unreachable，
# 而不是耗满 90s 的读超时。
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_WRITE_TIMEOUT_SECONDS = 30.0

INTERNAL_TOKEN_HEADER = "X-Internal-Token"

DEFAULT_LOCALE = "zh-CN"
DEFAULT_TIMEZONE_ID = "Asia/Shanghai"


class SessionStatus(str, Enum):
    """spec §7.8 的类型化结果枚举 —— 通道级，**不得**加入平台专属值
    (§6.1 硬要求 a)。

    每个操作只使用其中一个子集：

    - ``/session/validate``: session_valid / session_invalid / proxy_failed
      / timeout / failed
    - 扫码登录 (S2): 追加 qrcode_expired / sms_required
    - 发布 (S3): 追加 published
    """

    SESSION_VALID = "session_valid"
    SESSION_INVALID = "session_invalid"
    # S2 扫码登录的四个过程态 + 一个终态。WAITING_SCAN / SCANNED / SUCCESS 是
    # S2 补齐的：spec §7.8 明写"两侧枚举必须全集对齐"，而 S1 只落了它当时用得到
    # 的子集 —— 少一个值的后果不是报错而是**降级成 failed**，把"已扫码，等你在
    # 手机上确认"变成"登录失败了"，用户会重扫一遍已经生效的码。
    WAITING_SCAN = "waiting_scan"
    SCANNED = "scanned"
    QRCODE_EXPIRED = "qrcode_expired"
    # 平台在扫码之后插了一屏「身份验证」，还在等我们选验证方式 —— **此刻一条
    # 短信都还没发出去**。它跟 ``sms_required`` 分开正是因为两者授权的用户文案
    # 相反：后者的意思是"把收到的码填进来"，而在选择页上这么说，用户就会对着
    # 一个从未被请求过的验证码干等到 TTL 用完（2026-08-11 实测）。
    #
    # ⚠️ 少了这个值不会报错，只会让轮询循环走进 "unhandled login status" 分支
    # 直接判死（§7.8 两侧枚举必须对齐的那条，这里是活的例子）。
    IDENTITY_CHALLENGE = "identity_challenge"
    SMS_REQUIRED = "sms_required"
    SUCCESS = "success"
    PUBLISHED = "published"
    # P1-3 回读：**"我们看过了，它没上线"**。它是一个结论，不是一次失败 ——
    # 这跟 ``failed``（"我们没问出来"）是两件事，而把两者合并正是这条链最容易
    # 犯的错：合并之后，一次容器宕机会把一整批好好的作品判成没发出去，而平台
    # 真的拒稿时又跟"浏览器挂了"长得一模一样。
    #
    # 措辞保持通道级中性（§6.1 a）："没上线"对审核中 / 被拒 / 用户删稿都成立，
    # **具体是哪一种**走 ``detail["reason"]``。
    NOT_PUBLISHED = "not_published"
    TIMEOUT = "timeout"
    PROXY_FAILED = "proxy_failed"
    FAILED = "failed"


class SessionErrorKind(str, Enum):
    """基建失败的种类 —— 出现在 ``detail["error_kind"]``。

    与 ``SessionStatus`` 正交：``error_kind`` 有值 == 我们没能得出业务
    结论，调用方**不得**据此改动账号状态。业务原因（例如账号压根没绑过
    会话）走 ``detail["reason"]``，不占用这个键。
    """

    NOT_CONFIGURED = "not_configured"  # BROWSER_SERVICE_URL / token 没配
    UNREACHABLE = "unreachable"  # 连不上容器（DNS / 拒绝连接 / 网络）
    UNAUTHORIZED = "unauthorized"  # 401/403 —— 内网 token 不匹配
    TIMEOUT = "timeout"  # 超过上界仍未返回
    SERVER_ERROR = "server_error"  # 5xx
    BAD_RESPONSE = "bad_response"  # 非 JSON / 缺字段 / 非法 status 值
    # 浏览器容器的并发槽位耗尽（browser/app/main.py 的 admission 背压）。它由
    # **对端**产生，backend 只是必须认得：不在本枚举里的 error_kind 会让
    # ``is_infra_failure`` 判 False，等于把"没排上队"当成一个真实结论。今天
    # 它恰好配 status=failed 所以不会误伤账号，但那是巧合而非保证 —— 一旦对端
    # 改成配 session_invalid，饱和的那一轮就会把账号集体标成掉线。
    POOL_SATURATED = "pool_saturated"
    # Fernet 密钥错配 / 轮换没做完。**由 repository 层产生**：
    # SocialAccountsRepository.get_with_session 解 session_state 失败时抛错，
    # 调用方（S3 发布 step / S5 巡检）捕获后调
    # session_adapter.decrypt_failure_result() 转成本 kind。
    # 它不是死枚举 —— session_adapter 只负责 json 解析，不再自己解密。
    DECRYPT_FAILED = "decrypt_failed"


# status 只是"结论"，error_kind 才是"没有结论"。此集合供 is_infra_failure 用。
_INFRA_ERROR_KINDS = frozenset(k.value for k in SessionErrorKind)

# /session/validate 允许返回的 status 值（契约已定死）。
_VALIDATE_STATUSES = frozenset(
    {
        SessionStatus.SESSION_VALID.value,
        SessionStatus.SESSION_INVALID.value,
        SessionStatus.PROXY_FAILED.value,
        SessionStatus.TIMEOUT.value,
        SessionStatus.FAILED.value,
    }
)


# /session/publish 允许返回的 status 值（契约已定死）。
# ``published`` 是 S3 才出现的终态 —— §7.8 的「已知待办」明写它必须在 S3
# 第一时间补进两侧枚举，否则**发布成功会被判成未知状态**（然后按坏响应处理，
# 于是一次真实成功的发布被记成失败，用户很可能手动重发一遍）。
_PUBLISH_STATUSES = frozenset(
    {
        SessionStatus.PUBLISHED.value,
        SessionStatus.SESSION_INVALID.value,
        SessionStatus.TIMEOUT.value,
        SessionStatus.PROXY_FAILED.value,
        SessionStatus.FAILED.value,
    }
)

# /session/verify-publish 回读允许返回的 status 值（契约已定死）。
#
# 只有 ``published`` / ``not_published`` 是**结论**；其余四个都表示"这一轮没
# 问出来"，调用方必须重试而不是下判断。这个划分写在 ``VerifyResult.conclusive``
# 里，别在调用点各写一遍。
_VERIFY_STATUSES = frozenset(
    {
        SessionStatus.PUBLISHED.value,
        SessionStatus.NOT_PUBLISHED.value,
        SessionStatus.SESSION_INVALID.value,
        SessionStatus.TIMEOUT.value,
        SessionStatus.PROXY_FAILED.value,
        SessionStatus.FAILED.value,
    }
)

# /session/inspect（只读勘探，T0）允许返回的 status 值（契约已定死）。
#
# ``session_valid`` 是唯一的成功值 —— 勘探成功意味着"我们带着这个会话打开了
# 那一页并读到了东西"，这跟会话校验的结论是同一件事，所以复用同一个枚举值而
# 不是新造一个。**不含 published / not_published**：勘探永远不对作品下判断。
_INSPECT_STATUSES = frozenset(
    {
        SessionStatus.SESSION_VALID.value,
        SessionStatus.SESSION_INVALID.value,
        SessionStatus.PROXY_FAILED.value,
        SessionStatus.TIMEOUT.value,
        SessionStatus.FAILED.value,
    }
)

# 扫码登录 (/session/login/*) 允许返回的 status 值（契约已定死）。
LOGIN_STATUSES = frozenset(
    {
        SessionStatus.WAITING_SCAN.value,
        SessionStatus.SCANNED.value,
        SessionStatus.QRCODE_EXPIRED.value,
        SessionStatus.IDENTITY_CHALLENGE.value,
        SessionStatus.SMS_REQUIRED.value,
        SessionStatus.SUCCESS.value,
        SessionStatus.TIMEOUT.value,
        SessionStatus.PROXY_FAILED.value,
        SessionStatus.FAILED.value,
    }
)

# 登录流程里"还在进行中"的状态 —— 轮询循环见到它们要继续等。
# qrcode_expired 也在其中：浏览器侧**已经自动点了刷新**并在同一响应里带回新码
# (spec §7.8)，所以它是一次画面更新，不是终点。
# identity_challenge 同理：浏览器侧**已经自己点了**「接收短信验证码」并在同一
# 响应里带回点完之后的状态，所以它也是一次画面更新，不是终点。它停在这个状态
# 只意味着"选择页还在"，浏览器侧有自己的重试上界，超了会翻成 failed。
LOGIN_PENDING_STATUSES = frozenset(
    {
        SessionStatus.WAITING_SCAN.value,
        SessionStatus.SCANNED.value,
        SessionStatus.QRCODE_EXPIRED.value,
        SessionStatus.IDENTITY_CHALLENGE.value,
        SessionStatus.SMS_REQUIRED.value,
    }
)

# 登录失败的三个终态。与 SUCCESS 一起构成轮询的出口。
LOGIN_FAILURE_STATUSES = frozenset(
    {
        SessionStatus.TIMEOUT.value,
        SessionStatus.PROXY_FAILED.value,
        SessionStatus.FAILED.value,
    }
)

# 浏览器侧在 ``detail`` 里给"这个码没被接受"打的标记（``login_sessions.py`` 的
# ``submit_sms``）。它必须能否决 ``success``：``sms_required`` 是进行中状态，
# 而"进行中 == 问到了结论 == success"这条推导对**提交验证码**这一次调用是错的
# —— 用户刚做了一个动作，动作没成，success=True 会让前端走成功分支（清空输入
# 框、不提示任何东西），这正是"验证码输错界面零反馈"的成因。
#
# 只有 ``/sms`` 的响应会带这个键：``/status`` 的轮询没人提交过码，自然也无从
# 被拒 —— 所以在共用的 ``_login_snapshot`` 里判它是安全的，第一次要码仍是
# success=True。
LOGIN_CODE_REJECTED_KEY = "code_rejected"


@dataclass(frozen=True)
class SessionOpResult:
    """spec §7.8 的结果信封。``to_dict()`` 是跨层传递的正式形态。"""

    success: bool
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "message": self.message,
            "detail": dict(self.detail),
        }

    @property
    def error_kind(self) -> Optional[str]:
        return self.detail.get("error_kind")

    @property
    def is_infra_failure(self) -> bool:
        return is_infra_failure(self.to_dict())


def is_infra_failure(result: Mapping[str, Any]) -> bool:
    """结果是否属于"我们没能问出结论"。

    巡检 / 发布链路的**必查项**：为 True 时禁止把账号标成
    ``needs_relogin``，也不该刷新 ``session_checked_at`` —— 那会把一次
    容器宕机记成"刚校验过、一切正常"。
    """
    detail = result.get("detail") or {}
    return detail.get("error_kind") in _INFRA_ERROR_KINDS


def _failure(
    status: SessionStatus,
    kind: SessionErrorKind,
    message: str,
    **extra: Any,
) -> SessionOpResult:
    detail: dict[str, Any] = {"error_kind": kind.value}
    detail.update({k: v for k, v in extra.items() if v is not None})
    return SessionOpResult(
        success=False, status=status.value, message=message, detail=detail
    )


@dataclass(frozen=True)
class SessionEnvironment:
    """每账号钉死的浏览器环境 (spec §3.2 ``account_environments``)。

    ``proxy_url`` 到这里时**必须已经是明文** —— 解密同样是 backend 的
    责任（见 ``session_adapter.build_environment``）。本类不做解密，也不
    把 proxy_url 放进任何 repr/log 路径以外的地方。
    """

    proxy_url: Optional[str] = None
    user_agent: Optional[str] = None
    locale: str = DEFAULT_LOCALE
    timezone_id: str = DEFAULT_TIMEZONE_ID
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None

    def to_payload(self) -> dict[str, Any]:
        """契约固定的 6 个键 —— 全部显式发出（含 null），浏览器侧不必
        猜缺省值。"""
        return {
            "proxy_url": self.proxy_url,
            "user_agent": self.user_agent,
            "locale": self.locale or DEFAULT_LOCALE,
            "timezone_id": self.timezone_id or DEFAULT_TIMEZONE_ID,
            "geo_lat": self.geo_lat,
            "geo_lng": self.geo_lng,
        }

    def __repr__(self) -> str:  # pragma: no cover - 防呆
        # proxy_url 含账密，绝不进 repr（repr 会被 loguru 的 f-string 带出去）
        return (
            f"SessionEnvironment(proxy={'set' if self.proxy_url else 'none'}, "
            f"locale={self.locale!r}, timezone_id={self.timezone_id!r}, "
            f"geo={'set' if self.geo_lat is not None else 'none'})"
        )


@dataclass(frozen=True)
class LoginSnapshot:
    """扫码登录的一次状态快照 —— ``/start`` / ``/status`` / ``/sms`` 共用。

    信封仍然是 ``SessionOpResult``（§7.8），额外三个字段是**画面**：二维码图片
    与它的失效时刻。它们随每次快照一起回来，因为 ``qrcode_expired`` 时浏览器侧
    会自动刷新并带回新码 —— 若二维码只在 ``/start`` 返回一次，刷新后的码就没有
    通路送到前端，用户会一直盯着一张已经作废的图。
    """

    result: SessionOpResult
    login_session_id: Optional[str] = None
    qrcode_data_url: Optional[str] = None
    expires_at: Optional[str] = None

    @property
    def status(self) -> str:
        return self.result.status

    @property
    def message(self) -> str:
        return self.result.message

    @property
    def detail(self) -> dict[str, Any]:
        return dict(self.result.detail)

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def is_infra_failure(self) -> bool:
        return self.result.is_infra_failure

    def __repr__(self) -> str:  # pragma: no cover - 防呆
        # 二维码是几十 KB 的 base64，且扫一下就等于把一个平台账号绑进本系统 ——
        # 它既撑爆日志也不该进日志。只报"有没有"。
        return (
            f"LoginSnapshot(status={self.status!r}, "
            f"session={'set' if self.login_session_id else 'none'}, "
            f"qrcode={'set' if self.qrcode_data_url else 'none'}, "
            f"expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True)
class LoginState:
    """``GET /session/login/{id}/state`` —— 登录成功后取会话物料。

    ``storage_state`` 是**明文凭证**。它到 backend 只为了立刻走
    ``secret_box.encrypt`` 入库（spec §7.6：不落盘、不进日志、不进 DBOS 的
    input/output）。本类的 ``__repr__`` 因此把它整个抹掉 —— loguru 的 f-string
    会把 repr 带进日志，这是最容易的泄漏路径。
    """

    result: SessionOpResult
    storage_state: Optional[dict[str, Any]] = None
    platform_user_id: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    # 抖音号 / 小红书号 —— 显示用,不参与账号唯一键(mig 414)。
    platform_handle: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def status(self) -> str:
        return self.result.status

    def __repr__(self) -> str:  # pragma: no cover - 防呆
        return (
            f"LoginState(status={self.status!r}, "
            f"storage_state={'set' if self.storage_state else 'none'}, "
            f"platform_user_id={self.platform_user_id!r}, "
            f"username={self.username!r})"
        )


@dataclass(frozen=True)
class PublishResult:
    """``POST /session/publish`` 的传输层结果 —— 信封 + 三个发布专属字段。

    与 ``LoginState`` 同款分层：本模块拥有**传输形态**，业务形态是
    ``session_adapter.PublishOutcome``（同样三个字段，由 adapter 包一层）。
    两者不合并，是为了让"浏览器回了什么"与"业务该怎么办"各有一个可独立
    演进的类型 —— 例如将来加平台侧统计字段时，只动这里不惊动业务层。

    ``updated_storage_state`` 是**明文凭证**（spec §7.6）：只允许流向
    ``secret_box.encrypt`` 后入库。``__repr__`` 因此把它整个抹掉 —— loguru
    的 f-string 会把 repr 带进日志，是最容易的泄漏路径。
    """

    result: SessionOpResult
    platform_item_id: Optional[str] = None
    published_url: Optional[str] = None
    updated_storage_state: Optional[dict[str, Any]] = None

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def status(self) -> str:
        return self.result.status

    @property
    def is_infra_failure(self) -> bool:
        return self.result.is_infra_failure

    def __repr__(self) -> str:  # pragma: no cover - 防呆
        return (
            f"PublishResult(status={self.status!r}, "
            f"platform_item_id={self.platform_item_id!r}, "
            f"published_url={'set' if self.published_url else 'none'}, "
            f"updated_storage_state="
            f"{'set' if self.updated_storage_state else 'none'})"
        )


@dataclass(frozen=True)
class VerifyResult:
    """``POST /session/verify-publish`` 的传输层结果 (P1-3)。

    与 ``PublishResult`` 同形，因为调用方"作品上线了、这是它的 URL 和 id"的
    落库路径不该因为消息来自回读而不是来自发布就再实现一遍。

    ``conclusive`` 是本类存在的理由。回读只有两种**结论**（上线 / 没上线），
    其余一律是"这一轮没问出来"。把这个判断放在类型上而不是每个调用点各写一
    次 ``status in (...)``，是因为写错的那一次代价极不对称：把"没问出来"当成
    "没上线"，就会因为一次容器宕机把一批好好的作品挂成待办事故。
    """

    result: SessionOpResult
    platform_item_id: Optional[str] = None
    published_url: Optional[str] = None
    updated_storage_state: Optional[dict[str, Any]] = None

    @property
    def status(self) -> str:
        return self.result.status

    @property
    def is_live(self) -> bool:
        """平台上真的能看到这条作品。"""
        return self.status == SessionStatus.PUBLISHED.value

    @property
    def is_not_live(self) -> bool:
        """我们看过了 —— 它没上线（审核中 / 被拒 / 找不到）。"""
        return self.status == SessionStatus.NOT_PUBLISHED.value

    @property
    def conclusive(self) -> bool:
        return self.is_live or self.is_not_live

    @property
    def reason(self) -> Optional[str]:
        return self.result.detail.get("reason")

    def __repr__(self) -> str:  # pragma: no cover - 防呆
        return (
            f"VerifyResult(status={self.status!r}, reason={self.reason!r}, "
            f"platform_item_id={self.platform_item_id!r}, "
            f"published_url={'set' if self.published_url else 'none'}, "
            f"updated_storage_state="
            f"{'set' if self.updated_storage_state else 'none'})"
        )


@dataclass(frozen=True)
class InspectResult:
    """``POST /session/inspect`` 的传输层结果（只读勘探，T0）。

    ``observation`` 是浏览器侧那份**受控摘要**（文案计数 / 选择器计数 / input
    属性 / 截断过的可见文本），原样透传。这里刻意不做任何"解读" —— 勘探的
    产出是数字，谁调它谁解读；在传输层塞判断逻辑会让下一个人以为那些结论是
    平台说的。

    ``updated_storage_state`` 与发布 / 回读同理：一次已认证的页面加载会让平台
    下发新 cookie，不写回等于让勘探**净消耗**会话寿命。
    """

    result: SessionOpResult
    observation: dict[str, Any] = field(default_factory=dict)
    updated_storage_state: Optional[dict[str, Any]] = None

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def status(self) -> str:
        return self.result.status

    def __repr__(self) -> str:  # pragma: no cover - 防呆
        # observation 可能有几 KB 的页面文本，绝不进 repr（repr 会被 loguru
        # 的 f-string 带进日志）。
        return (
            f"InspectResult(status={self.status!r}, "
            f"observation_keys={sorted(self.observation)}, "
            f"updated_storage_state="
            f"{'set' if self.updated_storage_state else 'none'})"
        )


@dataclass(frozen=True)
class BrowserHealth:
    """``GET /healthz`` 的类型化结果 —— 同样不抛异常。

    ``ok`` 只在 HTTP 通、且 ``status=='ok'`` 且 ``browser_ready`` 为真时
    成立。CLAUDE.md「验收纪律」：探针必须探真信号 —— 进程活着不等于浏览器
    能用，所以 ``browser_ready`` 参与判定而不是仅作展示。
    """

    ok: bool
    browser_ready: bool = False
    xvfb: bool = False
    version: str = ""
    error_kind: Optional[str] = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "browser_ready": self.browser_ready,
            "xvfb": self.xvfb,
            "version": self.version,
            "error_kind": self.error_kind,
            "message": self.message,
        }


class _TransportFailure(Exception):
    """内部信号 —— 在 ``_call`` 内抛出，公开方法统一转成类型化结果。
    绝不逃逸出本模块。"""

    def __init__(
        self,
        kind: SessionErrorKind,
        message: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.extra = extra
        # 错误响应的 JSON body（若有）。浏览器侧的登录端点在 4xx/5xx 上仍然
        # 回一个**类型化信封**（如 502 + status=proxy_failed），丢掉它就会把
        # "代理不通"降级成"browser service returned HTTP 502"，而这正是
        # §7.8 反复强调不能混的两类东西。放在独立属性而不是 extra 里：extra
        # 会整个进 detail，把一坨响应体塞进日志与 UI 不是我们要的。
        self.body = body


class BrowserClient:
    """对 nous-browser 的窄客户端。

    设计取舍：
    - **不抛传输异常**。每个公开方法都返回类型化结果，因为调用方（巡检
      循环、发布 step）需要按种类分支，而不是 catch 一个笼统 Exception 后
      失去信息（CLAUDE.md：silent no-op / 笼统吞错 不可接受）。
      参数用错（例如 storage_state 不是 dict）仍然 raise ``ValueError``
      —— 那是编程错误，不是运行时状态。
    - **不重试**。重试口径归上层：会话校验的"重试 3 次"是**浏览器侧**
      在同一个 context 内做的（§7.1 三件套），在这里再叠一层会变成开 3 次
      浏览器，代价完全不同。
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        validate_timeout: float = DEFAULT_VALIDATE_TIMEOUT_SECONDS,
        health_timeout: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        publish_timeout: float = DEFAULT_PUBLISH_TIMEOUT_SECONDS,
    ) -> None:
        from app.core.config import settings

        raw_base = base_url if base_url is not None else settings.BROWSER_SERVICE_URL
        raw_token = token if token is not None else settings.BROWSER_INTERNAL_TOKEN
        self._base_url = (raw_base or "").rstrip("/")
        self._token = raw_token or ""
        self._validate_timeout = validate_timeout
        self._health_timeout = health_timeout
        self._connect_timeout = connect_timeout
        self._publish_timeout = publish_timeout

    # ── 配置 ────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """URL 与 token 都在才算配好。

        token 缺失**不降级为不鉴权调用** —— 浏览器服务持有解密后的会话
        (spec §8.3)，宁可 fail loud 也不能裸奔。
        """
        return bool(self._base_url) and bool(self._token)

    def _not_configured_message(self) -> str:
        missing = []
        if not self._base_url:
            missing.append("BROWSER_SERVICE_URL")
        if not self._token:
            missing.append("BROWSER_INTERNAL_TOKEN")
        return f"browser service not configured (missing: {', '.join(missing)})"

    def _headers(self) -> dict[str, str]:
        return {
            INTERNAL_TOKEN_HEADER: self._token,
            "Content-Type": "application/json",
        }

    def _timeout(self, read: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._connect_timeout,
            read=read,
            write=DEFAULT_WRITE_TIMEOUT_SECONDS,
            pool=self._connect_timeout,
        )

    # ── 传输 ────────────────────────────────────────────────

    async def _call(
        self,
        method: str,
        path: str,
        *,
        read_timeout: float,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """发一次请求，返回解析后的 JSON dict。

        失败一律抛 ``_TransportFailure``（带 ``SessionErrorKind``），由公开
        方法转成类型化结果。**请求体不进日志** —— payload 里有明文
        storage_state。
        """
        if not self.is_configured:
            raise _TransportFailure(
                SessionErrorKind.NOT_CONFIGURED, self._not_configured_message()
            )
        url = f"{self._base_url}{path}"
        try:
            # trust_env=False：nous-browser 只在 docker 内网可达
            # (spec §2.2「不映射宿主机端口」)，把它走进宿主机的 HTTP(S)_PROXY
            # 永远是错的 —— 代理会解析不到容器名，或把内网流量送出去。
            # 业务出口代理是**每账号**的，走 Playwright 的 proxy 参数
            # (account_environments.proxy_url)，与系统代理层无关 (spec §5.3)。
            async with httpx.AsyncClient(
                timeout=self._timeout(read_timeout), trust_env=False
            ) as client:
                response = await client.request(
                    method, url, headers=self._headers(), json=payload
                )
        except httpx.TimeoutException as exc:
            raise _TransportFailure(
                SessionErrorKind.TIMEOUT,
                f"browser service timed out after {read_timeout}s",
                timeout_seconds=read_timeout,
            ) from exc
        except httpx.HTTPError as exc:
            # ConnectError / ReadError / 协议错误 —— 容器没起来或内网不通。
            # 只 log 异常类型，不 log 消息（可能带 URL 之外的上下文）。
            raise _TransportFailure(
                SessionErrorKind.UNREACHABLE,
                f"browser service unreachable ({type(exc).__name__})",
            ) from exc

        code = response.status_code
        if code in (401, 403):
            raise _TransportFailure(
                SessionErrorKind.UNAUTHORIZED,
                f"browser service rejected internal token (HTTP {code})",
                status_code=code,
            )
        if code >= 400:
            # 错误响应的 body 也带上（见 _TransportFailure.body）。浏览器侧的
            # 登录端点在 502/503/404 上返回的是完整的 §7.8 信封，调用方拿它
            # 才能区分「代理不通」与「我们这边挂了」。解析失败就当没有。
            error_body: Optional[dict[str, Any]] = None
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    error_body = parsed
            except (ValueError, json.JSONDecodeError):
                error_body = None
            kind = (
                SessionErrorKind.SERVER_ERROR
                if code >= 500
                else SessionErrorKind.BAD_RESPONSE
            )
            raise _TransportFailure(
                kind,
                f"browser service returned HTTP {code}",
                body=error_body,
                status_code=code,
            )
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise _TransportFailure(
                SessionErrorKind.BAD_RESPONSE,
                "browser service returned a non-JSON body",
                status_code=code,
            ) from exc
        if not isinstance(data, dict):
            raise _TransportFailure(
                SessionErrorKind.BAD_RESPONSE,
                f"browser service returned {type(data).__name__}, expected object",
                status_code=code,
            )
        return data

    # ── /healthz ────────────────────────────────────────────

    async def health(self) -> BrowserHealth:
        """``GET /healthz``。永不抛异常。"""
        try:
            data = await self._call(
                "GET", "/healthz", read_timeout=self._health_timeout
            )
        except _TransportFailure as failure:
            logger.warning(f"[browser.health] {failure.kind.value}: {failure.message}")
            return BrowserHealth(
                ok=False, error_kind=failure.kind.value, message=failure.message
            )

        browser_ready = bool(data.get("browser_ready"))
        status_ok = data.get("status") == "ok"
        ok = status_ok and browser_ready
        return BrowserHealth(
            ok=ok,
            browser_ready=browser_ready,
            xvfb=bool(data.get("xvfb")),
            version=str(data.get("version") or ""),
            error_kind=None if ok else SessionErrorKind.BAD_RESPONSE.value,
            message="ok" if ok else f"unhealthy: {data.get('status')!r}",
        )

    # ── /session/validate ───────────────────────────────────

    async def validate_session(
        self,
        platform: str,
        storage_state: Mapping[str, Any],
        environment: Optional[SessionEnvironment] = None,
    ) -> SessionOpResult:
        """``POST /session/validate``。

        ``storage_state`` 是**已解密的明文 JSON 对象**。本方法对其内部结构
        零假设 —— 只检查"是个非空对象"。这一点是给「档位 2」留的位置
        (spec §6.1 c)：小红书这类平台的会话可能主要落在 localStorage /
        origins 而不是 cookies，任何 ``storage_state["cookies"]`` 的校验都
        会把它挡在门外。

        永不抛传输异常；参数非法（storage_state 不是对象）才 raise
        ``ValueError``。
        """
        if not isinstance(storage_state, Mapping) or not storage_state:
            raise ValueError("storage_state must be a non-empty JSON object")
        env = environment or SessionEnvironment()
        payload = {
            "platform": platform,
            "storage_state": dict(storage_state),
            "environment": env.to_payload(),
        }
        try:
            data = await self._call(
                "POST",
                "/session/validate",
                read_timeout=self._validate_timeout,
                payload=payload,
            )
        except _TransportFailure as failure:
            # TIMEOUT 有对外的业务同名 status（§7.8），其余归 failed。
            status = (
                SessionStatus.TIMEOUT
                if failure.kind is SessionErrorKind.TIMEOUT
                else SessionStatus.FAILED
            )
            logger.warning(
                f"[browser.validate] platform={platform} "
                f"{failure.kind.value}: {failure.message}"
            )
            return _failure(status, failure.kind, failure.message, **failure.extra)

        raw_status = data.get("status")
        if raw_status not in _VALIDATE_STATUSES:
            # 非法状态值必须失败，不能"猜一个"——猜错就是把好账号判死。
            logger.warning(
                f"[browser.validate] platform={platform} "
                f"illegal status={raw_status!r}"
            )
            return _failure(
                SessionStatus.FAILED,
                SessionErrorKind.BAD_RESPONSE,
                f"browser service returned unknown status {raw_status!r}",
            )

        # success 由 status 推导，不取响应里的 success 字段 —— 单一真相源，
        # 免疫浏览器侧两个字段写不一致的 bug。
        success = raw_status == SessionStatus.SESSION_VALID.value
        detail = data.get("detail")
        detail = dict(detail) if isinstance(detail, Mapping) else {}
        logger.info(f"[browser.validate] platform={platform} status={raw_status}")
        return SessionOpResult(
            success=success,
            status=raw_status,
            message=str(data.get("message") or ""),
            detail=detail,
        )

    # ── /session/publish (S3 发布) ──────────────────────────

    async def publish(
        self,
        platform: str,
        storage_state: Mapping[str, Any],
        intent: Mapping[str, Any],
        environment: Optional[SessionEnvironment] = None,
    ) -> PublishResult:
        """``POST /session/publish``。

        ``intent`` 是 ``session_adapter.PublishIntent.to_payload()`` 的产物：
        平台无关的"发什么"，**不含任何执行指令**。浏览器侧按 ``platform``
        选执行档位（DOM 木偶戏 / 签名机+裸 HTTP / 外包 CLI），本模块不知道也
        不需要知道用的是哪一档（§6.1 硬要求 b）。

        与 ``validate_session`` 的两点不同：

        1. **读超时大一个量级**（``DEFAULT_PUBLISH_TIMEOUT_SECONDS``），因为
           一次调用要传完整个视频。仍然是上界，不是"等到好为止"。
        2. **回程带 ``updated_storage_state``** —— 平台会话滑动续期，这是
           spec §4.2 第 6 步"决定账号是两周还是三个月扫一次码"的那一行。
           调用方拿到后必须重新加密入库。

        永不抛传输异常；参数非法才 raise ``ValueError``。
        """
        if not isinstance(storage_state, Mapping) or not storage_state:
            raise ValueError("storage_state must be a non-empty JSON object")
        if not isinstance(intent, Mapping) or not intent:
            raise ValueError("intent must be a non-empty object")
        env = environment or SessionEnvironment()
        payload = {
            "platform": platform,
            "storage_state": dict(storage_state),
            "environment": env.to_payload(),
            "intent": dict(intent),
        }
        try:
            data = await self._call(
                "POST",
                "/session/publish",
                read_timeout=self._publish_timeout,
                payload=payload,
            )
        except _TransportFailure as failure:
            logger.warning(
                f"[browser.publish] platform={platform} "
                f"{failure.kind.value}: {failure.message}"
            )
            # 传输失败 == 我们不知道那边发出去了没有。仍然报 error_kind（基建
            # 失败 → 账号状态一律不动）—— 把一次容器重启记成"账号掉线"，会让
            # 用户为一个健康的会话重扫码。
            return PublishResult(result=self._transport_result(failure))

        raw_status = data.get("status")
        if raw_status not in _PUBLISH_STATUSES:
            # 不猜。猜错的方向两边都很坏：把 published 猜成失败会诱发重复发布，
            # 把失败猜成 published 会让用户以为发出去了。
            logger.warning(
                f"[browser.publish] platform={platform} illegal status={raw_status!r}"
            )
            return PublishResult(
                result=_failure(
                    SessionStatus.FAILED,
                    SessionErrorKind.BAD_RESPONSE,
                    f"browser service returned unknown status {raw_status!r}",
                )
            )

        # success 由 status 推导（与 validate_session / _login_snapshot 同款单一
        # 真相源）：只有 published 算成功。
        detail = data.get("detail")
        detail = dict(detail) if isinstance(detail, Mapping) else {}
        updated = data.get("updated_storage_state")
        # 空对象按"没有回传"处理：写一个空 storage_state 回库等于把账号的会话
        # 抹掉，比不写回坏得多。
        updated_state = (
            dict(updated) if isinstance(updated, Mapping) and updated else None
        )
        item_id = data.get("platform_item_id")
        published_url = data.get("published_url")
        logger.info(
            f"[browser.publish] platform={platform} status={raw_status} "
            f"state_refreshed={bool(updated_state)}"
        )
        return PublishResult(
            result=SessionOpResult(
                success=raw_status == SessionStatus.PUBLISHED.value,
                status=raw_status,
                message=str(data.get("message") or ""),
                detail=detail,
            ),
            platform_item_id=str(item_id) if item_id else None,
            published_url=str(published_url) if published_url else None,
            updated_storage_state=updated_state,
        )

    # ── /session/verify-publish (P1-3 回读) ─────────────────

    async def verify_publish(
        self,
        platform: str,
        storage_state: Mapping[str, Any],
        title: str,
        environment: Optional[SessionEnvironment] = None,
    ) -> VerifyResult:
        """``POST /session/verify-publish`` —— 去创作者中心确认作品真的上线了。

        为什么需要它：``douyin_publish`` 明确返回 ``published_url=None``，因为
        平台发布后的跳转不带作品 id，而"取列表里第一条"对任何有定时稿或并发
        发布的账号都会张冠李戴。所以发布这一步只能证明"编辑器收下了"。对定时
        发布来说，那离真相还有几个小时 —— 期间平台可以审核、拒稿、取消定时，
        用户也可以删稿。

        读超时用 ``validate`` 那一档而不是 ``publish`` 那一档：回读只有一次
        导航、没有上传，几百秒的预算是留给传视频的。

        永不抛传输异常；参数非法才 raise ``ValueError``。
        """
        if not isinstance(storage_state, Mapping) or not storage_state:
            raise ValueError("storage_state must be a non-empty JSON object")
        if not title or not title.strip():
            # 标题是回读**唯一**的抓手（我们从来没拿到过作品 id）。空标题不是
            # "查全部"，而是根本无法定位 —— 让它在这里炸，好过让浏览器白跑一趟
            # 然后返回一个看似权威的 not_found，把用户的待办挂成事故。
            raise ValueError("title must be a non-empty string")
        env = environment or SessionEnvironment()
        payload = {
            "platform": platform,
            "storage_state": dict(storage_state),
            "environment": env.to_payload(),
            "probe": {"title": title},
        }
        try:
            data = await self._call(
                "POST",
                "/session/verify-publish",
                read_timeout=self._validate_timeout,
                payload=payload,
            )
        except _TransportFailure as failure:
            logger.warning(
                f"[browser.verify] platform={platform} "
                f"{failure.kind.value}: {failure.message}"
            )
            # 传输失败 == 没问出来。``conclusive`` 因此为 False，调用方会重试
            # 而不是给作品下判断。
            return VerifyResult(result=self._transport_result(failure))

        raw_status = data.get("status")
        if raw_status not in _VERIFY_STATUSES:
            logger.warning(
                f"[browser.verify] platform={platform} illegal status={raw_status!r}"
            )
            return VerifyResult(
                result=_failure(
                    SessionStatus.FAILED,
                    SessionErrorKind.BAD_RESPONSE,
                    f"browser service returned unknown status {raw_status!r}",
                )
            )

        detail = data.get("detail")
        detail = dict(detail) if isinstance(detail, Mapping) else {}
        updated = data.get("updated_storage_state")
        updated_state = (
            dict(updated) if isinstance(updated, Mapping) and updated else None
        )
        item_id = data.get("platform_item_id")
        published_url = data.get("published_url")
        logger.info(
            f"[browser.verify] platform={platform} status={raw_status} "
            f"reason={detail.get('reason')!r}"
        )
        return VerifyResult(
            result=SessionOpResult(
                # success 由 status 推导（与其余方法同款单一真相源）。
                success=raw_status == SessionStatus.PUBLISHED.value,
                status=raw_status,
                message=str(data.get("message") or ""),
                detail=detail,
            ),
            platform_item_id=str(item_id) if item_id else None,
            published_url=str(published_url) if published_url else None,
            updated_storage_state=updated_state,
        )

    # ── /session/inspect (T0 只读勘探) ──────────────────────

    async def inspect_page(
        self,
        platform: str,
        storage_state: Mapping[str, Any],
        url: str,
        *,
        environment: Optional[SessionEnvironment] = None,
        seed_files: Optional[Sequence[Mapping[str, Any]]] = None,
        text_probes: Optional[Sequence[str]] = None,
        selector_probes: Optional[Sequence[str]] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> InspectResult:
        """``POST /session/inspect`` —— 打开一个白名单内的页面，数东西。

        存在的理由：本仓库的平台适配器全部是对着**没人能从代码里看一眼**的
        页面写的。设计规格里那一片 ``[TO-VERIFY]``、``xiaohongshu.py`` 里
        两个 ``[GUESS] UNVERIFIED`` 常量，都是同一个缺口的症状 —— 想知道
        "这句文案在页面上精确匹配几个节点"，此前只能让人开 DevTools 念出来。

        **这个方法不发布任何东西**，浏览器侧那个模块里也不存在提交路径（有
        测试逐字检查）。它能做的全部是：导航、把探针文件交给一个 file input
        （否则某些表单根本不渲染）、数匹配数、读被截断过的可见文本。

        ``url`` 的白名单在**浏览器侧**校验（对着该平台自己的 CREATOR_HOSTS）。
        不在这里预判：allow-list 只能有一处，放在真正会去访问的那一侧。

        ⚠️ **账号级串行锁不在这里** —— 它是 PG advisory lock，归 backend 侧
        持有（见 ``session_inspect.inspect_account_page``）。直接调本方法而
        不持锁，会与同账号的发布 / 巡检撞车，两个 context 互相把对方踢下线
        （spec §7.5）。

        永不抛传输异常；参数非法才 raise ``ValueError``。
        """
        if not isinstance(storage_state, Mapping) or not storage_state:
            raise ValueError("storage_state must be a non-empty JSON object")
        if not url or not url.strip():
            raise ValueError("url must be a non-empty string")
        env = environment or SessionEnvironment()
        payload: dict[str, Any] = {
            "platform": platform,
            "storage_state": dict(storage_state),
            "environment": env.to_payload(),
            "url": url.strip(),
            "seed_files": [dict(item) for item in (seed_files or [])],
            "text_probes": list(text_probes or []),
            "selector_probes": list(selector_probes or []),
        }
        # 其余旋钮（settle_ms / seed_selector / excerpt_chars / budget_s …）
        # 原样透传，由浏览器侧的 pydantic 模型兜住上下界 —— 在这里复述一遍
        # 边界值就是第二处声明，而那正是本次设计要消灭的病。
        payload.update({k: v for k, v in (options or {}).items() if v is not None})
        try:
            data = await self._call(
                "POST",
                "/session/inspect",
                read_timeout=self._publish_timeout,
                payload=payload,
            )
        except _TransportFailure as failure:
            logger.warning(
                f"[browser.inspect] platform={platform} "
                f"{failure.kind.value}: {failure.message}"
            )
            # 传输失败带着浏览器侧的信封（如果有）—— URL 被白名单拒绝时是
            # HTTP 400 + reason=url_not_allowed，那是**结论**，不该被降级成
            # 一句 "browser service returned HTTP 400"。
            if failure.body and isinstance(failure.body.get("detail"), Mapping):
                return InspectResult(
                    result=SessionOpResult(
                        success=False,
                        status=str(
                            failure.body.get("status") or SessionStatus.FAILED.value
                        ),
                        message=str(failure.body.get("message") or failure.message),
                        detail=dict(failure.body["detail"]),
                    )
                )
            return InspectResult(result=self._transport_result(failure))

        raw_status = data.get("status")
        if raw_status not in _INSPECT_STATUSES:
            logger.warning(
                f"[browser.inspect] platform={platform} illegal status={raw_status!r}"
            )
            return InspectResult(
                result=_failure(
                    SessionStatus.FAILED,
                    SessionErrorKind.BAD_RESPONSE,
                    f"browser service returned unknown status {raw_status!r}",
                )
            )

        detail = data.get("detail")
        detail = dict(detail) if isinstance(detail, Mapping) else {}
        updated = data.get("updated_storage_state")
        updated_state = (
            dict(updated) if isinstance(updated, Mapping) and updated else None
        )
        observation = {
            key: data.get(key)
            for key in (
                "url_after",
                "page_title",
                "texts",
                "selectors",
                "body_text_excerpt",
                "body_text_truncated",
                "input_summary",
                "input_total",
                "seeded_files",
            )
            if key in data
        }
        logger.info(
            f"[browser.inspect] platform={platform} status={raw_status} "
            f"probes={len(payload['text_probes'])}/"
            f"{len(payload['selector_probes'])} "
            f"state_refreshed={bool(updated_state)}"
        )
        return InspectResult(
            result=SessionOpResult(
                success=raw_status == SessionStatus.SESSION_VALID.value,
                status=raw_status,
                message=str(data.get("message") or ""),
                detail=detail,
            ),
            observation=observation,
            updated_storage_state=updated_state,
        )

    # ── /session/login/* (S2 扫码登录) ──────────────────────

    async def start_login(
        self,
        platform: str,
        environment: Optional[SessionEnvironment] = None,
    ) -> LoginSnapshot:
        """``POST /session/login/start`` —— 起一个**保活**的登录 context。

        与 ``validate_session`` 的关键区别：这一调用返回后浏览器容器里那个
        context **仍然活着**（二维码属于它，context 一销毁码就作废，spec §4.1）。
        因此调用方对返回的 ``login_session_id`` 负有释放义务 —— 成功、失败、
        取消、超时四条路径都必须走 ``close_login``。浏览器侧的 TTL 自毁只是
        进程死掉时的兜底，不是常规释放路径。

        永不抛传输异常。
        """
        env = environment or SessionEnvironment()
        payload = {"platform": platform, "environment": env.to_payload()}
        try:
            data = await self._call(
                "POST",
                "/session/login/start",
                read_timeout=DEFAULT_LOGIN_START_TIMEOUT_SECONDS,
                payload=payload,
            )
        except _TransportFailure as failure:
            typed = self._typed_failure_snapshot(failure)
            if typed is not None:
                # 浏览器侧对"起不来"的分类（proxy_failed / timeout / 容量不足）
                # 比 HTTP 码有用得多 —— 把代理故障报成"重扫二维码"是纯粹的误导。
                logger.warning(
                    f"[browser.login.start] platform={platform} "
                    f"rejected: {typed.status} {typed.message}"
                )
                return typed
            logger.warning(
                f"[browser.login.start] platform={platform} "
                f"{failure.kind.value}: {failure.message}"
            )
            return LoginSnapshot(result=self._transport_result(failure))

        snapshot = self._login_snapshot(data)
        if snapshot.success and not snapshot.login_session_id:
            # 没有 id 就无法轮询、更无法释放 —— 会永久泄漏一个 context。
            # 当作坏响应，而不是"先跑起来再说"。
            logger.warning(
                f"[browser.login.start] platform={platform} "
                "response has no login_session_id"
            )
            return LoginSnapshot(
                result=_failure(
                    SessionStatus.FAILED,
                    SessionErrorKind.BAD_RESPONSE,
                    "browser service returned no login_session_id",
                )
            )
        logger.info(
            f"[browser.login.start] platform={platform} status={snapshot.status}"
        )
        return snapshot

    async def get_login_status(self, login_session_id: str) -> LoginSnapshot:
        """``GET /session/login/{id}/status``。永不抛传输异常。

        ``qrcode_data_url`` 在 ``qrcode_expired`` 时会带回**新**码（浏览器侧
        已自动点了刷新），其余状态可能为 null —— 调用方保留上一张即可。
        """
        try:
            data = await self._call(
                "GET",
                f"/session/login/{login_session_id}/status",
                read_timeout=DEFAULT_LOGIN_POLL_TIMEOUT_SECONDS,
            )
        except _TransportFailure as failure:
            # 带类型化 body 的错误（典型：404 —— login session 已经不存在了）
            # 是**结论**，不是抖动：那个 context 真的没了，重试三轮也变不回来。
            typed = self._typed_failure_snapshot(failure, login_session_id)
            if typed is not None:
                logger.warning(f"[browser.login.status] rejected: {typed.status}")
                return typed
            # 其余传输失败**不等于**登录失败：容器重启/网络抖动都会走到这里，
            # 而用户手机上那次扫码可能已经成功。调用方按 is_infra_failure
            # 决定"再试一轮"还是"放弃"，别在这里替它决定。
            logger.warning(
                f"[browser.login.status] {failure.kind.value}: {failure.message}"
            )
            return LoginSnapshot(result=self._transport_result(failure))
        return self._login_snapshot(data, login_session_id=login_session_id)

    async def submit_login_sms(self, login_session_id: str, code: str) -> LoginSnapshot:
        """``POST /session/login/{id}/sms`` —— 提交短信验证码。

        返回的仍是状态快照：验证码没被接受时浏览器侧回 ``sms_required`` +
        ``detail.code_rejected=True``，而不是 HTTP 4xx —— 这样"码没过"和
        "容器挂了"在调用方看来是两件不同的事（§7.8 的整条设计）。

        ⚠️ 那个标记不是装饰：光看 status 分不出"第一次要码"和"你给的码没过"
        （两者都是 ``sms_required``），而 ``_login_snapshot`` 正是靠它把
        ``success`` 判成 False。丢掉它，这次调用就会以 success=True 回到前端，
        用户输错验证码将得到**零反馈**。
        """
        if not (code or "").strip():
            raise ValueError("sms code must be a non-empty string")
        try:
            data = await self._call(
                "POST",
                f"/session/login/{login_session_id}/sms",
                read_timeout=DEFAULT_LOGIN_POLL_TIMEOUT_SECONDS,
                payload={"code": code},
            )
        except _TransportFailure as failure:
            typed = self._typed_failure_snapshot(failure, login_session_id)
            if typed is not None:
                logger.warning(f"[browser.login.sms] rejected: {typed.status}")
                return typed
            logger.warning(
                f"[browser.login.sms] {failure.kind.value}: {failure.message}"
            )
            return LoginSnapshot(result=self._transport_result(failure))
        # 验证码本身绝不进日志。
        snapshot = self._login_snapshot(data, login_session_id=login_session_id)
        logger.info(f"[browser.login.sms] status={snapshot.status}")
        return snapshot

    async def get_login_state(self, login_session_id: str) -> LoginState:
        """``GET /session/login/{id}/state`` —— 取登录成功后的会话物料。

        只在 ``status == success`` 之后调用。返回的 ``storage_state`` 是明文
        凭证，调用方必须在同一个作用域内加密入库，**不得**放进 DBOS step 的
        返回值（会被持久化进引擎表，spec §7.6）。
        """
        try:
            data = await self._call(
                "GET",
                f"/session/login/{login_session_id}/state",
                read_timeout=DEFAULT_LOGIN_STATE_TIMEOUT_SECONDS,
            )
        except _TransportFailure as failure:
            # 409 + ``status=failed`` + ``detail.reason=identity_unresolved`` 是
            # 浏览器侧的**通道结论**,不是传输故障:它认得这一页、也确实登录了,
            # 只是拿不到身份 cookie,于是拒绝造一个身份不明的账号。丢掉 body
            # 会把这句话压成 "browser service returned HTTP 409",用户看到的
            # 就只剩一个数字 —— 与 §7.8「HTTP 码表达传输层,body 表达结论」
            # 相悖,也正是仓库里「触发路径必须类型化失败回显」那条纪律。
            typed = self._typed_failure_snapshot(failure, login_session_id)
            if typed is not None:
                logger.warning(
                    f"[browser.login.state] {typed.status}: "
                    f"reason={typed.result.detail.get('reason')}"
                )
                return LoginState(result=typed.result)
            logger.warning(
                f"[browser.login.state] {failure.kind.value}: {failure.message}"
            )
            return LoginState(result=self._transport_result(failure))

        storage_state = data.get("storage_state")
        if not isinstance(storage_state, Mapping) or not storage_state:
            # 到这一步没有会话物料 == 白扫了一次码。必须失败，不能建一个
            # session_state 为空的账号行 —— 那种账号在发布时才会暴露，
            # 而那时用户已经以为绑定成功了。
            logger.warning("[browser.login.state] response has no storage_state")
            return LoginState(
                result=_failure(
                    SessionStatus.FAILED,
                    SessionErrorKind.BAD_RESPONSE,
                    "browser service returned no storage_state",
                )
            )
        platform_user_id = data.get("platform_user_id")
        if not platform_user_id:
            # 没有平台用户 id 就没有 upsert 的自然键，重扫会建出重复账号行。
            logger.warning("[browser.login.state] response has no platform_user_id")
            return LoginState(
                result=_failure(
                    SessionStatus.FAILED,
                    SessionErrorKind.BAD_RESPONSE,
                    "browser service returned no platform_user_id",
                )
            )
        return LoginState(
            result=SessionOpResult(
                success=True,
                status=SessionStatus.SUCCESS.value,
                message=str(data.get("message") or "ok"),
                detail={},
            ),
            storage_state=dict(storage_state),
            platform_user_id=str(platform_user_id),
            username=str(data.get("username") or "") or None,
            avatar_url=str(data.get("avatar_url") or "") or None,
            platform_handle=str(data.get("platform_handle") or "") or None,
        )

    async def close_login(self, login_session_id: str) -> bool:
        """``POST /session/login/{id}/close`` —— 释放浏览器 context。

        返回是否**确认**释放。永不抛异常，因为它总是在 ``finally`` 里被调用：
        清理失败绝不能盖掉正在传播的真实错误。返回 False 只意味着"我们没能
        确认"，浏览器侧的 TTL 自毁仍会兜底（spec §4.1）。
        """
        try:
            data = await self._call(
                "POST",
                f"/session/login/{login_session_id}/close",
                read_timeout=DEFAULT_LOGIN_CLOSE_TIMEOUT_SECONDS,
            )
        except _TransportFailure as failure:
            if failure.extra.get("status_code") == 404:
                # 浏览器侧不认识这个 id == 那个 context 已经不存在（TTL 自毁、
                # 或者 workflow 的 finally 先关过一次）。这就是"已释放"，
                # 报 False 会让取消端点对用户说一句吓人的"释放未确认"。
                logger.info("[browser.login.close] already released")
                return True
            logger.warning(
                f"[browser.login.close] {failure.kind.value}: {failure.message}"
            )
            return False
        closed = bool(data.get("closed"))
        logger.info(f"[browser.login.close] closed={closed}")
        return closed

    # ── 登录响应的公共映射 ──────────────────────────────────

    @staticmethod
    def _typed_failure_snapshot(
        failure: _TransportFailure, login_session_id: Optional[str] = None
    ) -> Optional[LoginSnapshot]:
        """4xx/5xx 的 body 里若带着合法的通道 status，就以它为准。

        浏览器侧对登录端点的约定是"HTTP 码表达传输层，body 表达通道结论"：
        502 + ``proxy_failed``、503 + 容量不足、404 + login session 不存在。
        只看 HTTP 码会把这三种都塌缩成"browser service returned HTTP 5xx"
        + ``error_kind``，于是它们全部变成"基建失败"—— 而基建失败的语义是
        "我们没能问出结论、别动账号状态"，正好把三个**确定**的结论丢掉。

        401/403 不走这里（token 错配没有通道结论可言），因为它抛的是
        UNAUTHORIZED 且 body 里没有 status 字段。
        """
        body = failure.body
        if not isinstance(body, Mapping):
            return None
        if body.get("status") not in LOGIN_STATUSES:
            return None
        return BrowserClient._login_snapshot(body, login_session_id=login_session_id)

    @staticmethod
    def _transport_result(failure: _TransportFailure) -> SessionOpResult:
        """传输失败 → §7.8 信封。TIMEOUT 有同名业务 status，其余归 failed。"""
        status = (
            SessionStatus.TIMEOUT
            if failure.kind is SessionErrorKind.TIMEOUT
            else SessionStatus.FAILED
        )
        return _failure(status, failure.kind, failure.message, **failure.extra)

    @staticmethod
    def _login_snapshot(
        data: Mapping[str, Any], *, login_session_id: Optional[str] = None
    ) -> LoginSnapshot:
        """登录响应 JSON → ``LoginSnapshot``。

        非法 status 一律判 BAD_RESPONSE 而不猜 —— 猜错的代价是把"等你确认"
        显示成"失败了"。``success`` 由 status 推导（单一真相源，与
        ``validate_session`` 同款）：进行中的四个状态都算"问到了结论"，
        只有三个失败终态才是 False。

        唯一的例外是 ``detail.code_rejected``（见 ``LOGIN_CODE_REJECTED_KEY``）：
        它是浏览器侧对"用户刚提交的这个码没被接受"的类型化标记，status 仍是
        ``sms_required``（页面确实还在要码），但对调用方而言这次操作**失败了**。
        """
        raw_status = data.get("status")
        if raw_status not in LOGIN_STATUSES:
            logger.warning(f"[browser.login] illegal status={raw_status!r}")
            return LoginSnapshot(
                result=_failure(
                    SessionStatus.FAILED,
                    SessionErrorKind.BAD_RESPONSE,
                    f"browser service returned unknown status {raw_status!r}",
                )
            )
        detail = data.get("detail")
        detail = dict(detail) if isinstance(detail, Mapping) else {}
        qrcode = data.get("qrcode_data_url")
        expires_at = data.get("expires_at")
        code_rejected = detail.get(LOGIN_CODE_REJECTED_KEY) is True
        return LoginSnapshot(
            result=SessionOpResult(
                success=raw_status not in LOGIN_FAILURE_STATUSES and not code_rejected,
                status=raw_status,
                message=str(data.get("message") or ""),
                detail=detail,
            ),
            login_session_id=str(data.get("login_session_id") or login_session_id or "")
            or None,
            qrcode_data_url=str(qrcode) if qrcode else None,
            expires_at=str(expires_at) if expires_at else None,
        )


__all__ = [
    "DEFAULT_LOCALE",
    "DEFAULT_TIMEZONE_ID",
    "DEFAULT_VALIDATE_TIMEOUT_SECONDS",
    "DEFAULT_LOGIN_START_TIMEOUT_SECONDS",
    "DEFAULT_LOGIN_POLL_TIMEOUT_SECONDS",
    "DEFAULT_LOGIN_STATE_TIMEOUT_SECONDS",
    "DEFAULT_LOGIN_CLOSE_TIMEOUT_SECONDS",
    "DEFAULT_PUBLISH_TIMEOUT_SECONDS",
    "INTERNAL_TOKEN_HEADER",
    "LOGIN_CODE_REJECTED_KEY",
    "LOGIN_FAILURE_STATUSES",
    "LOGIN_PENDING_STATUSES",
    "LOGIN_STATUSES",
    "BrowserClient",
    "BrowserHealth",
    "InspectResult",
    "LoginSnapshot",
    "LoginState",
    "PublishResult",
    "SessionEnvironment",
    "SessionErrorKind",
    "SessionOpResult",
    "SessionStatus",
    "VerifyResult",
    "is_infra_failure",
]
