"""Distribution adapter 注册表 —— OAuth 与 Session 两类适配器的解析入口。

两类适配器形状不同（OAuth 有 authorize/exchange/refresh，Session 有
storage_state），所以不共享一个基类，而是在这里按 ``auth_type`` 分流。

**``get_adapter(platform, creds)`` 的语义保持原样不变**：它是 OAuth 通道
的解析器，现有 4 处 ``distribution_router`` 调用与 ``publish_distribution``
的调用无需改动。会话通道走新增的 ``get_session_adapter`` /
``resolve_adapter``，两者纯增量。
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.distribution.douyin_adapter import DouyinAdapter, DouyinCredentials
from app.services.distribution.platform_base import PlatformAdapter
from app.services.distribution.session_adapter import (
    AUTH_TYPE_SESSION,
    SessionAdapter,
    supported_session_platforms,
)

AUTH_TYPE_OAUTH = "oauth"

_ADAPTERS = {"douyin": DouyinAdapter}


def get_adapter(platform: str, creds: DouyinCredentials) -> PlatformAdapter:
    """OAuth 通道适配器（official / h5）。签名与语义与 PR-D2 完全一致。"""
    cls = _ADAPTERS.get(platform)
    if cls is None:
        raise ValueError(f"Unsupported platform: {platform}")
    return cls(creds)


def supports_session(platform: str) -> bool:
    """该平台是否已接入会话通道。"""
    return platform in supported_session_platforms()


def get_session_adapter(platform: str) -> SessionAdapter:
    """Session 通道适配器。

    不收 ``creds``：会话通道的凭证是账号自己的 storage_state（在
    ``social_accounts.session_state``），不是应用级的 client_key/secret。
    """
    return SessionAdapter(platform)


def resolve_adapter(
    platform: str,
    *,
    auth_type: str = AUTH_TYPE_OAUTH,
    creds: Optional[DouyinCredentials] = None,
) -> Any:
    """按 ``auth_type`` 分流的统一入口 —— 给同时可能拿到两类账号的调用方
    （发布 step、巡检）用。

    返回 ``PlatformAdapter`` 或 ``SessionAdapter``；两者形状不同，调用方
    必须自己知道拿到的是哪一类（这正是不给它们编一个假共同基类的原因）。
    """
    if auth_type == AUTH_TYPE_SESSION:
        return get_session_adapter(platform)
    if creds is None:
        raise ValueError(f"OAuth adapter for {platform} requires credentials")
    return get_adapter(platform, creds)


__all__ = [
    "AUTH_TYPE_OAUTH",
    "AUTH_TYPE_SESSION",
    "get_adapter",
    "get_session_adapter",
    "resolve_adapter",
    "supports_session",
]
