"""SessionAdapter —— 解析会话 → 调 nous-browser → 类型化结果映射。

浏览器服务全程用 fake 客户端替身；这里验的是解密职责的归属
（``session_state`` 归 repository、``proxy_url`` 归 adapter）、结果分流
（业务失败 vs 基建失败）、以及 spec §6.1 三条抽象硬要求确实成立。

``session_state`` 在这些夹具里是**明文 JSON 串** —— 那正是
``SocialAccountsRepository.get_with_session`` 的输出契约。
"""

from __future__ import annotations

import json

import pytest

from app.core import secret_box
from app.services.distribution.browser_client import (
    SessionEnvironment,
    SessionErrorKind,
    SessionOpResult,
    SessionStatus,
    is_infra_failure,
)
from app.services.distribution.registry import (
    get_adapter,
    get_session_adapter,
    resolve_adapter,
    supports_session,
)
from app.services.distribution.session_adapter import (
    PublishIntent,
    PublishMedia,
    SessionAdapter,
    build_environment,
    decrypt_failure_result,
    parse_session_state,
)

STORAGE_STATE = {"cookies": [{"name": "sessionid", "value": "s3cr3t"}], "origins": []}


class FakeBrowserClient:
    """记录调用参数、返回预设结果。"""

    def __init__(self, result: SessionOpResult | None = None):
        self.result = result or SessionOpResult(
            success=True, status=SessionStatus.SESSION_VALID.value, message="ok"
        )
        self.calls: list[dict] = []

    async def validate_session(self, platform, storage_state, environment=None):
        self.calls.append(
            {
                "platform": platform,
                "storage_state": storage_state,
                "environment": environment,
            }
        )
        return self.result


def _account(**over) -> dict:
    base = {
        "id": "701",
        "platform": "douyin",
        "auth_type": "session",
        # repository 已解密 —— adapter 拿到的就是明文 JSON 串
        "session_state": json.dumps(STORAGE_STATE),
    }
    base.update(over)
    return base


def _adapter(client: FakeBrowserClient | None = None) -> SessionAdapter:
    return SessionAdapter("douyin", client=client or FakeBrowserClient())


# ── session_state 解析（解密归 repository） ──────────────────


def test_parse_session_state_reads_repository_plaintext():
    assert parse_session_state(_account()) == STORAGE_STATE


def test_parse_never_calls_secret_box(monkeypatch):
    """解密只在 repository 发生一次。这里再兜一层的话，等 secret_box 的
    legacy 明文透传分支被删（其注释写明"迟早不会再触发"），会话通道会突然
    全线崩溃且报错点落在 adapter，排查要绕一圈。"""

    def _boom(*a, **k):  # pragma: no cover - 触发即失败
        raise AssertionError("session_adapter must not decrypt session_state")

    monkeypatch.setattr(secret_box, "decrypt", _boom)
    assert parse_session_state(_account()) == STORAGE_STATE


def test_parse_missing_session_state_is_business_reason():
    from app.services.distribution.session_adapter import SessionStateError

    with pytest.raises(SessionStateError) as exc:
        parse_session_state({"id": "1"})
    assert exc.value.reason == "no_session_state"


def test_parse_non_json_is_malformed():
    from app.services.distribution.session_adapter import SessionStateError

    with pytest.raises(SessionStateError) as exc:
        parse_session_state({"id": "1", "session_state": "not json at all"})
    assert exc.value.reason == "malformed_session_state"


def test_parse_non_object_payload_is_malformed():
    from app.services.distribution.session_adapter import SessionStateError

    with pytest.raises(SessionStateError) as exc:
        parse_session_state({"id": "1", "session_state": "[]"})
    assert exc.value.reason == "malformed_session_state"


def test_decrypt_failure_result_is_infra_never_session_invalid():
    """DECRYPT_FAILED 的唯一生产入口 —— repository 解密失败时调用方用它。
    密钥错配下 100 个账号绝不能被集体标 needs_relogin。"""
    result = decrypt_failure_result("bad key", account_id="701")

    assert result["status"] == SessionStatus.FAILED.value
    assert result["status"] != SessionStatus.SESSION_INVALID.value
    assert result["detail"]["error_kind"] == SessionErrorKind.DECRYPT_FAILED.value
    assert is_infra_failure(result) is True


async def test_validate_passes_plaintext_object_to_browser():
    """浏览器服务拿到的是解析好的 storage_state 对象，密钥永远不出 backend。"""
    client = FakeBrowserClient()
    await _adapter(client).validate_session(_account())

    sent = client.calls[0]["storage_state"]
    assert sent == STORAGE_STATE
    assert client.calls[0]["platform"] == "douyin"


