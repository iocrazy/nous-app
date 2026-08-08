"""Bilibili (B站) session validation + QR login.

Same contract as `douyin.py` / `xiaohongshu.py`: platform facts + two pure
judges, nothing platform-neutral.

**Bilibili differs from the other two in ways that matter here:**

1. **Two hosts, not one.** The QR code is rendered on `passport.bilibili.com`
   (or the `www.bilibili.com` login modal), while the creator console lives on
   `member.bilibili.com`. A login therefore *changes host* on success — the
   judge below treats reaching the member host as the success signal, and must
   NOT treat "left the passport host" as a failure.
2. **The console is the studio**, `member.bilibili.com/platform/upload/video`.
3. Its QR flow exposes a real polling API, but we stay at the DOM level like
   the other platforms — one mechanism, one place to debug.

**Verification status.** Every constant is tagged the same way as the
Xiaohongshu module:

- `[URL]`   — documented entry points, low risk.
- `[COPY]`  — Chinese UI copy. **UNVERIFIED against a live page.**
- `[GUESS]` — DOM selectors. **UNVERIFIED.**

Calibrate by binding one account, then counting `get_by_text(..., exact=True)`
matches on the live pages. Anything not exactly 1 is wrong. Douyin's history
(three inferred profile selectors, all wrong, green tests throughout) is the
reason this warning is repeated in every platform module.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlsplit

from ..login import LoginFlowSpec, LoginJudgement, LoginPageSnapshot, LoginProfile
from ..schemas import EnvironmentConfig, SessionResult, SessionStatus
from ..validation import DomValidationSpec, Judgement, run_dom_session_validation
from . import register, register_login

PLATFORM = "bilibili"

# --- session validation -----------------------------------------------------

# [URL] The creator studio's video upload page.
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"
MEMBER_HOSTS = ("member.bilibili.com",)
UPLOAD_PATH_FRAGMENT = "/platform/upload"

# [COPY] UNVERIFIED. Broad on purpose — missing a marker means reading a login
# page as a live session, which is the expensive direction.
LOGIN_TEXT_MARKERS = (
    "登录",
    "扫码登录",
    "短信登录",
    "密码登录",
    "请使用哔哩哔哩客户端扫码登录",
)


def judge_bilibili_session(url: str, visible_login_texts: Sequence[str]) -> Judgement:
    """Decide validity from the settled page. Pure.

    Parses the URL rather than substring-matching it: bilibili's logged-out
    redirect carries the original destination in a `returnUrl`/`gourl` query
    parameter, so `"/platform/upload" in url` would read the *login* page as a
    valid session — the same trap documented in the Douyin judge.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()

    if host not in MEMBER_HOSTS:
        return Judgement(
            valid=False,
            reason=f"navigated away from the member host (host={host or 'unknown'})",
        )

    if UPLOAD_PATH_FRAGMENT not in parts.path:
        return Judgement(
            valid=False,
            reason=f"redirected off the upload page (path={parts.path or '/'})",
        )

    if visible_login_texts:
        return Judgement(
            valid=False,
            reason="login prompt visible on the upload page: "
            + ", ".join(visible_login_texts),
        )

    return Judgement(valid=True, reason="upload page reached with no login prompt")


SPEC = DomValidationSpec(
    platform=PLATFORM,
    target_url=UPLOAD_URL,
    login_text_markers=LOGIN_TEXT_MARKERS,
    judge=judge_bilibili_session,
)


async def validate_session(
    storage_state: dict[str, Any], environment: EnvironmentConfig | None = None
) -> SessionResult:
    return await run_dom_session_validation(SPEC, storage_state, environment)


register(PLATFORM, validate_session)

# --- QR login ---------------------------------------------------------------

# [URL] 登录后的落点(gourl 目标)。
PROFILE_URL = "https://member.bilibili.com/platform/home"
# [URL] 读身份的地方 —— **不是**创作中心,见 PROFILE_TEXT_SELECTORS 的说明。
# space.bilibili.com 不带 uid 时会自动重定向到当前登录用户的空间。
IDENTITY_URL = "https://space.bilibili.com/"

# [URL] Passport login page, **with an explicit post-login destination**.
#
# ⚠️ 没有 `gourl` 的话,确认之后页面**原地不动**留在 /login。2026-08-08 实测:
# 用户手机点了确认,后端却一直收到
#   waiting_scan / "no recognisable login state on the page yet (path=/login)"
# 因为本模块的成功判据是"到达 member.bilibili.com",而没人告诉 B 站要跳过去。
#
# 判据本身是对的、也验证过:未登录访问 member.bilibili.com 会被弹回
# passport /login,所以"落在 member 主机上"确实等价于"已登录"。缺的只是让
# 平台知道往哪跳。
#
# 实测带上 gourl 后二维码照常渲染(alt="Scan me!" 仍匹配 1 个),所以这个
# 参数不影响扫码流程本身。
LOGIN_URL = (
    "https://passport.bilibili.com/login"
    f"?gourl={quote(PROFILE_URL, safe='')}"
)

