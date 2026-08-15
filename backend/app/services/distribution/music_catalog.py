"""平台曲库 —— 搜一首歌，拿回它的**身份**而不只是名字。

为什么值得单独一层
==================
发布页的配乐字段此前是一个自由文本框（`Track name to search`）：用户手打一个
曲名，浏览器侧拿它去抖音发布页的音乐弹窗里搜，搜到同名那行就点。用户看到的就
是「让我自己打歌名」，而他想要的是抖音官网那样**在侧边栏挑一首**。

这一层把"挑"变成可能：问平台自己的曲库，把封面/歌名/作者/时长/使用量整条卡片
交给前端，用户点中哪张，我们就带着那张卡片的 ``music_id`` 去发布（mig 429 /
``douyin_publish.judge_music_reference``）。

三条实测事实，决定了这层代码为什么长这样
========================================
（2026-08-15，全部经 `docker exec` 在容器内跑通，cookie 未离开进程）

1. **HTTP 200 在这条链上不构成成功信号。** creator 站对不存在的路由回
   ``{"status_code":1,"status_msg":"Url doesn't match"}``、对未登录回
   ``{"status_code":8}`` —— **两者都是 HTTP 200**。所以本模块判成功只看响应体
   里的 ``status_code == 0``；把 200 当成功会把"未登录"和"路由不存在"一起吞成
   一个静默的空列表。

2. **搜索要 ``Agw-Auth`` 头，而且 aid 换错不报错只回空。** 搜索走
   ``tsearch.amemv.com``，签名从 creator 站一个**带 cookie** 的接口换来
   （``/web/api/media/aweme/search/post/auth`` → ``signature``，121 字符，实测
   连续两次同值 → 可缓存，但 TTL 未知，所以按"随时会过期"实现：403 就作废重取
   一次再重试一次）。⚠️ 搜索本身的 ``aid`` 是 **1128**（抖音 App），不是
   creator 站的 2906 —— 传 2906 会 200 但 ``music:[]`` 且
   ``search_nil_info.search_nil_item = "invalid_app"``，又一个"看起来能搜、就是
   搜不出东西"的形态。

3. **``/music/list`` 接口静默忽略 keyword。** 六种参数名（keyword / search_key /
   query / q / music_name / search_keyword）全部被忽略，返回的仍是该 tab 的常规
   列表且 ``status_code=0``。**它不是搜索接口**，本模块不拿它当搜索用。

这条链比 ``topic_suggest`` 危险，不能照抄它的松弛度
==================================================
``topic_suggest`` 是匿名公共查询（无 cookie、无签名），可以随便打。这条不是：
换签名那一步**必须带账号 cookie**，等于每一次都算进那个账号的风控画像。所以

* **懒加载**：面板不打开就一个请求都不发（发布页初次渲染只需要已有的
  ``supports_music`` 能力声明，那不用问平台）；
* **签名是唯一带 cookie 的调用，且被缓存 10 分钟** —— 一个用户连着搜二十次，
  带身份的请求仍然只有一次；
* 取签名失败后有 ``SIGNATURE_COOLDOWN_S`` 的退避，避免一个"鉴权已经坏了"的
  状态被打字节奏放大成一串带身份的请求。这是**刻意的一次"缓存失败"**，与
  ``topic_suggest`` 那条"只缓存成功"的规矩相反，因为这里的代价不对称：那边多打
  几次只是浪费，这边多打几次是拿用户的账号去撞风控。

失败一律类型化，绝不静默空列表
==============================
"这个词没搜到"和"接口挂了"在面板里长得一模一样。所以成功路径才返回列表，其余
一律 ``raise MusicCatalogError``，带 ``reason`` 上浮成 HTTP，让前端能把两件事
说成两句话（CLAUDE.md「触发路径必须类型化失败回显」）。

⚠️ 空列表**几乎不会**是"没这首歌"：实测拿一个乱码关键词去搜仍回 8 条（模糊
召回）。所以空列表更可能是我们自己参数错了，前端文案照这个事实写，不写
"No results"。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

# ── 出网点（改签名/加风控时只改这三个常量 + 两个 _fetch） ──────

#: 换搜索签名。**必须带账号 cookie**，这是本模块唯一带身份的请求。
DOUYIN_AUTH_URL = "https://creator.douyin.com/web/api/media/aweme/search/post/auth"
#: 曲库搜索。``withCredentials:false`` —— 不带 cookie，只带 ``Agw-Auth``。
DOUYIN_SEARCH_URL = "https://tsearch.amemv.com/openapi/aweme/v1/music/search/"
#: 换签名那一步的 cookie 域。按 cookie 自己的 domain 挑，不是"全带上"。
DOUYIN_CREATOR_HOST = "creator.douyin.com"

#: creator 站的 app id（换签名用）。
AID_CREATOR = "2906"
#: 抖音 App 的 app id（**搜索用**）。两个 aid 各管一段，不能统一 —— 搜索传
#: 2906 会 200 且回空列表，是一个不报错的死胡同。
AID_APP = "1128"

#: 用户在等着看结果，给它一个远比默认短的预算。
CATALOG_TIMEOUT_SECONDS = 8.0

#: 一页最多回多少条。实测 ``count`` 是**上限不是数量**（传 10 回 6 条）。
SEARCH_PAGE_SIZE = 20
MAX_KEYWORD_LEN = 50

#: 搜索结果缓存。同一个词会被反复问到（退格重打、翻页来回、两个标签页）。
#: 与账号无关（搜索不带 cookie），所以一次拉取服务所有用户。
SEARCH_CACHE_TTL_SECONDS = 300
#: 缓存上界 —— 键来自用户输入，没有它就是一条按输入无限增长的内存路径。
CACHE_MAX_ENTRIES = 512

#: 签名缓存。TTL **未实测**，10 分钟是保守值；过期/被拒时会作废重取。
SIGNATURE_TTL_SECONDS = 600
#: 取签名失败后的退避。见模块头：这一步是唯一带账号身份的请求。
SIGNATURE_COOLDOWN_S = 60

SUPPORTED_PLATFORMS = frozenset({"douyin"})

# ── 类型化失败原因（前端按 code 出文案，绝不正则匹配英文） ─────

REASON_PLATFORM_UNSUPPORTED = "platform_unsupported"
REASON_KEYWORD_EMPTY = "keyword_empty"
#: 这个账号不是扫码会话绑定的 —— 换不到签名，也就搜不了。
REASON_ACCOUNT_NOT_SESSION = "account_not_session_bound"
#: 会话没了/解不开：用户要重新扫码。与"上游挂了"必须分开，两者的下一步完全不同。
REASON_SESSION_UNUSABLE = "session_unusable"
#: 换不到 ``Agw-Auth``（含退避期内的快速失败）。
REASON_SIGNATURE_UNAVAILABLE = "signature_unavailable"
REASON_UPSTREAM_UNREACHABLE = "upstream_unreachable"
#: 上游 HTTP 状态不对。
REASON_UPSTREAM_STATUS = "upstream_status"
#: 响应体形状不对，**含业务码非 0** —— 那是 200 伪装成功的那一族。
REASON_UPSTREAM_SHAPE = "upstream_shape"


class MusicCatalogError(Exception):
    """一次曲库查询失败，带类型化 reason 与建议 HTTP 状态。

    ``http_status`` 三档：400 = 请求本身不成立；409 = 账号那边要人动手
    （重新扫码）；502 = 上游的问题。前端要区分的正是这三种下一步。
    """

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        http_status: int = 502,
        retry_after_s: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.http_status = http_status
        #: 退避还剩多久（仅 ``signature_unavailable`` 的退避档给）。前端据此
        #: 说"稍后再试"而不是"坏了"——两句话对应两种真相。
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class MusicTrack:
    """曲库里的一首歌，**照抄上游给的字段，不加工**。

    ⚠️ ``music_id`` 取的是上游的 ``id_str``，不是 ``id``。同一条结果里两者并存
    （[实测 2026-08-15] ``id`` 是 JSON number ``6953836671917951012``，超过
    2^53），只有前者经 JSON 到前端还是同一个数。这是 CLAUDE.md「Snowflake
    BIGINT 精度丢失」的外部版本，差别是这次连后端也会踩。

    ``duration`` 是**秒**（[实测 2026-08-15] 同一次搜索回 49 / 221 / 323 / 267，
    量级只可能是秒）。这一条重要到值得单独实测：它是发布时"哪一行才是用户点的
    那一首"三元组里的一维，单位搞错的表现不是报错，是每一次选择都变成"发布时
    找不到那首歌"。
    """

    music_id: str
    title: str
    author: str
    duration: int
    user_count: int
    cover_url: str
    play_url: str


@dataclass(frozen=True)
class MusicSearchResult:
    tracks: list[MusicTrack]
    #: 下一页的游标，**原样回传上游给的值**（各 tab 的游标语义不同，自己算会错）。
    cursor: int
    has_more: bool
    cached: bool


# key: (keyword_lower, cursor) → (expires_at_monotonic, result)
_SEARCH_CACHE: dict[
    tuple[str, int], tuple[float, tuple[list[MusicTrack], int, bool]]
] = {}
# 全局签名缓存：(expires_at_monotonic, signature)
_SIGNATURE: Optional[tuple[float, str]] = None
# 取签名失败后的退避截止时刻（monotonic）
_SIGNATURE_COOLDOWN_UNTIL: float = 0.0


def clear_catalog_cache() -> None:
    """测试用（也可作为运维手段）：把进程内缓存与退避全部清空。"""
    global _SIGNATURE, _SIGNATURE_COOLDOWN_UNTIL
    _SEARCH_CACHE.clear()
    _SIGNATURE = None
    _SIGNATURE_COOLDOWN_UNTIL = 0.0


def normalize_keyword(raw: str) -> str:
    """把用户输入收成一个可查的词：去空白、截到上限。"""
    return (raw or "").strip()[:MAX_KEYWORD_LEN]


def _cache_put(key: tuple[str, int], value: tuple[list[MusicTrack], int, bool]) -> None:
    if len(_SEARCH_CACHE) >= CACHE_MAX_ENTRIES:
        now = time.monotonic()
        for k in [k for k, (exp, _) in _SEARCH_CACHE.items() if exp <= now]:
            _SEARCH_CACHE.pop(k, None)
        if len(_SEARCH_CACHE) >= CACHE_MAX_ENTRIES:
            _SEARCH_CACHE.pop(
                min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0]), None
            )
    _SEARCH_CACHE[key] = (time.monotonic() + SEARCH_CACHE_TTL_SECONDS, value)


def _cache_get(
    key: tuple[str, int],
) -> Optional[tuple[list[MusicTrack], int, bool]]:
    hit = _SEARCH_CACHE.get(key)
    if hit is None:
        return None
    expires_at, value = hit
    if expires_at <= time.monotonic():
        _SEARCH_CACHE.pop(key, None)
        return None
    return value


# ── 纯函数：上游 JSON → 我们的形状（可单测，不碰网络） ─────────


def _require_ok(payload: Any, *, what: str) -> Mapping[str, Any]:
    """成功的判据：是个对象 **且 ``status_code == 0``**。

    这一函数是模块头第 1 条事实的落点。HTTP 状态在别处已经判过，但那不够 ——
    路由不存在（1）与未登录（8）都是 HTTP 200。
    """
    if not isinstance(payload, Mapping):
        raise MusicCatalogError(
            REASON_UPSTREAM_SHAPE, f"{what}: upstream returned a non-object body"
        )
    status = payload.get("status_code")
    if status not in (0, None):
        raise MusicCatalogError(
            REASON_UPSTREAM_SHAPE,
            f"{what}: upstream status_code={status!r} "
            f"({payload.get('status_msg') or 'no message'})",
        )
    return payload


def _cover_of(row: Mapping[str, Any]) -> str:
    """封面 URL：按清晰度从高到低取第一个有 ``url_list`` 的。

    上游给四个尺寸（``cover_hd`` / ``large`` / ``medium`` / ``thumb``），每个是
    ``{uri, url_list[], width, height}``。缺失是常态（部分曲目没有封面），返回
    空串让前端画占位，而不是让整条结果被丢掉。
    """
    for key in ("cover_medium", "cover_large", "cover_hd", "cover_thumb"):
        value = row.get(key)
        if not isinstance(value, Mapping):
            continue
        urls = value.get("url_list")
        if isinstance(urls, Sequence) and not isinstance(urls, (str, bytes)):
            for url in urls:
                if isinstance(url, str) and url.startswith("http"):
                    return url
    return ""


def _first_url(value: Any) -> str:
    if isinstance(value, Mapping):
        urls = value.get("url_list")
        if isinstance(urls, Sequence) and not isinstance(urls, (str, bytes)):
            for url in urls:
                if isinstance(url, str) and url.startswith("http"):
                    return url
    return ""


def parse_search_payload(payload: Any) -> tuple[list[MusicTrack], int, bool]:
    """搜索响应 → (曲目, 下一页游标, 还有没有)。纯函数。

    形状不对就 raise ``upstream_shape`` —— 这正是"上游哪天改了字段"最先撞上的
    那道门，它必须响，不能悄悄回空列表。

    ⚠️ ``has_more`` 上游给的是 **1/0 整数**（不是 JSON bool），``cursor`` 是整数
    偏移。照抄它们的真实形状，不"美化"。
    """
    body = _require_ok(payload, what="music search")
    rows = body.get("music")
    if rows is None:
        raise MusicCatalogError(REASON_UPSTREAM_SHAPE, "search body has no music list")
    if not isinstance(rows, list):
        raise MusicCatalogError(REASON_UPSTREAM_SHAPE, "search music is not a list")

    tracks: list[MusicTrack] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        # id_str，永远不是 id：见 MusicTrack 的说明。一条没有 id_str 的结果对
        # 我们没有用处（它无法成为一个身份），丢掉好过带着半个身份往下走。
        music_id = str(row.get("id_str") or "").strip()
        title = str(row.get("title") or "").strip()
        if not music_id or not title:
            continue
        try:
            duration = max(0, int(row.get("duration") or 0))
        except (TypeError, ValueError):
            duration = 0
        try:
            user_count = max(0, int(row.get("user_count") or 0))
        except (TypeError, ValueError):
            user_count = 0
        tracks.append(
            MusicTrack(
                music_id=music_id,
                title=title,
                author=str(row.get("author") or "").strip(),
                duration=duration,
                user_count=user_count,
                cover_url=_cover_of(row),
                play_url=_first_url(row.get("play_url")),
            )
        )

    try:
        cursor = int(body.get("cursor") or 0)
    except (TypeError, ValueError):
        cursor = 0
    # 1/0 与 true/false 都认；缺失当没有下一页。
    has_more = bool(body.get("has_more"))
    return tracks, cursor, has_more


def parse_signature(payload: Any) -> str:
    body = _require_ok(payload, what="search auth")
    signature = str(body.get("signature") or "").strip()
    if not signature:
        raise MusicCatalogError(
            REASON_SIGNATURE_UNAVAILABLE,
            "the creator site returned no search signature",
        )
    return signature


# ── 出网 ──────────────────────────────────────────────────────


async def _get_json(url: str, *, params: dict, headers: dict) -> Any:
    """**唯一出网点**。返回解析后的 JSON；成功判定留给 ``_require_ok``。

    ``safe_async_client`` 而不是裸 httpx —— 本仓库对外发请求的唯一入口（SSRF
    校验 + 跨源重定向剥 Cookie）。``trust_env=False``：宿主机可能带
    HTTP(S)_PROXY，让一次面向国内站点的查询绕地球一圈只会更慢更容易超时。
    """
    from app.boundary import safe_async_client

    async with safe_async_client(
        timeout=CATALOG_TIMEOUT_SECONDS, trust_env=False
    ) as client:
        response = await client.get(url, params=params, headers=headers)
        if response.status_code != 200:
            raise MusicCatalogError(
                REASON_UPSTREAM_STATUS,
                f"upstream returned HTTP {response.status_code}",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MusicCatalogError(
                REASON_UPSTREAM_SHAPE, "upstream body was not JSON"
            ) from exc


async def _account_cookie_header(account_id: int) -> str:
    """这个账号在 creator 站的 cookie 头。明文只活在这一帧，不进日志不进返回值。

    失败一律是 ``session_unusable``：会话没了、解不开、或者这个域下一条 cookie
    都没有 —— 三种都指向同一个下一步（重新扫码），而它们与"上游挂了"是完全
    不同的两件事。
    """
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
        SocialAccountsRepository,
    )
    from app.services.distribution.session_adapter import (
        AUTH_TYPE_SESSION,
        SessionStateError,
        parse_session_state,
    )
    from app.services.distribution.session_probe import cookies_for_host

    acct = await SocialAccountsRepository().get_with_session(int(account_id))
    if not acct:
        raise MusicCatalogError(
            REASON_SESSION_UNUSABLE, "account not found", http_status=409
        )
    if acct.get("auth_type") != AUTH_TYPE_SESSION:
        raise MusicCatalogError(
            REASON_ACCOUNT_NOT_SESSION,
            "this account was not connected by QR code, so the platform's music "
            "catalogue cannot be browsed with it",
            http_status=400,
        )
    if acct.get(SESSION_STATE_DECRYPT_FAILED):
        raise MusicCatalogError(
            REASON_SESSION_UNUSABLE,
            "the stored session could not be decrypted; reconnect the account",
            http_status=409,
        )
    try:
        storage_state = parse_session_state(acct)
    except SessionStateError as exc:
        raise MusicCatalogError(
            REASON_SESSION_UNUSABLE, str(exc), http_status=409
        ) from exc

    cookies = storage_state.get("cookies")
    jar = cookies_for_host(
        cookies if isinstance(cookies, list) else [], DOUYIN_CREATOR_HOST
    )
    if not jar:
        raise MusicCatalogError(
            REASON_SESSION_UNUSABLE,
            "the stored session carries no cookies for the creator site; "
            "reconnect the account",
            http_status=409,
        )
    return "; ".join(f"{name}={value}" for name, value in jar)


async def _signature(account_id: int, *, force_refresh: bool = False) -> str:
    """搜索用的 ``Agw-Auth``，缓存 10 分钟。**唯一带账号 cookie 的调用。**

    ``force_refresh`` 只由 403 那条重试路径传 —— 签名的真实 TTL 未实测，所以
    "被拒了就作废重取一次"是我们对它唯一可靠的假设。
    """
    global _SIGNATURE, _SIGNATURE_COOLDOWN_UNTIL

    now = time.monotonic()
    if not force_refresh and _SIGNATURE is not None:
        expires_at, cached = _SIGNATURE
        if expires_at > now:
            return cached

    if _SIGNATURE_COOLDOWN_UNTIL > now:
        # 刻意的一次"缓存失败"。理由在模块头：这一步带用户的账号身份，把一个
        # 已经坏了的鉴权按打字节奏重打，是拿用户的账号去撞风控。
        raise MusicCatalogError(
            REASON_SIGNATURE_UNAVAILABLE,
            "backing off after a failed authorisation with the creator site",
            retry_after_s=max(1, int(_SIGNATURE_COOLDOWN_UNTIL - now)),
        )

    cookie = await _account_cookie_header(account_id)
    try:
        payload = await _get_json(
            DOUYIN_AUTH_URL,
            params={"aid": AID_CREATOR},
            headers={"cookie": cookie},
        )
        signature = parse_signature(payload)
    except MusicCatalogError:
        _SIGNATURE_COOLDOWN_UNTIL = time.monotonic() + SIGNATURE_COOLDOWN_S
        raise
    except Exception as exc:  # 网络/超时/DNS/SSRF 拦截
        _SIGNATURE_COOLDOWN_UNTIL = time.monotonic() + SIGNATURE_COOLDOWN_S
        logger.warning(f"[music_catalog] auth call failed: {type(exc).__name__}")
        raise MusicCatalogError(
            REASON_UPSTREAM_UNREACHABLE,
            f"could not reach the creator site ({type(exc).__name__})",
        ) from exc

    _SIGNATURE = (time.monotonic() + SIGNATURE_TTL_SECONDS, signature)
    _SIGNATURE_COOLDOWN_UNTIL = 0.0
    return signature


async def search_music(
    *, platform: str, keyword: str, account_id: int, cursor: int = 0
) -> MusicSearchResult:
    """搜平台曲库。成功返回结果（可能为空），失败 raise。

    ``account_id`` 只用来换签名 —— 搜索本身不带 cookie，结果与身份无关，所以
    缓存也不按账号分。调用方（路由）负责先确认这个账号属于调用者。
    """
    platform = (platform or "").strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise MusicCatalogError(
            REASON_PLATFORM_UNSUPPORTED,
            f"there is no music catalogue we can search for platform {platform!r}",
            http_status=400,
        )

    word = normalize_keyword(keyword)
    if not word:
        raise MusicCatalogError(
            REASON_KEYWORD_EMPTY,
            "type something to search the platform's music library",
            http_status=400,
        )

    cursor = max(0, int(cursor or 0))
    key = (word.lower(), cursor)
    cached = _cache_get(key)
    if cached is not None:
        tracks, next_cursor, has_more = cached
        return MusicSearchResult(
            tracks=tracks, cursor=next_cursor, has_more=has_more, cached=True
        )

    signature = await _signature(account_id)
    params = {
        "aid": AID_APP,  # 1128，不是 2906 —— 见模块头第 2 条
        "count": str(SEARCH_PAGE_SIZE),
        "search_source": "normal_search",
        "keyword": word,
        "cursor": str(cursor),
    }
    headers = {"Agw-Auth": signature, "Openapi-Omit-Shark": "1"}

    try:
        payload = await _get_json(DOUYIN_SEARCH_URL, params=params, headers=headers)
    except MusicCatalogError as exc:
        # 403 = 签名过期（TTL 未实测，所以这是唯一可靠的判据）。作废重取一次，
        # 再失败才算真失败 —— 但**只重试一次**，不做指数退避循环：用户在等。
        if exc.reason != REASON_UPSTREAM_STATUS or "403" not in exc.message:
            raise
        signature = await _signature(account_id, force_refresh=True)
        headers["Agw-Auth"] = signature
        payload = await _get_json(DOUYIN_SEARCH_URL, params=params, headers=headers)
    except Exception as exc:
        logger.warning(f"[music_catalog] search call failed: {type(exc).__name__}")
        raise MusicCatalogError(
            REASON_UPSTREAM_UNREACHABLE,
            f"could not reach the music search upstream ({type(exc).__name__})",
        ) from exc

    tracks, next_cursor, has_more = parse_search_payload(payload)
    # 只缓存成功。缓存一次失败 = 把一次抖动放大成 5 分钟的功能不可用。
    _cache_put(key, (tracks, next_cursor, has_more))
    return MusicSearchResult(
        tracks=tracks, cursor=next_cursor, has_more=has_more, cached=False
    )


__all__ = [
    "MusicCatalogError",
    "MusicSearchResult",
    "MusicTrack",
    "MAX_KEYWORD_LEN",
    "REASON_ACCOUNT_NOT_SESSION",
    "REASON_KEYWORD_EMPTY",
    "REASON_PLATFORM_UNSUPPORTED",
    "REASON_SESSION_UNUSABLE",
    "REASON_SIGNATURE_UNAVAILABLE",
    "REASON_UPSTREAM_SHAPE",
    "REASON_UPSTREAM_STATUS",
    "REASON_UPSTREAM_UNREACHABLE",
    "clear_catalog_cache",
    "normalize_keyword",
    "parse_search_payload",
    "parse_signature",
    "search_music",
]
