"""打字式勘探的 backend 侧：取锁 → 让浏览器打字 → **不开浏览器**再调一次。

这一层为什么不是 ``session_inspect`` 的一个参数
==============================================
T0 那条链回答的是"这一页上有什么"。本链回答的是另一类问题："页面在**我打字
的时候**去取了什么数据"——抖音描述框里输入 ``#南通`` 会弹出带累计播放量的
官方话题下拉，那份数据的来源决定了我们能不能在自己的发布页做同样的体验。

而"能不能做"这件事，光看一眼请求是**下不了结论**的：URL 里有没有
``a_bogus`` / ``msToken`` 这类签名参数、签名跟关键词绑不绑，直接决定方案是
"带 cookie 直接调"还是"每次都得驱动一个浏览器"。所以这条链比 T0 多一步，而
且那一步是它存在的主要理由：

    浏览器操作 → 抓到那条请求 → **在这里、用 httpx、不开浏览器**跑一遍阶梯
      ① 原样重放           → 这条 URL 离开浏览器还成不成立
      ② 只改关键词重放     → 签名跟关键词绑不绑（这条才是决定性的）
      ③ 只去掉 cookie      → 是不是"必须以某个账号身份"才能问
      ④ 连 UA/referer 也去 → 还剩不剩浏览器痕迹依赖
      ⑤ 参数只留白名单     → 最小可用请求的候选
      ⑥ 逐个再拿掉一个     → 其中**哪一个**是真必需的

②成立就意味着我们可以自己拼 URL、自己发；②失败而①成功，说明签名是一次性的，
只能靠浏览器现算。**"看起来是个普通 GET 所以能直接调"不是结论**，这几次实调
才是。⑥ 是"最小"这个词的唯一依据 —— 只做到 ⑤ 得到的是"第一个碰巧能用的组
合"。话题那次的结论「``aid`` 是唯一必需的」就是这么问出来的。

⚠️ 目标不再限于"带了我们输入的那个词"的请求。面板一打开就加载的推荐/榜单
列表天生不带关键词，而它恰恰是"能不能在自己界面里列出来"要问的那一条；浏览
器侧用 ``replay_url_contains`` 把这类调用也标成可重放目标。

明文纪律（spec §7.6）与它的一个新面
==================================
``session_state`` 明文只在本函数这一帧；照旧。新增的是 ``replay_targets``：
浏览器侧回传的**未脱敏完整 URL**（可能带 msToken/签名）。它跟明文会话同级 ——
只在这一帧里被 httpx 消费，**不进返回值、不进日志**（``test_session_probe``
逐条守着）。返回给调用方的只有结构性事实：状态码、body 长度、JSON 顶层键、
以及"改了关键词之后 body 里有没有出现新词"这个布尔量。

``attempts=1``
==============
与 T0 同理：抢不到账号锁说明那个账号此刻正在发布或巡检，勘探让开。
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from loguru import logger

REASON_ACCOUNT_MISSING = "account_missing"
REASON_ACCOUNT_BUSY = "account_busy"
REASON_AUTH_TYPE_MISMATCH = "auth_type_mismatch"
REASON_PLATFORM_UNSUPPORTED = "platform_unsupported"

# 重放最多打几条。抓到的可重放目标通常只有一条（携带了我们输入的那个词的那
# 条）；上限存在只是为了让"页面同时发了两条候选接口"这种情况也能一次问清，
# 而不是让一次勘探变成压测。
MAX_REPLAY_TARGETS = 4
REPLAY_TIMEOUT_SECONDS = 20.0

# 一次勘探最多真发这么多次。阶梯（见 `_replay_ladder`）每个目标 4 + N 次，
# N 是保留参数个数；这个上界是为了让"多开几个 tab"不至于变成对上游的压测。
MAX_REPLAY_CALLS = 40

# 重放响应只读这么多字节。我们不回显 body（形状已经由浏览器侧那份脱敏摘要
# 给过了），读它只是为了数长度、解析 JSON 顶层键、判断新关键词在不在里面。
MAX_REPLAY_BODY_BYTES = 512 * 1024


def _envelope(
    status: str,
    message: str,
    *,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    """§7.8 信封 + 勘探自己的字段。失败路径永远走这里。"""
    return {
        "success": False,
        "status": status,
        "message": message,
        "detail": {"reason": reason, **extra},
        "observation": {},
        "replay": [],
        "session_refreshed": False,
    }


# ── 纯函数：cookie 挑选与 URL 变形（可单测，不碰网络） ─────────


def cookies_for_host(
    cookies: Sequence[Mapping[str, Any]], host: str
) -> list[tuple[str, str]]:
    """这个 host 该带哪些 cookie。纯函数。

    按 cookie 自己的 ``domain`` 匹配，而不是"全带上"。全带上会把 A 站点的
    会话 cookie 发给 B 站点 —— 在勘探里那是把凭证送到一个我们没打算送的地
    方，即使两者都属于同一个平台。前导点表示"含子域"，是 cookie 规范本来的
    语义，这里照抄，不发明。
    """
    target = (host or "").lower().strip(".")
    if not target:
        return []
    out: list[tuple[str, str]] = []
    for cookie in cookies or []:
        if not isinstance(cookie, Mapping):
            continue
        name = str(cookie.get("name") or "")
        if not name:
            continue
        domain = str(cookie.get("domain") or "").lower().strip()
        if not domain:
            continue
        if domain.startswith("."):
            base = domain[1:]
            matches = target == base or target.endswith("." + base)
        else:
            matches = target == domain
        if matches:
            out.append((name, str(cookie.get("value") or "")))
    return out


def swap_query_value(url: str, param: str, value: str) -> str:
    """把 URL 里某个查询参数换成新值，其余原封不动。纯函数。

    刻意保留全部其它参数（含签名参数）—— 这正是实验设计：**只**动关键词，
    看签名还认不认。重建一遍 query 而不是做字符串替换，是因为关键词可能是
    中文，编码形式不止一种，字符串替换会漏。
    """
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(name == param for name, _ in pairs):
        return url
    swapped = [(name, value if name == param else item) for name, item in pairs]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(swapped), parts.fragment)
    )


def keep_query_params(url: str, keep: Sequence[str]) -> str:
    """只留下 ``keep`` 里的查询参数，顺序保持原样。纯函数。

    "最小可用请求"不能靠看一眼 URL 猜。话题那次剥到只剩 ``aid+keyword``，是
    **一次一次真调**出来的；这个函数只是把"剥"这个动作写成可复现的一步，而不
    是每次勘探都现写一段脚本。
    """
    parts = urlsplit(url)
    wanted = {str(k) for k in keep}
    pairs = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name in wanted
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment)
    )


def drop_query_param(url: str, name: str) -> str:
    """去掉一个查询参数，其余不动。纯函数。

    与 ``keep_query_params`` 配对使用：先剥到一个还能用的小集合，再逐个拿掉，
    才知道**哪一个是必需的**。少了这一步，"最小"只是"我们试出来的第一个能用
    的组合"，不是最小。
    """
    parts = urlsplit(url)
    pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != name
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment)
    )


def summarise_replay_body(raw: str, *, needles: Mapping[str, str]) -> dict[str, Any]:
    """一次重放响应的**结构性**摘要。纯函数，不回显内容。

    不带 body 摘要是刻意的：内容形状浏览器侧那份脱敏捕获已经给过了，这里再
    带一份就等于把同一套脱敏规则在两个服务里各写一遍 —— 而两份脱敏规则迟早
    会分叉。这里只回答"它答没答、答的是不是我们要的东西"。
    """
    out: dict[str, Any] = {"body_chars": len(raw or "")}
    for label, needle in (needles or {}).items():
        out[f"contains_{label}"] = bool(needle) and needle in (raw or "")
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        out["json"] = False
        return out
    out["json"] = True
    if isinstance(parsed, Mapping):
        out["json_top_keys"] = sorted(str(k) for k in parsed)[:40]
        # 抖音 web 接口惯例：业务成功与否在 status_code 里，HTTP 一律 200。
        # 只把它当**线索**报出去，不在这里替调用方下判断。
        for key in ("status_code", "status", "code"):
            if key in parsed and isinstance(parsed[key], (int, str)):
                out["json_status_field"] = f"{key}={parsed[key]}"
                break
        for key, value in parsed.items():
            if isinstance(value, list):
                out["first_list_key"] = str(key)
                out["first_list_len"] = len(value)
                break
    elif isinstance(parsed, list):
        out["json_top_keys"] = []
        out["first_list_len"] = len(parsed)
    return out


# ── 重放（唯一会碰外网的部分） ────────────────────────────────


async def _replay_once(
    client: Any,
    url: str,
    *,
    headers: Mapping[str, str],
    needles: Mapping[str, str],
) -> dict[str, Any]:
    """打一次，永远返回类型化结果 —— 重放失败是**发现**，不是异常。"""
    try:
        response = await client.get(url, headers=dict(headers))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    raw = response.text or ""
    if len(raw.encode("utf-8", "ignore")) > MAX_REPLAY_BODY_BYTES:
        raw = raw[: MAX_REPLAY_BODY_BYTES // 4]
    return {
        "ok": True,
        "http_status": int(response.status_code),
        "content_type": str(response.headers.get("content-type") or ""),
        **summarise_replay_body(raw, needles=needles),
    }


def _replay_ladder(
    url: str,
    *,
    keyword_param: Optional[str],
    mutation_text: str,
    keep_params: Sequence[str],
    full_headers: Mapping[str, str],
    bare_headers: Mapping[str, str],
) -> list[tuple[str, str, Mapping[str, str]]]:
    """``(标签, URL, headers)`` 的实验序列。纯函数，不发请求。

    顺序是按"每一步只动一个变量"排的，因为这条链的产物是一句结论 ——「最小可
    用请求是什么」—— 而同时动两样东西的实验答不出这句话：

    ① ``verbatim``      原样：这条 URL 离开浏览器还成不成立
    ② ``keyword``       只换关键词：签名跟关键词绑不绑（有关键词参数时才有）
    ③ ``no_cookie``     只去掉 cookie：这是不是一次"必须以某个账号身份"的查询
    ④ ``bare_headers``  连 UA / referer 也去掉：还剩不剩"浏览器痕迹"依赖
    ⑤ ``kept_params``   查询参数只留 ``keep_params``，配 ④ 的 headers
    ⑥ ``drop_<名>``     在 ⑤ 的基础上逐个拿掉 —— **哪一个才是必需的**

    ⑥ 存在的理由：只做到 ⑤ 得到的是"我们试出来的第一个能用的组合"，不是最小。
    话题那次的结论「``aid`` 是唯一必需的」正是 ⑥ 才能说出口的话。
    """
    plan: list[tuple[str, str, Mapping[str, str]]] = [("verbatim", url, full_headers)]
    if keyword_param and mutation_text:
        plan.append(
            (
                "keyword",
                swap_query_value(url, keyword_param, mutation_text),
                full_headers,
            )
        )
    no_cookie = {k: v for k, v in full_headers.items() if k != "cookie"}
    plan.append(("no_cookie", url, no_cookie))
    plan.append(("bare_headers", url, bare_headers))

    present = {
        name for name, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)
    }
    kept = [name for name in keep_params if name in present]
    if kept:
        minimal = keep_query_params(url, kept)
        plan.append(("kept_params", minimal, bare_headers))
        for name in kept:
            plan.append((f"drop_{name}", drop_query_param(minimal, name), bare_headers))
    return plan


async def _run_replays(
    targets: Sequence[Mapping[str, Any]],
    storage_state: Mapping[str, Any],
    *,
    user_agent: str,
    mutation_text: str,
    proxy_url: Optional[str],
    keep_params: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """对每个目标跑一遍 ``_replay_ladder``，每一档都是一次**真调**。

    用的是 ``safe_async_client`` 而不是裸 httpx —— 它是本仓库对外发请求的唯一
    入口（SSRF 校验 + 跨源重定向剥 Cookie）。``trust_env=False``：出口代理是
    **每账号**的（``account_environments.proxy_url``），走宿主机的
    HTTP(S)_PROXY 会让这次实调根本不在被勘探账号的出口上发生，那样得到的
    "能/不能"是关于我们服务器的，不是关于那个账号的。
    """
    from app.boundary import safe_async_client

    cookies = storage_state.get("cookies") if isinstance(storage_state, Mapping) else []
    results: list[dict[str, Any]] = []

    client_kwargs: dict[str, Any] = {
        "timeout": REPLAY_TIMEOUT_SECONDS,
        "trust_env": False,
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    budget = MAX_REPLAY_CALLS
    async with safe_async_client(**client_kwargs) as client:
        for target in list(targets)[:MAX_REPLAY_TARGETS]:
            url = str(target.get("url") or "")
            # ⚠️ 没有关键词参数**不再跳过**。面板一打开就加载的推荐/榜单列表
            # 天生不带关键词，而它正是"能不能在自己界面里列出来"要问的那一条；
            # 跳过它等于把最该问的目标排除在外。少的只是 ② 那一档。
            param = target.get("keyword_param") or None
            original = str(target.get("keyword_value") or "")
            if not url:
                continue
            host = (urlsplit(url).hostname or "").lower()
            jar = cookies_for_host(cookies if isinstance(cookies, list) else [], host)
            full_headers = {
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "referer": str(target.get("referer") or ""),
                "user-agent": user_agent
                or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            }
            if jar:
                full_headers["cookie"] = "; ".join(f"{n}={v}" for n, v in jar)
            bare_headers = {"accept": "*/*"}

            needles = {"original": original, "mutation": mutation_text}
            ladder = _replay_ladder(
                url,
                keyword_param=str(param) if param else None,
                mutation_text=mutation_text,
                keep_params=keep_params,
                full_headers=full_headers,
                bare_headers=bare_headers,
            )
            attempts: dict[str, Any] = {}
            for label, variant_url, variant_headers in ladder:
                if budget <= 0:
                    attempts[label] = {"ok": False, "error": "replay_budget_exhausted"}
                    continue
                budget -= 1
                attempts[label] = await _replay_once(
                    client, variant_url, headers=variant_headers, needles=needles
                )
            results.append(
                {
                    "host": host,
                    "path": urlsplit(url).path or "/",
                    "phase": str(target.get("phase") or ""),
                    "keyword_param": str(param) if param else None,
                    # 名字不是凭证，值才是。带名字是为了让"没带上会话 cookie
                    # 所以 403"与"带了还是 403"两种失败能被分开。
                    "cookie_names": sorted(name for name, _ in jar),
                    "cookies_sent": len(jar),
                    "proxied": bool(proxy_url),
                    "mutated_to": mutation_text,
                    "kept_params": [
                        name
                        for name in keep_params
                        if name
                        in {
                            n
                            for n, _ in parse_qsl(
                                urlsplit(url).query, keep_blank_values=True
                            )
                        }
                    ],
                    "attempts": attempts,
                    # 旧形状保留：调用方（和它的测试）按这两个键读结论已有先例，
                    # 阶梯只是在旁边多摆了几档，不该把已经在用的读法弄坏。
                    "verbatim": attempts.get("verbatim", {}),
                    "mutated": attempts.get("keyword", {}),
                }
            )
    return results


# ── 编排 ──────────────────────────────────────────────────────


async def probe_account_page(
    account_id: int,
    url: str,
    *,
    probe_text: str,
    target_selectors: Sequence[str],
    seed_files: Optional[Sequence[Mapping[str, Any]]] = None,
    observe_selectors: Optional[Sequence[str]] = None,
    capture_url_contains: Optional[Sequence[str]] = None,
    replay_mutation_text: str = "",
    replay_keep_params: Optional[Sequence[str]] = None,
    options: Optional[Mapping[str, Any]] = None,
    client: Any = None,
) -> dict[str, Any]:
    """用某个已绑账号的会话打开白名单页面、打一段字、回报它因此发出的请求。

    **从不抛**（除了调用方传了非法参数）：每条失败路径都是类型化结论，调用方
    按 ``detail.reason`` 分支给出可读回显。
    """
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
        SocialAccountsRepository,
    )
    from app.services.distribution.browser_client import BrowserClient, SessionStatus
    from app.services.distribution.session_adapter import (
        AUTH_TYPE_SESSION,
        SESSION_PLATFORM_PROFILES,
        SessionStateError,
        build_environment,
        decrypt_failure_result,
        parse_session_state,
    )
    from app.services.distribution.session_lock import account_session_lock

    accounts_repo = SocialAccountsRepository()
    acct: Optional[dict] = None
    replay: list[dict[str, Any]] = []
    try:
        acct = await accounts_repo.get_with_session(int(account_id))
        if not acct:
            return _envelope(
                SessionStatus.FAILED.value,
                "account not found",
                reason=REASON_ACCOUNT_MISSING,
            )

        platform = acct.get("platform") or ""
        if platform not in SESSION_PLATFORM_PROFILES:
            return _envelope(
                SessionStatus.FAILED.value,
                f"platform {platform!r} has no session profile",
                reason=REASON_PLATFORM_UNSUPPORTED,
                platform=platform,
            )
        if acct.get("auth_type") != AUTH_TYPE_SESSION:
            return _envelope(
                SessionStatus.FAILED.value,
                f"account auth_type is {acct.get('auth_type')!r}, not 'session'",
                reason=REASON_AUTH_TYPE_MISMATCH,
            )
        if acct.get(SESSION_STATE_DECRYPT_FAILED):
            failure = decrypt_failure_result(
                "session_state could not be decrypted", account_id=account_id
            )
            return {
                **failure,
                "observation": {},
                "replay": [],
                "session_refreshed": False,
            }

        try:
            storage_state = parse_session_state(acct)
        except SessionStateError as exc:
            return _envelope(
                SessionStatus.SESSION_INVALID.value, str(exc), reason=exc.reason
            )

        environment = build_environment(acct.get("environment"))
        browser = client or BrowserClient()

        async with account_session_lock(account_id, attempts=1) as acquired:
            if not acquired:
                return _envelope(
                    SessionStatus.FAILED.value,
                    "another browser session is already running for this account",
                    reason=REASON_ACCOUNT_BUSY,
                )
            result = await browser.probe_page(
                platform,
                storage_state,
                url,
                probe_text=probe_text,
                target_selectors=target_selectors,
                environment=environment,
                seed_files=seed_files,
                observe_selectors=observe_selectors,
                capture_url_contains=capture_url_contains,
                options=options,
            )

            # 重放留在锁里：它用的是同一份会话，一次真实发布同时在跑的时候
            # 平台可能已经轮换过 cookie，那样这次实调测的就不是我们以为的
            # 那个会话了。
            wants_replay = bool(replay_mutation_text or replay_keep_params)
            if wants_replay and getattr(result, "replay_targets", None):
                try:
                    replay = await _run_replays(
                        result.replay_targets,
                        storage_state,
                        user_agent=getattr(result, "replay_user_agent", "") or "",
                        mutation_text=replay_mutation_text,
                        proxy_url=environment.proxy_url if environment else None,
                        keep_params=replay_keep_params or (),
                    )
                except Exception as exc:  # noqa: BLE001
                    # 重放炸了不该吞掉已经拿到的抓包结果 —— 那是本次调用的
                    # 主要产物。把失败**说出来**，别静默 no-op。
                    replay = [{"ok": False, "error": type(exc).__name__}]
    finally:
        # 明文只活在这一帧（spec §7.6）。
        if acct is not None:
            acct.pop("session_state", None)

    refreshed = False
    if result.updated_storage_state:
        try:
            await accounts_repo.update_session_state(
                int(account_id),
                json.dumps(result.updated_storage_state, ensure_ascii=False),
            )
            refreshed = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[session.probe] account={account_id} "
                f"state write-back failed: {type(exc).__name__}"
            )

    logger.info(
        f"[session.probe] account={account_id} status={result.status} "
        f"replays={len(replay)} refreshed={refreshed}"
    )
    return {
        **result.result.to_dict(),
        # 摘要原样透传。**明文 storage_state 与 replay_targets 到此为止**：
        # 两者都不在这个 dict 里，也永远不会在。
        "observation": dict(result.observation),
        "replay": replay,
        "session_refreshed": refreshed,
    }


__all__ = [
    "MAX_REPLAY_CALLS",
    "MAX_REPLAY_TARGETS",
    "_replay_ladder",
    "drop_query_param",
    "keep_query_params",
    "REASON_ACCOUNT_BUSY",
    "REASON_ACCOUNT_MISSING",
    "REASON_AUTH_TYPE_MISMATCH",
    "REASON_PLATFORM_UNSUPPORTED",
    "cookies_for_host",
    "probe_account_page",
    "summarise_replay_body",
    "swap_query_value",
]
