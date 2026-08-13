"""每账号浏览器环境的生成规则（P2-4，mig 402 + 424）。

这里守的不是"能不能生成出一个值"，而是**每个值为什么是那个值** —— 指纹的
每一项都要么与其它表面自洽，要么根本不该写。所以断言分两类：

1. **必须为空的字段**（user_agent / geo / proxy / fingerprint_profile_id）。
   这些是"我们决定不设"，不是"还没做"。写进去反而制造自相矛盾的指纹：
   Playwright 的 ``user_agent`` 选项**不改 Client Hints**（2026-08-12 在生产
   nous-browser 实测：UA 覆盖成 Chrome/999 之后 ``Sec-CH-UA`` 仍是
   ``"Chromium";v="145"``），设 geo 会连带自动授予 geolocation 权限。
2. **必须稳定的字段**（viewport）。一个每次登录指纹都在变的账号比指纹固定的
   更可疑，所以生成只发生一次 —— 那条由 ``pin_environment`` 的测试守（见
   ``tests/repositories/test_account_environment_pinning.py``）。
"""

from __future__ import annotations

import random

from app.services.distribution.account_environment import (
    SESSION_VIEWPORTS,
    GeneratedEnvironment,
    choose_viewport,
    generate_environment,
)
from app.services.distribution.browser_client import (
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE_ID,
)


# ── 值的自洽性 ───────────────────────────────────────────────────────────
def test_generated_environment_never_sets_a_user_agent():
    """UA 必须留空。

    Playwright 的 ``user_agent`` 只改 navigator.userAgent 与 UA 请求头，
    Client Hints（Sec-CH-UA / navigator.userAgentData）仍报真实 Chromium 版本。
    任何写死的 UA 串在浏览器镜像升级后就变成**自相矛盾**的指纹 —— 比共用同一
    个 UA 更糟。浏览器自己的 UA 是唯一恒自洽的那个。
    """
    env = generate_environment("douyin")
    assert not hasattr(env, "user_agent")
    assert "user_agent" not in env.to_row()
    assert env.to_session_environment().user_agent is None


def test_generated_environment_never_sets_geolocation():
    """geo 必须留空 —— 设了会连带 ``permissions: ["geolocation"]``，页面于是
    不弹窗就能拿到坐标，真实用户的全新 profile 永远不会这样。"""
    env = generate_environment("douyin")
    row = env.to_row()
    assert "geo_lat" not in row and "geo_lng" not in row
    session_env = env.to_session_environment()
    assert session_env.geo_lat is None and session_env.geo_lng is None


def test_generated_environment_cannot_carry_a_proxy_secret():
    """``GeneratedEnvironment`` 结构上就放不下 proxy_url。

    它是 DBOS step 的返回值，会被引擎持久化 —— CLAUDE.md 的纪律是凭证不进
    workflow input/output。让这一点成为类型层面的事实，而不是一句"记得剥掉"。
    """
    env = generate_environment("douyin")
    assert not hasattr(env, "proxy_url")
    assert "proxy_url" not in env.to_row()
    assert "proxy_url" not in env.to_payload()
    assert env.to_session_environment().proxy_url is None


def test_generated_environment_never_claims_a_fingerprint_profile():
    """S6（AdsPower / 比特浏览器）没有任何实现读它，写值等于让一个不生效的
    字段看起来生效了。"""
    assert "fingerprint_profile_id" not in generate_environment("douyin").to_row()


def test_locale_and_timezone_match_the_chinese_exit_ip():
    """中国平台 + 中国家宽出口 → zh-CN / Asia/Shanghai。

    这两个值**不逐账号变化**是刻意的：它们必须与出口 IP 的地理一致，让它们
    不同就是在制造异常信号，不是隔离（mig 402 表头）。
    """
    env = generate_environment("douyin")
    assert env.locale == DEFAULT_LOCALE == "zh-CN"
    assert env.timezone_id == DEFAULT_TIMEZONE_ID == "Asia/Shanghai"