# ⚠️ Login STARTS on passport.* and SUCCEEDS on member.* — the host changes.
# `judge_bilibili_login` must therefore accept both, and must not read "no
# longer on the passport host" as a failure. This is the one structural
# difference from Douyin / Xiaohongshu, where login begins and ends on the same
# host.
PASSPORT_HOSTS = ("passport.bilibili.com", "www.bilibili.com")
CONSOLE_PATH_FRAGMENT = "/platform"

# [VERIFIED 2026-08-08] 对着真实 passport.bilibili.com/login 逐个数过命中数:
#
#   img[class*="qrcode"]      → 0   ← 我原本写的,**匹配不到**
#   div[class*="qrcode"] img  → 1
#   img[src^="data:image"]    → 1
#   .login-scan-box img       → 0   ← 也是猜的,不存在
#   div[class*="scan"] img    → 1
#
# 二维码那张 img 的 class 是**空字符串**,所以任何 class 匹配都够不到它。
# 真实祖先链:DIV.login-scan__qrcode → DIV.login-scan sns_bind_left_wp main__
#
# 顺序按"最稳 → 最泛":`alt="Scan me!"` 是这张图唯一自带的语义标识,比
# 结构位置更抗改版;data: 前缀次之(二维码总是内联生成的);class 匹配放
# 最后当兜底。
QRCODE_SELECTORS = (
    'img[alt="Scan me!"]',
    'div[class*="qrcode"] img',
    'img[src^="data:image"]',
    'div[class*="scan"] img',
)

# [COPY] UNVERIFIED — the login sub-states.
LOGIN_MARKERS = ("扫码登录", "请使用哔哩哔哩客户端扫码登录", "短信登录", "密码登录")
SCANNED_MARKERS = ("扫码成功", "请在手机端确认", "已扫描")
EXPIRED_MARKERS = ("二维码已失效", "点击刷新", "已过期")

# [GUESS] UNVERIFIED. Re-issue an expired code.
REFRESH_SELECTORS = (
    'div[class*="qrcode"]',
    'div[class*="refresh"]',
    ".login-scan-box",
)

# [GUESS] UNVERIFIED. SMS fallback.
SMS_INPUT_SELECTORS = (
    'input[placeholder*="验证码"]',
    'input[placeholder*="短信"]',
)
SMS_SUBMIT_SELECTORS = (
    'button:has-text("登录")',
    '.btn-login',
)

# [VERIFIED 2026-08-08] 用真实会话在两个页面上数过命中数。
#
# ⚠️ **昵称不在创作中心**。member.bilibili.com/platform/home 上:
#     [class*="nickname"]  -> 0
# 头像旁边那块文字是「成为UP主的第2560天」;hover 头像弹出的 popover 里
# 只有导航项(个人中心 / 投稿管理 / B币钱包 / 订单中心 / 直播中心 / 退出登录),
# 没有昵称。所以 profile_url 指向**个人空间**而不是创作中心:
#
#   space.bilibili.com/<uid>  →  .nickname 命中 1 个,读到 "imheygo"
#
# 这也是它与抖音的一处结构差异:抖音的创作者中心首页同时有昵称和头像,
# B 站把身份信息留在了主站空间页。
#
# 头像:member 首页 `.avatar img` 命中 1 个(精确)。原先写的
# `img[src*="hdslb.com"]` 命中 **3+**,因为 hdslb.com 是 B 站所有静态资源的
# CDN,页面上一堆 AI 工具图标都在那个域下 —— 用它当"头像"会抓到图标。
# `bfs/face` 才是头像专属路径段。
PROFILE_TEXT_SELECTORS: Mapping[str, tuple[str, ...]] = {
    "username": (
        ".nickname",
        '[class*="nickname"]',
        "#h-name",
    ),
    # 留空:空间页 URL 里就带 uid,而 DedeUserID cookie 是更可靠的来源
    # (见 USER_ID_COOKIES)。与其配几个猜的选择器,不如让 cookie 兜底接手 ——
    # 它已经在实战里救过一次:三个 profile 选择器全落空时,账号至少还有 id。
    "platform_user_id": (),
}
PROFILE_ATTR_SELECTORS: Mapping[str, tuple[tuple[str, ...], str]] = {
    "avatar_url": (
        (
            'img[src*="bfs/face"]',   # 头像专属路径段,不是泛泛的 hdslb.com
            ".avatar img",
            '[class*="avatar"] img',
        ),
        "src",
    ),
}

