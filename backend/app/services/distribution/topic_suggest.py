"""话题实时建议 —— 平台自己的话题库，问一次就有。

为什么值得单独一层
==================
发布页的话题输入框此前只有一个"打字 → 变成 chip"的本地行为，外加三个**写死
的英文词**顶着「Trending」的名字（`goldenhour` / `cityscape` / `4k`）。那是一
个假声明：它既不热门、也不来自任何平台，用户点它得到的只是一个我们编的词。

真实数据来自 creator 站自己的建议接口 —— 抖音发布页里输入 `#南通` 弹出的那份
带累计播放量的下拉，就是它。PR #1830 的打字式勘探把这条链问清楚了（A–K 十一
组对照）：

    GET https://creator.douyin.com/aweme/v1/search/challengesug/?aid=2906&keyword=<词>

**最小可用请求就这两个参数**：无 cookie、无 `a_bogus`、无 `msToken`、无 UA、
无 referer，全部 200。``aid`` 是唯一必需的（去掉它 ``sug_list`` 长度为 0）。
实测单次 138–289ms，15 并发 323ms 全成功。

上游随时可能加签名，所以调用点收敛成一个函数
============================================
它是 creator 站的**私有接口**，没有任何契约保证今天能打明天还能打。所以整条
链只有 ``_fetch_douyin_sug_list`` 一个出网点：真到了要加签名/带 cookie 的那
天，改动落在一个函数里，而不是散落在路由和前端。降级梯子（按代价递增）：

  ① 自签 ``a_bogus`` —— 仓库已有签名器
     ``services/media/parsers/douyin_parse/_f2_abogus.py``，勘探时实测自签也
     200，属于纯本地计算，不需要账号。
  ② 带账号 cookie（并考虑走该账号的 ``account_environments.proxy_url``）——
     代价是这条链从"匿名的公共查询"变成"以某个账号的身份查询"，风控画像与出
     口都跟着变，所以是最后一档，不是第一档。

失败必须类型化，绝不静默返回空列表
==================================
"没搜到这个词"和"接口挂了"在 UI 上长得一模一样 —— 都是一个空下拉。本仓库在
``attachment_failures`` 上已经栽过一次同族的问题（后端返回了原因，前端从来没
读）。所以这一层**只有成功路径返回列表**，任何上游异常都 ``raise
TopicSuggestError``，带 ``reason`` 码上浮到 HTTP，让前端能把两件事说成两句话。

⚠️ 空列表本身是**合法结果**：上游 200 且 ``status_code=0`` 但 ``sug_list``
为空，就是"这个词平台没有建议"。它走成功路径，前端说 "No topics found"。

⚠️ 本层**不碰发布时怎么把话题打进抖音**。勘探发现纯打字路径的 DOM 上没有实体
节点，但那是强证据不是定论（平台可能在服务端解析 `#词`）。要定论得真发一条再
回读作品的 ``text_extra``。在此之前我们只做一件事：把 ``topic_id``（上游的
``cid``）**采下来存好**，将来才有东西可比。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

# 唯一出网点。改这里 = 改整条链。
DOUYIN_SUGGEST_URL = "https://creator.douyin.com/aweme/v1/search/challengesug/"
# 抖音 creator 站的 app id。勘探结论：**唯一必需参数**，去掉它上游回空列表
# （200 但 sug_list=0），所以它不是"顺手带上的"，是这条链成立的前提。
DOUYIN_AID = "2906"

# 这条链是打字联想，用户在等着看下拉。给它一个远比默认 30s 短的预算：超时了
# 说一句"暂时问不到"，比让输入框转 30 秒圈要诚实得多。上游实测 138–289ms。
SUGGEST_TIMEOUT_SECONDS = 6.0

# 短 TTL 缓存。去抖之后同一个词仍会被反复问到（退格重打、多账号来回切、
# 两个浏览器标签），5 分钟足够把这些压掉，又不会让"平台刚出的新话题"迟到太久。
CACHE_TTL_SECONDS = 300
# 缓存上界 —— 这是个进程内 dict，没有它就是一条按用户输入无限增长的内存路径。
CACHE_MAX_ENTRIES = 512

MAX_KEYWORD_LEN = 50
# 上游一次回 10 条；这个上界只是防止上游哪天改成回 200 条时我们原样转发。
MAX_SUGGESTIONS = 20

# 目前只有抖音有实现。别的平台**明说不支持**，不假装（返回空列表等于告诉用户
# "这个词没有话题"，而真相是"我们根本没问"）。
SUPPORTED_PLATFORMS = frozenset({"douyin"})

# ── 类型化失败原因（前端按 code 出文案，绝不正则匹配英文句子） ──
REASON_PLATFORM_UNSUPPORTED = "platform_unsupported"
REASON_KEYWORD_EMPTY = "keyword_empty"
REASON_UPSTREAM_UNREACHABLE = "upstream_unreachable"
REASON_UPSTREAM_STATUS = "upstream_status"
REASON_UPSTREAM_SHAPE = "upstream_shape"


class TopicSuggestError(Exception):
    """一次话题建议失败，带类型化 reason 与建议 HTTP 状态。

    ``http_status`` 分两档：400 = 请求本身不对（平台不支持、关键词为空），
    502 = 上游的问题（网络、状态码、响应形状）。前端只需要区分"我填错了"和
    "外面挂了"，所以两档够用。
    """

    def __init__(self, reason: str, message: str, *, http_status: int = 502) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class TopicSuggestion:
    """一条话题建议。

    ``topic_id`` 是上游的 ``cid`` —— 话题**实体 id**。新话题上游回 ``""``，
    那不是缺数据，是"平台还没有这个实体"的标记，所以原样保留并用
    ``is_new`` 把它显式说出来，而不是让前端去猜一个空串的含义。

    ``view_count`` 是累计播放量的**原始整数**（UI 上的 `7210.8亿` 是格式化出
    来的）。格式化留给前端：后端存原始值，换个语言/换个量级单位不用改后端。
    """

    name: str
    topic_id: str
    view_count: int
    is_new: bool


@dataclass(frozen=True)
class TopicSuggestResult:
    suggestions: list[TopicSuggestion]
    cached: bool


# key: (platform, normalized keyword) → (expires_at_monotonic, suggestions)
_CACHE: dict[tuple[str, str], tuple[float, list[TopicSuggestion]]] = {}


def clear_suggest_cache() -> None:
    """测试用（也可作为运维手段）：把进程内缓存清空。"""
    _CACHE.clear()


def normalize_keyword(raw: str) -> str:
    """把用户输入收成一个可查的词：去空白、剥前导 '#'（输入框里带不带 # 是用户
    习惯，不该变成两个不同的缓存键）、截到长度上限。"""
    return raw.strip().lstrip("#").strip()[:MAX_KEYWORD_LEN]


def _cache_get(key: tuple[str, str]) -> Optional[list[TopicSuggestion]]:
    hit = _CACHE.get(key)
    if hit is None:
        return None
    expires_at, value = hit
    if expires_at <= time.monotonic():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: tuple[str, str], value: list[TopicSuggestion]) -> None:
    if len(_CACHE) >= CACHE_MAX_ENTRIES:
        # 先扫一遍过期项；真的全是活的就丢最早到期的那条。粗糙但足够 —— 这是
        # 个几百条的字典，不值得为它引入一个 LRU 依赖。
        now = time.monotonic()
        for k in [k for k, (exp, _) in _CACHE.items() if exp <= now]:
            _CACHE.pop(k, None)
        if len(_CACHE) >= CACHE_MAX_ENTRIES:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
    _CACHE[key] = (time.monotonic() + CACHE_TTL_SECONDS, value)


def parse_sug_list(payload: Any) -> list[TopicSuggestion]:
    """上游 JSON → 我们的形状。纯函数，可单测，不碰网络。

    形状不对就 raise ``upstream_shape`` —— 这正是"上游哪天加了签名/换了字段"
    最先撞上的那道门，它必须响，不能悄悄回空列表。
    """
    if not isinstance(payload, dict):
        raise TopicSuggestError(
            REASON_UPSTREAM_SHAPE, "upstream returned a non-object body"
        )
    # status_code 是上游自己的业务码，0 = ok。非 0 时 sug_list 往往也在，但那是
    # 一份不该信的列表（典型：风控要求验证）。
    status_code = payload.get("status_code")
    if status_code not in (0, None):
        raise TopicSuggestError(
            REASON_UPSTREAM_SHAPE,
            f"upstream status_code={status_code!r}",
        )
    sug_list = payload.get("sug_list")
    if sug_list is None:
        raise TopicSuggestError(REASON_UPSTREAM_SHAPE, "upstream body has no sug_list")
    if not isinstance(sug_list, list):
        raise TopicSuggestError(REASON_UPSTREAM_SHAPE, "sug_list is not a list")

    out: list[TopicSuggestion] = []
    for item in sug_list[:MAX_SUGGESTIONS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("cha_name") or "").strip()
        if not name:
            continue
        cid = str(item.get("cid") or "")
        raw_views = item.get("view_count")
        try:
            views = int(raw_views)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            views = 0
        out.append(
            TopicSuggestion(
                name=name,
                topic_id=cid,
                view_count=max(views, 0),
                # cid 为空 = 平台还没有这个话题实体（用户会是第一个用它的人）。
                is_new=not cid,
            )
        )
    return out


async def _fetch_douyin_sug_list(keyword: str) -> Any:
    """**唯一出网点**。返回上游解析后的 JSON（不做形状判断，那是 parse 的事）。

    ``safe_async_client`` 而不是裸 httpx —— 本仓库对外发请求的唯一入口（SSRF
    校验 + 跨源重定向剥 Cookie）。``trust_env=False``：宿主机可能带
    HTTP(S)_PROXY，让一次面向国内 creator 站的查询绕地球一圈只会更慢更容易
    超时；这条链要么直连要么明说失败，不受本机环境摆布。

    刻意**不带账号 cookie、不走账号代理**：这是一次匿名的公共查询，发生在用户
    还没选账号（甚至可能选多个账号）的时候，硬绑某一个账号的出口既没有依据，
    也会把这条只读链算进那个账号的风控画像。真被限流了，降级梯子见模块头。
    """
    from app.boundary import safe_async_client

    params = {"aid": DOUYIN_AID, "keyword": keyword}
    async with safe_async_client(
        timeout=SUGGEST_TIMEOUT_SECONDS, trust_env=False
    ) as client:
        response = await client.get(DOUYIN_SUGGEST_URL, params=params)
        if response.status_code != 200:
            raise TopicSuggestError(
                REASON_UPSTREAM_STATUS,
                f"upstream returned HTTP {response.status_code}",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise TopicSuggestError(
                REASON_UPSTREAM_SHAPE, "upstream body was not JSON"
            ) from exc


async def suggest_topics(*, platform: str, keyword: str) -> TopicSuggestResult:
    """查一个平台的话题建议。成功返回列表（可能为空），失败 raise。"""
    platform = (platform or "").strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise TopicSuggestError(
            REASON_PLATFORM_UNSUPPORTED,
            f"topic suggestions are not implemented for platform {platform!r}",
            http_status=400,
        )

    word = normalize_keyword(keyword or "")
    if not word:
        raise TopicSuggestError(
            REASON_KEYWORD_EMPTY,
            "keyword is empty after trimming '#' and whitespace",
            http_status=400,
        )

    key = (platform, word.lower())
    cached = _cache_get(key)
    if cached is not None:
        return TopicSuggestResult(suggestions=cached, cached=True)

    try:
        payload = await _fetch_douyin_sug_list(word)
    except TopicSuggestError:
        raise
    except Exception as exc:  # 网络/超时/DNS/SSRF 拦截，全部归"够不着上游"
        logger.warning(f"[topic_suggest] upstream call failed: {type(exc).__name__}")
        raise TopicSuggestError(
            REASON_UPSTREAM_UNREACHABLE,
            f"could not reach the topic suggestion upstream ({type(exc).__name__})",
        ) from exc

    suggestions = parse_sug_list(payload)
    # 只缓存成功。缓存一次失败 = 把一次抖动放大成 5 分钟的功能不可用。
    _cache_put(key, suggestions)
    return TopicSuggestResult(suggestions=suggestions, cached=False)