# ── viewport：唯一真正逐账号不同的轴 ─────────────────────────────────────
def test_every_candidate_viewport_is_at_least_the_known_good_baseline():
    """下界不变式：抖音 DOM 自动化一直跑在 Playwright 默认的 1280x720 上。

    所有候选都 ≥ 它 → 任何账号拿到的空间只会比"已知可用"更多，不会更少。
    这条排掉了"某个账号窗口太窄，创作中心切成紧凑布局，选择器全崩"这种只在
    单账号上复现的故障。上界是 Xvfb 的 1920x1080，headed 窗口得装得下。
    """
    assert SESSION_VIEWPORTS, "候选表不能为空 —— 空表会让 choose_viewport 无从可选"
    for w, h in SESSION_VIEWPORTS:
        assert w >= 1280 and h >= 720, f"{w}x{h} 比已知可用的 1280x720 还小"
        assert w <= 1920 and h <= 1080, f"{w}x{h} 装不进 Xvfb 的 1920x1080"


def test_candidate_viewports_are_distinct():
    """重复项会让避让逻辑白做一格。"""
    assert len(set(SESSION_VIEWPORTS)) == len(SESSION_VIEWPORTS)


def test_generated_viewport_comes_from_the_candidate_table():
    for _ in range(50):
        env = generate_environment("douyin")
        assert (env.viewport_width, env.viewport_height) in SESSION_VIEWPORTS


def test_choose_viewport_avoids_sizes_a_sibling_already_pinned():
    """避让是这个轴唯一的意义所在。

    候选只有 6 个、用户当前 3 个抖音账号，纯随机大概率撞车 —— 而撞车的两个
    账号在这条唯一可变的轴上就又变回一模一样了，等于对它们没做。
    """
    taken = list(SESSION_VIEWPORTS[:-1])
    for _ in range(20):
        assert choose_viewport(taken) == SESSION_VIEWPORTS[-1]


def test_choose_viewport_falls_back_to_random_when_everything_is_taken():
    """账号数超过候选数时"有重复"仍远好于"全部相同"，所以退回随机而不是报错
    —— 绝不能因为选不出尺寸就挡住用户绑号。"""
    got = choose_viewport(list(SESSION_VIEWPORTS), rng=random.Random(0))
    assert got in SESSION_VIEWPORTS


def test_generate_environment_threads_taken_viewports_through():
    taken = list(SESSION_VIEWPORTS[:-1])
    env = generate_environment("douyin", taken_viewports=taken)
    assert (env.viewport_width, env.viewport_height) == SESSION_VIEWPORTS[-1]


def test_choose_viewport_spreads_across_candidates():
    """不是常量。若实现退化成"永远返回第一个"，避让逻辑看着还在、实际已死。"""
    seen = {choose_viewport(rng=random.Random(s)) for s in range(200)}
    assert len(seen) > 1


# ── 穿过 DBOS step 边界的往返 ────────────────────────────────────────────
def test_payload_roundtrip_preserves_the_generated_values():
    """step 返回值会被 DBOS 持久化并在重放时原样喂回来。往返丢字段 = 重放时
    悄悄换了一套环境。"""
    env = generate_environment("douyin")
    assert GeneratedEnvironment.from_payload(env.to_payload()) == env


def test_from_payload_of_nothing_is_the_safe_default():
    """mig 424 之前起的 workflow 重放时，DBOS 里记的 step 返回值没有
    ``environment`` 键 —— 必须退回默认值而不是炸掉。"""
    env = GeneratedEnvironment.from_payload(None)
    assert env.locale == DEFAULT_LOCALE
    assert env.timezone_id == DEFAULT_TIMEZONE_ID
    assert env.viewport_width is None and env.viewport_height is None


def test_session_environment_carries_the_viewport_to_the_browser():
    """浏览器侧只认 ``SessionEnvironment.to_payload()`` 的键。"""
    env = generate_environment("douyin")
    payload = env.to_session_environment().to_payload()
    assert payload["viewport_width"] == env.viewport_width
    assert payload["viewport_height"] == env.viewport_height
    assert payload["locale"] == "zh-CN"
    assert payload["timezone_id"] == "Asia/Shanghai"
    assert payload["user_agent"] is None
    assert payload["proxy_url"] is None