# ── 结果映射 ────────────────────────────────────────────────


async def test_validate_valid_returns_78_envelope():
    result = await _adapter().validate_session(_account())
    assert set(result) == {"success", "status", "message", "detail"}
    assert result["success"] is True
    assert result["status"] == "session_valid"


async def test_validate_missing_session_state_maps_to_session_invalid():
    """没绑过会话 → 让用户去扫码，是正确的 UX 结论。"""
    result = await _adapter().validate_session(_account(session_state=None))

    assert result["status"] == SessionStatus.SESSION_INVALID.value
    assert result["detail"]["reason"] == "no_session_state"
    assert is_infra_failure(result) is False


async def test_validate_malformed_session_state_maps_to_session_invalid():
    client = FakeBrowserClient()
    result = await _adapter(client).validate_session(_account(session_state="{broken"))

    assert result["status"] == SessionStatus.SESSION_INVALID.value
    assert result["detail"]["reason"] == "malformed_session_state"
    assert is_infra_failure(result) is False
    # 会话都解析不出来，不该白开一次浏览器
    assert client.calls == []


async def test_validate_oauth_account_returns_typed_failure_not_silent_noop():
    client = FakeBrowserClient()
    result = await _adapter(client).validate_session(_account(auth_type="oauth"))

    assert result["success"] is False
    assert result["detail"]["reason"] == "auth_type_mismatch"
    assert client.calls == []


async def test_validate_forwards_browser_failure_verbatim():
    client = FakeBrowserClient(
        SessionOpResult(
            success=False,
            status=SessionStatus.PROXY_FAILED.value,
            message="proxy auth failed",
            detail={"exit_ip": None},
        )
    )
    result = await _adapter(client).validate_session(_account())
    assert result["status"] == "proxy_failed"
    assert result["message"] == "proxy auth failed"


# ── 环境组装 ────────────────────────────────────────────────


def test_build_environment_decrypts_proxy_url():
    env = build_environment(
        {
            "account_id": "701",
            "proxy_url": secret_box.encrypt("http://u:p@proxy:8080"),
            "user_agent": "UA/2",
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "geo_lat": 39.9,
            "geo_lng": 116.4,
        }
    )
    assert env.proxy_url == "http://u:p@proxy:8080"
    assert env.user_agent == "UA/2"
    assert env.geo_lat == 39.9


def test_build_environment_none_row_is_direct_connection_defaults():
    env = build_environment(None)
    assert env.proxy_url is None
    assert env.locale == "zh-CN"
    assert env.timezone_id == "Asia/Shanghai"


def test_build_environment_undecryptable_proxy_falls_back_to_direct():
    env = build_environment({"account_id": "1", "proxy_url": "gAAAAA" + "x" * 60})
    assert env.proxy_url is None


def _joined_env_row(**over) -> dict:
    """``get_with_session`` 的 ``environment`` 值：``account_environments``
    的原始列名 (spec §3.2)，``proxy_url`` 保持密文（解密归 adapter）。"""
    row = {
        "account_id": "701",
        "proxy_url": secret_box.encrypt("http://u:p@proxy.example:8080"),
        "user_agent": "Mozilla/5.0 (Windows NT 10.0)",
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "geo_lat": 39.9042,
        "geo_lng": 116.4074,
        "fingerprint_profile_id": None,
    }
    row.update(over)
    return row


async def test_validate_uses_full_joined_environment_row():
    """LEFT JOIN 出来的整行环境必须一路送到浏览器 —— 逐列核对 §3.2 列名，
    任何一列拼错都会静默退化成默认环境（北京 IP 配默认时区正是风控特征）。"""
    client = FakeBrowserClient()
    await _adapter(client).validate_session(
        _account(environment=_joined_env_row(timezone_id="America/Los_Angeles"))
    )

    env = client.calls[0]["environment"]
    assert env.proxy_url == "http://u:p@proxy.example:8080"  # adapter 解的密
    assert env.user_agent == "Mozilla/5.0 (Windows NT 10.0)"
    assert env.locale == "zh-CN"
    assert env.timezone_id == "America/Los_Angeles"
    assert env.geo_lat == 39.9042
    assert env.geo_lng == 116.4074


async def test_validate_without_environment_row_falls_back_to_defaults():
    """S4 之前绝大多数账号没有环境行（repository 给 None，不是 {}）。"""
    client = FakeBrowserClient()
    await _adapter(client).validate_session(_account(environment=None))

    env = client.calls[0]["environment"]
    assert env.proxy_url is None
    assert env.locale == "zh-CN"
    assert env.timezone_id == "Asia/Shanghai"


