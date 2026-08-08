"""WebRTC 不得绕过代理暴露真实出口 IP。

2026-08-08 在本容器实测:不加任何策略时,页面能通过 ICE gathering 读到
**公网地址** `38.175.x.x` —— 而同一时刻所有 HTTP 请求都老实走着账号配置的
代理。ICE 会自己开 UDP 套接字,那条路径根本不经过 Playwright 配的 HTTP 代理。

**这比不配代理更糟。** 每账号环境会配一套城市匹配的代理 + locale + 时区;
一个来自另一个省份的真实出口 IP,把本来自洽的身份变成**自相矛盾**的身份,
而自相矛盾正是风控最容易抓的信号。

这类"防泄露"配置的特点是:删掉它,所有功能照常工作,测试照常绿,只是防护
没了 —— 所以必须有一条会因为它消失而失败的测试。
"""

from __future__ import annotations

import pytest

from app.browser_runtime import LAUNCH_ARGS, build_launch_kwargs

pytestmark = pytest.mark.unit

# Chromium 改过这个开关的名字,新旧都要传:旧版本遇到不认识的名字是**静默
# 忽略**而不是报错,所以只传新名会让旧基础镜像继续泄露而毫无征兆。
_WEBRTC_FLAGS = (
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
)


def test_launch_args_carry_both_webrtc_flag_spellings():
    for flag in _WEBRTC_FLAGS:
        assert flag in LAUNCH_ARGS, (
            f"缺少 {flag} —— WebRTC 会绕过代理直接暴露真实出口 IP"
        )


def test_the_policy_survives_into_the_actual_launch_kwargs():
    """光在常量里不算数,要真的传给 chromium.launch()。

    分开测是因为这两件事断过:`build_launch_kwargs` 完全可以在某次重构里
    改成只挑几个参数传,而常量测试照样绿。
    """
    args = build_launch_kwargs(None)["args"]
    for flag in _WEBRTC_FLAGS:
        assert flag in args


def test_the_policy_is_present_even_when_a_proxy_is_configured():
    """配了代理时更需要它 —— 那正是泄露最有害的场景。

    没有代理时泄露的是"本来也会暴露的那个 IP";配了代理却泄露,才制造出
    "代理 IP 与真实 IP 不一致"这种自相矛盾的指纹。
    """
    from app.schemas import EnvironmentConfig

    env = EnvironmentConfig(proxy_url="http://user:pass@proxy.example:8080")
    kwargs = build_launch_kwargs(env)

    assert "proxy" in kwargs, "前提:代理确实被配上了"
    for flag in _WEBRTC_FLAGS:
        assert flag in kwargs["args"]