# `DedeUserID` is bilibili's numeric user id cookie and is the reliable
# fallback identity when every profile selector misses.
USER_ID_COOKIES = ("DedeUserID",)

# [COPY] Prefixes shown in front of the public id.
_ID_PREFIXES = ("UID：", "UID:", "UID", "ID：", "ID:")


def judge_bilibili_login(snapshot: LoginPageSnapshot) -> LoginJudgement:
    """What state is the login page in? Pure.

    Specific → general, same as the other platforms: an expired code and a
    scanned code both still render login copy, so testing `login_texts` first
    would swallow both.
    """
    if snapshot.expired_texts:
        return LoginJudgement(
            SessionStatus.QRCODE_EXPIRED,
            "the QR code expired: " + ", ".join(snapshot.expired_texts),
        )

    if snapshot.scanned_texts:
        return LoginJudgement(
            SessionStatus.SCANNED,
            "scanned; waiting for confirmation on the phone",
        )

    if snapshot.sms_input_visible:
        return LoginJudgement(
            SessionStatus.SMS_REQUIRED,
            "the platform is asking for an SMS code",
        )

    parts = urlsplit(snapshot.url or "")
    host = (parts.hostname or "").lower()

    # Success = arrived on the MEMBER host. Unlike the other platforms this is a
    # different host from where login started, which is why it is checked before
    # anything else host-related.
    if host in MEMBER_HOSTS:
        # ⚠️ SUCCESS,不是 SESSION_VALID。登录轮询(login_sessions.py)只认
        # SUCCESS —— 返回别的值时它会当作"还没完成"继续等,于是即便页面
        # 已经登录成功,弹窗也永远停在上一个状态。抖音那份用的就是 SUCCESS,
        # 我照抄时又按直觉换了名字(同一天第二次栽在枚举上)。
        #
        # SESSION_VALID 是**会话校验**那条链路的词汇(validate_session 用),
        # 跟登录完成不是一回事 —— 名字相近但归属不同的两套状态。
        return LoginJudgement(SessionStatus.SUCCESS, "reached the creator studio")

    if snapshot.login_texts or snapshot.qrcode_visible:
        return LoginJudgement(
            SessionStatus.WAITING_SCAN, "waiting for the code to be scanned"
        )

    # Still on passport/www with nothing recognisable yet — the login card is
    # injected by client-side JS, so an early read legitimately sees nothing.
    if host in PASSPORT_HOSTS:
        return LoginJudgement(
            SessionStatus.WAITING_SCAN,
            f"no recognisable login state on the page yet (path={parts.path or '/'})",
        )

    return LoginJudgement(
        SessionStatus.FAILED,
        f"navigated to an unexpected host (host={host or 'unknown'})",
    )


def parse_bilibili_profile(
    fields: Mapping[str, str], cookies: Sequence[Mapping[str, Any]]
) -> LoginProfile:
    """Scraped strings -> identity. Pure, best-effort throughout.

    `DedeUserID` makes the fallback unusually reliable here: even with every
    display selector broken, a bound account still gets its real numeric id
    rather than an opaque session token.
    """
    username = (fields.get("username") or "").strip()

    raw_id = (fields.get("platform_user_id") or "").strip()
    for prefix in _ID_PREFIXES:
        if raw_id.startswith(prefix):
            raw_id = raw_id[len(prefix) :].strip()
            break

    if not raw_id:
        by_name = {c.get("name"): c.get("value") for c in cookies if c.get("name")}
        for name in USER_ID_COOKIES:
            value = (by_name.get(name) or "").strip()
            if value:
                raw_id = value
                break

    avatar = (fields.get("avatar_url") or "").strip()
    if not avatar.startswith(("http://", "https://", "data:image")):
        avatar = ""

    return LoginProfile(
        platform_user_id=raw_id,
        username=username,
        avatar_url=avatar or None,
    )


LOGIN_SPEC = LoginFlowSpec(
    platform=PLATFORM,
    login_url=LOGIN_URL,
    profile_url=IDENTITY_URL,
    qrcode_selectors=QRCODE_SELECTORS,
    login_markers=LOGIN_MARKERS,
    scanned_markers=SCANNED_MARKERS,
    expired_markers=EXPIRED_MARKERS,
    refresh_selectors=REFRESH_SELECTORS,
    sms_input_selectors=SMS_INPUT_SELECTORS,
    sms_submit_selectors=SMS_SUBMIT_SELECTORS,
    judge=judge_bilibili_login,
    parse_profile=parse_bilibili_profile,
    profile_text_selectors=PROFILE_TEXT_SELECTORS,
    profile_attr_selectors=PROFILE_ATTR_SELECTORS,
)

register_login(PLATFORM, LOGIN_SPEC)