async def test_explicit_environment_overrides_account_row():
    client = FakeBrowserClient()
    await _adapter(client).validate_session(
        _account(environment={"locale": "en-US"}),
        environment=SessionEnvironment(locale="ja-JP"),
    )
    assert client.calls[0]["environment"].locale == "ja-JP"


# ── fail-fast (§7.7) ────────────────────────────────────────


def _video_intent(**over) -> PublishIntent:
    base = dict(
        content_type="video",
        media=(PublishMedia(kind="video", url="https://s3/x.mp4", filename="x.mp4"),),
        title="Launch",
    )
    base.update(over)
    return PublishIntent(**base)  # type: ignore[arg-type]


def test_valid_intent_has_no_problems():
    assert _adapter().validate_publish_intent(_video_intent()) == []


def test_intent_without_media_is_rejected_before_the_browser_starts():
    problems = _adapter().validate_publish_intent(_video_intent(media=()))
    assert any("no media" in p for p in problems)


def test_intent_with_empty_title_is_rejected():
    problems = _adapter().validate_publish_intent(_video_intent(title="   "))
    assert any("title is empty" in p for p in problems)


def test_intent_with_unsupported_extension_is_rejected():
    problems = _adapter().validate_publish_intent(
        _video_intent(
            media=(
                PublishMedia(kind="video", url="https://s3/x.avi", filename="x.avi"),
            )
        )
    )
    assert any(".avi" in p or "avi" in p for p in problems)


def test_intent_with_unknown_visibility_is_rejected():
    problems = _adapter().validate_publish_intent(_video_intent(visibility="secret"))
    assert any("visibility" in p for p in problems)


def test_intent_payload_carries_semantic_visibility_not_platform_enum():
    """§6.1 a：通道契约里不出现抖音的 private_status 0/1/2。"""
    payload = _video_intent(visibility="friends").to_payload()
    assert payload["visibility"] == "friends"
    assert "private_status" not in payload
    assert "download_type" not in payload


def test_intent_payload_has_no_dom_mechanism_fields():
    """§6.1 c：意图只说"发什么"，不说"怎么发" —— 档位 2（浏览器当签名机 +
    裸 HTTP 上传）才能复用同一个契约。"""
    payload = _video_intent().to_payload()
    assert set(payload) == {
        "content_type",
        "media",
        "title",
        "description",
        "topics",
        "visibility",
        "allow_download",
        "cover",
        "scheduled_at",
        "platform_options",
    }
    # 素材以 URL 交付（DOM 档下载到 /tmp、裸 HTTP 档直接流式上传、CLI 档当参数）
    assert payload["media"][0]["url"].startswith("https://")


# ── 抽象形状 / registry ─────────────────────────────────────


async def test_publish_signature_is_fixed_but_lands_in_s3():
    # 一个 publish() 覆盖全部内容形态（intent.content_type），不是每种一个方法
    with pytest.raises(NotImplementedError):
        await _adapter().publish(_account(), _video_intent())


def test_unsupported_platform_rejected_at_construction():
    with pytest.raises(ValueError):
        SessionAdapter("myspace")


def test_registry_resolves_both_auth_types():
    from app.services.distribution.douyin_adapter import (
        DouyinAdapter,
        DouyinCredentials,
    )

    creds = DouyinCredentials(
        client_key="k", client_secret="s", redirect_uri="https://x"
    )
    # 现有签名语义未变 —— distribution_router / publish_distribution 不受影响
    assert isinstance(get_adapter("douyin", creds), DouyinAdapter)
    assert isinstance(get_session_adapter("douyin"), SessionAdapter)
    assert isinstance(resolve_adapter("douyin", auth_type="session"), SessionAdapter)
    assert isinstance(
        resolve_adapter("douyin", auth_type="oauth", creds=creds), DouyinAdapter
    )
    assert supports_session("douyin") is True
    assert supports_session("kuaishou") is False


def test_resolve_adapter_requires_creds_for_oauth():
    with pytest.raises(ValueError):
        resolve_adapter("douyin", auth_type="oauth")


def test_session_adapter_does_not_pretend_to_be_an_oauth_adapter():
    """两条通道形状不同：不给它们编一个假的共同基类。"""
    from app.services.distribution.platform_base import PlatformAdapter

    assert not issubclass(SessionAdapter, PlatformAdapter)
    assert not hasattr(SessionAdapter, "get_auth_url")
