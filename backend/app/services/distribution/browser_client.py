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
from typing import Any, Mapping, Optional

import httpx
from loguru import logger

# ── 超时 ─────────────────────────────────────────────────────
# 校验会真的开一个有头浏览器（Xvfb）、加载 storage_state、goto 平台页面并
# 重试 3 次（§7.1），90s 是给足余量后的**上界** —— 不是"等到好为止"。
# §7.2「所有轮询必须有上界」的同族纪律：任何等待都必须能超时收敛。
DEFAULT_VALIDATE_TIMEOUT_SECONDS = 90.0
DEFAULT_HEALTH_TIMEOUT_SECONDS = 5.0
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
    QRCODE_EXPIRED = "qrcode_expired"
    SMS_REQUIRED = "sms_required"
    PUBLISHED = "published"
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

    def __init__(self, kind: SessionErrorKind, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.extra = extra


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
    ) -> None:
        from app.core.config import settings

        raw_base = base_url if base_url is not None else settings.BROWSER_SERVICE_URL
        raw_token = token if token is not None else settings.BROWSER_INTERNAL_TOKEN
        self._base_url = (raw_base or "").rstrip("/")
        self._token = raw_token or ""
        self._validate_timeout = validate_timeout
        self._health_timeout = health_timeout
        self._connect_timeout = connect_timeout

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
        if code >= 500:
            raise _TransportFailure(
                SessionErrorKind.SERVER_ERROR,
                f"browser service returned HTTP {code}",
                status_code=code,
            )
        if code >= 400:
            raise _TransportFailure(
                SessionErrorKind.BAD_RESPONSE,
                f"browser service returned HTTP {code}",
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


__all__ = [
    "DEFAULT_LOCALE",
    "DEFAULT_TIMEZONE_ID",
    "DEFAULT_VALIDATE_TIMEOUT_SECONDS",
    "INTERNAL_TOKEN_HEADER",
    "BrowserClient",
    "BrowserHealth",
    "SessionEnvironment",
    "SessionErrorKind",
    "SessionOpResult",
    "SessionStatus",
    "is_infra_failure",
]
