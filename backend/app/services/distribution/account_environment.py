"""每账号浏览器环境的**生成**（P2-4，mig 402 建表 + mig 424 补 viewport）。

读取侧在 ``session_adapter.build_environment``（DB 行 → ``SessionEnvironment``），
应用侧在浏览器服务的 ``build_context_kwargs`` / ``_login_context_kwargs``。本
模块只负责一件事：**一个新账号第一次绑定时，那套环境的每个值取什么、为什么**。

为什么这里只动 viewport 一个轴
==============================
2026-08-12 在生产 nous-browser 容器（Chromium 145.0.7632.6，headed + Xvfb）
实测了 Playwright 每个 context 选项的真实作用面，结论是 ``account_environments``
的字段里**只有窗口尺寸能真正逐账号不同而不引入自相矛盾**：

``user_agent`` —— 不写（NULL）
    ``new_context(user_agent=...)`` 只改 ``navigator.userAgent`` 和
    ``User-Agent`` 请求头，**不改 Client Hints**。实测把 UA 覆盖成
    ``Chrome/999.0.0.0`` 之后，``Sec-CH-UA`` 仍然是
    ``"Chromium";v="145"``、``navigator.userAgentData.uaFullVersion`` 仍然是
    ``145.0.7632.6``。也就是说任何写进库的 UA 串，在浏览器镜像升级 Chromium
    的那一刻就变成一条**自相矛盾**的指纹 —— 比"三个账号共用同一个 UA"更糟：
    共用只是关联信号，矛盾是伪造信号。

    而且现代 Chrome 的 reduced UA 本身就没有可变位（实测默认值是
    ``Chrome/145.0.0.0``，minor/build 被 Chromium 主动清零），平台 token
    （``X11; Linux x86_64``）也被 ``Sec-CH-UA-Platform: "Linux"`` 和
    ``navigator.platform`` 锁死。所以"给每个账号编一个不同的 UA"这条路在
    技术上是空的，不是我们没做。

``locale`` / ``timezone_id`` —— 固定 zh-CN / Asia/Shanghai
    抖音是中国平台，出口是中国家宽住宅 IP。这两个值必须与出口 IP 的地理
    一致（mig 402 表头原话）。中国只有一个民用时区；``Asia/Urumqi`` 只在
    出口 IP 落在新疆时才自洽。**让它们逐账号不同就是制造异常**，不是隔离。

``geo_lat`` / ``geo_lng`` —— 不写（NULL）
    浏览器侧 ``build_context_kwargs`` 一旦看到坐标，会连带
    ``permissions: ["geolocation"]``。结果是页面调
    ``navigator.geolocation.getCurrentPosition()`` **不弹窗直接拿到坐标** ——
    真实用户的全新 profile 永远不会这样。这是把自己标出来，不是伪装。

``proxy_url`` —— 不写（NULL）
    没有代理池。字段与 ``build_environment`` 的解密/降级链路保持可用，留给
    将来。⚠️ 代理是本项目里**唯一真正强**的隔离轴（出口 IP），viewport 只是
    在它到位之前能拿到的那点熵，不要把两者当同一量级。

``fingerprint_profile_id`` —— 不写（NULL）
    S6（AdsPower / 比特浏览器）没有任何实现读它。写个值等于让一个不生效的
    字段看起来生效了。

``viewport_width`` / ``viewport_height`` —— **逐账号不同**
    窗口尺寸是唯一"任何取值都成立、且没有任何其它表面能与之矛盾"的轴：
    页面读到的 ``innerWidth`` / ``screen.width`` 只是一个数，不像 UA 那样
    有第二个信源可以对不上。同时它也是**唯一一个"这次跟上次不一样"属于正常
    现象**的轴（真人会拖窗口），这一点很重要 —— 见下面 ``choose_viewport``
    关于「登录那一次用的环境」的说明。

稳定性 > 随机性
===============
生成只发生在**绑定成功那一次**（``SocialAccountsRepository.pin_environment``
是 ``ON CONFLICT DO NOTHING``）。一个每次登录指纹都在变的账号，比一个指纹
固定的账号更可疑。所以本模块没有任何"刷新环境"的入口。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from app.services.distribution.browser_client import (
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE_ID,
    SessionEnvironment,
)

# 候选窗口尺寸。每一个都是**真实存在且常见的桌面分辨率** —— 这一点是必需的，
# 因为 Playwright 在设了 viewport 之后会把 ``screen`` 也覆盖成同样的值（实测：
# Xvfb 是 1920x1080，而页面读到的 ``screen`` 等于 viewport），所以这个数会同时
# 作为"屏幕分辨率"被读到，编一个 1920x960 这种不存在的屏幕就露馅了。
#
# 两条硬边界，改这张表时必须复核：
#   下界 1280x720 —— 抖音 DOM 自动化一直跑在 Playwright 默认的 1280x720 上。
#     所有候选都 ≥ 它，意味着任何账号拿到的空间只会**比已知可用的更多**，
#     不会更少。这条不变式排掉了"某个账号的窗口太窄，创作中心切成紧凑布局，
#     选择器全崩"这类只在单个账号上复现的故障。
#   上界 1920x1080 —— Xvfb 的 ``XVFB_SCREEN``。headed 窗口得装得下。
#
# ⚠️ 已知未修：viewport == screen 意味着"一个没有任何浏览器边框的窗口占满整个
# 屏幕"，严格说不像真实 headed 浏览器。修它要单独引入 ``screen`` context 选项
# （Playwright 支持），本次不做 —— 它对所有账号一视同仁，不是关联信号。
SESSION_VIEWPORTS: tuple[tuple[int, int], ...] = (
    (1366, 768),  # 经典入门笔记本
    (1280, 800),  # 16:10 小尺寸笔记本（宽度等于下界）
    (1440, 900),  # MacBook Air / 16:10
    (1536, 864),  # 1920x1080 @125% 缩放后的 CSS 像素 —— 桌面端占比最高的一档
    (1600, 900),  # 16:9 笔记本
    (1680, 1050),  # WSXGA+
)


@dataclass(frozen=True)
class GeneratedEnvironment:
    """一个新账号的初始环境。

    **刻意没有 proxy_url 字段。** 这个对象会作为 DBOS step 的返回值被引擎持久化
    （见 ``session_login.start_login_step``），而 CLAUDE.md 的纪律是凭证不进
    workflow input/output。让"这里放不下密钥"成为类型层面的事实，比写一句注释
    提醒未来的人记得剥掉可靠。代理要接进来时，走的是绑定后往
    ``account_environments`` 里补写，不是塞进这个 dataclass。
    """

    locale: str = DEFAULT_LOCALE
    timezone_id: str = DEFAULT_TIMEZONE_ID
    viewport_width: Optional[int] = None
    viewport_height: Optional[int] = None

    def to_session_environment(self) -> SessionEnvironment:
        """给浏览器服务用的形态（登录 / 校验 / 发布同一个类型）。"""
        return SessionEnvironment(
            locale=self.locale,
            timezone_id=self.timezone_id,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
        )

    def to_row(self) -> dict[str, Any]:
        """给 ``pin_environment`` 用的列名形态（即 mig 402/424 的列名）。

        只列本模块**决定**的列。``user_agent`` / ``geo_*`` / ``proxy_url`` /
        ``fingerprint_profile_id`` 不出现 —— 它们保持 DB 默认的 NULL，理由见
        模块 docstring。显式写 ``"user_agent": None`` 会让"我们决定不设"和
        "我们忘了设"长得一模一样。
        """
        return {
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
        }

    def to_payload(self) -> dict[str, Any]:
        """穿过 DBOS step 边界用的可序列化形态。"""
        return {
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
        }

    @classmethod
    def from_payload(cls, payload: Optional[dict[str, Any]]) -> "GeneratedEnvironment":
        if not payload:
            return cls()
        return cls(
            locale=str(payload.get("locale") or DEFAULT_LOCALE),
            timezone_id=str(payload.get("timezone_id") or DEFAULT_TIMEZONE_ID),
            viewport_width=payload.get("viewport_width"),
            viewport_height=payload.get("viewport_height"),
        )


def choose_viewport(
    taken: Iterable[tuple[int, int]] = (),
    *,
    rng: Optional[random.Random] = None,
) -> tuple[int, int]:
    """挑一个尽量没被同 scope 其它账号用过的窗口尺寸。

    ``taken`` 是同 scope + 同平台下已经钉住的尺寸。**避让是有意义的**：候选只有
    6 个，用户当前是 3 个抖音账号，纯随机大概率撞车，而撞车的两个账号在这唯一
    可变的轴上又变回一模一样 —— 那这个功能对它们就等于没做。全撞满之后退回
    纯随机（不报错：多于 6 个账号时"有重复"仍然远好于"全部相同"）。

    纯函数，DB 查询留在调用方（repository）—— 这样这条选择规则本身可测，不用
    起 DB。
    """
    r = rng or random.SystemRandom()
    used = {(int(w), int(h)) for w, h in taken}
    free = [vp for vp in SESSION_VIEWPORTS if vp not in used]
    return r.choice(free or list(SESSION_VIEWPORTS))


def generate_environment(
    platform: str,
    *,
    taken_viewports: Sequence[tuple[int, int]] = (),
    rng: Optional[random.Random] = None,
) -> GeneratedEnvironment:
    """一个新账号的初始环境。

    ``platform`` 目前只有会话通道的中国平台（抖音 / 小红书），locale 与 timezone
    因此是固定的。参数留着不是装饰：一旦接入非中国平台，**这两个值必须跟着出口
    IP 的地理一起变**，而变的地方就是这里。届时它们要与代理的出口地一致，不能
    各改各的（mig 402 表头）。
    """
    w, h = choose_viewport(taken_viewports, rng=rng)
    return GeneratedEnvironment(
        locale=DEFAULT_LOCALE,
        timezone_id=DEFAULT_TIMEZONE_ID,
        viewport_width=w,
        viewport_height=h,
    )


__all__ = [
    "SESSION_VIEWPORTS",
    "GeneratedEnvironment",
    "choose_viewport",
    "generate_environment",
]
