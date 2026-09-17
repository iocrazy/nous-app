# backend/app/services/douyin_parse/abogus_parser.py

"""
a_bogus 签名版抖音解析器

链路：share_url → aweme_id → 构造 /aweme/v1/web/aweme/detail/ 完整 URL
     → 计算 a_bogus → 拼到 URL 尾部 → webSignUrl 补 Argus 字段 → httpx GET
     → aweme_detail

与另一个 parser 的定位区别：
- `DrissionPageParser`  起 headless Chrome 拦截 API，最稳但最重（Docker 要装 Chrome）
- `ABogusDouyinParser`  用 HTTP + Node 子进程签名直达 API，轻量，依赖有效 cookie

Cookie 优先级：Redis 缓存（DrissionPageParser 会写入） > user_cookies 表 > 匿名
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.boundary import safe_async_client
from app.services.media.parsers.douyin_parse.failures import (
    DouyinFailure,
    DouyinParseError,
)

SignEngine = Literal["python", "node"]


# NOTE: the UA is NOT hardcoded here — it comes from ua_pool.pick_ua()
# at the task boundary and is threaded through parse() so the ABogus
# signature, the API request header, and yt-dlp's download header all
# agree. See app/services/douyin_parse/ua_pool.py.

DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

# 固定 web 端指纹参数。除 aweme_id / msToken / webid / ... 外其余按浏览器常见值写死。
# 这些参数要参与 a_bogus 计算，改动后签名会失效 —— 与 env.js 的 paths 同步。
BASE_PARAMS: dict[str, str] = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "pc_libra_divert": "Mac",
    "update_version_code": "170400",
    "version_code": "190500",
    "version_name": "19.5.0",
    "cookie_enabled": "true",
    "platform": "PC",
    "support_h265": "1",
    "support_dash": "1",
    "screen_width": "2560",
    "screen_height": "1440",
    "browser_language": "en-US",
    "browser_platform": "MacIntel",
    "browser_name": "Chrome",
    "browser_version": "147.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "147.0.0.0",
    "os_name": "Mac OS",
    "os_version": "10.15.7",
    "cpu_core_num": "12",
    "device_memory": "32",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "0",
    "request_source": "600",
    "origin_type": "video_page",
}

_ENV_JS = Path(__file__).with_name("env.js")
_WEBSIGN_ENV_JS = Path(__file__).with_name("websign_env.js")
_ID_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/share/slides/(\d+)"), "slides"),
    (re.compile(r"/share/video/(\d+)"), "video"),
    (re.compile(r"/video/(\d+)"), "video"),
    (re.compile(r"/note/(\d+)"), "note"),
)


def _cookie_value(cookie_header: str, name: str) -> str:
    """One cookie's value out of a `k=v; k=v` header, or "" when absent."""
    m = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]*)", cookie_header or "")
    return m.group(1) if m else ""


def _redact(text: str, *secrets: str) -> str:
    """Strip live credentials out of text that is about to be logged.

    The webSign signer receives the cookie through its environment, so ANY
    diagnostic it prints — a stack trace, a dump of `process.env` — can carry
    the whole session. That text becomes our exception, and from there the
    log line and the task row a user can open.

    Short values are left alone: redacting a two-character cookie would blank
    unrelated substrings without protecting anything worth protecting.
    """
    for secret in sorted(secrets, key=len, reverse=True):
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    return text


class ABogusDouyinParser:
    """轻量级抖音解析器（HTTP + a_bogus 签名直达 /aweme/v1/web/aweme/detail/）。

    Two signing engines are supported:
      - "python" (default): pure-Python ABogus from vendored f2 module.
        ~5ms per call, no subprocess, community-maintained algorithm.
      - "node": legacy Node subprocess over `env.js` + `douyin_bdms.js`.
        Kept as a fallback for A/B comparison; requires `node` binary.

    Either way the a_bogus-ed URL then goes through douyin's own `webSignUrl`
    VM (`websign_env.js`, Node >= 22) for the Argus fields the detail endpoint
    demands since 2026-09.
    """

    DEFAULT_ENGINE: SignEngine = "python"

    SHARE_REDIRECT_TIMEOUT = 10.0
    DETAIL_TIMEOUT = 15.0
    SIGN_TIMEOUT = 10.0

    @classmethod
    async def parse(
        cls,
        share_url_or_aweme_id: str,
        *,
        user_id: str | None = None,
        user_agent: str | None = None,
        engine: SignEngine | None = None,
    ) -> dict[str, Any] | None:
        """解析抖音分享链接 / aweme_id，返回 aweme_detail 字典。"""
        if not user_agent:
            from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

            user_agent = pick_ua()
        ua = user_agent
        eng: SignEngine = engine or cls.DEFAULT_ENGINE
        try:
            aweme_id = await cls._resolve_aweme_id(share_url_or_aweme_id, ua)
            if not aweme_id:
                logger.warning(
                    f"[ABogus] cannot resolve aweme_id from {share_url_or_aweme_id}"
                )
                return None

            cookie, extra_headers = await cls._resolve_cookie(user_id, aweme_id)
            fp = cls._extract_verify_fp(cookie)

            url = cls._build_detail_url(aweme_id)
            signed_url = await cls._sign(url, ua, eng, fp, cookie)

            return await cls._fetch_detail(signed_url, ua, cookie, extra_headers)
        except DouyinParseError:
            # A typed failure is the ONE thing this handler must not flatten:
            # turning it back into None here would undo the whole point of
            # raising it (CLAUDE.md "catch 静默吞错"). The chain above decides
            # what to do with it.
            raise
        except Exception as err:
            logger.error(f"[ABogus] parse failed (engine={eng}): {err}")
            return None

    # ───────────────── internals ─────────────────

    @classmethod
    def _match_aweme_id(cls, url: str) -> str | None:
        """Extract the aweme_id from a URL via _ID_PATTERNS, else None."""
        for pattern, _ in _ID_PATTERNS:
            m = pattern.search(url)
            if m:
                return m.group(1)
        return None

    @classmethod
    async def _resolve_aweme_id(cls, src: str, ua: str) -> str | None:
        """Resolve a share URL / bare id to the aweme_id.

        Hop-by-hop with short-circuit: douyin's short-link chain is
        v.douyin.com 302 → iesdouyin.com/share/video/{id} 302 →
        www.douyin.com/video/{id} (a full HTML page), and each hop can
        take seconds under douyin-side throttling. The FIRST Location
        already carries the id, so we read redirects manually
        (follow_redirects=False — initial URL still SSRF-validated) and
        stop as soon as a Location matches, instead of fetching every
        hop (8-22s observed → ~1 request)."""
        if src.isdigit():
            return src
        matched = cls._match_aweme_id(src)
        if matched:
            return matched
        try:
            async with safe_async_client(timeout=cls.SHARE_REDIRECT_TIMEOUT) as client:
                url = src
                for _hop in range(5):
                    resp = await client.get(
                        url, headers={"User-Agent": ua}, follow_redirects=False
                    )
                    location = resp.headers.get("Location")
                    if not resp.is_redirect or not location:
                        url = str(resp.url)
                        break
                    url = str(httpx.URL(url).join(location))
                    matched = cls._match_aweme_id(url)
                    if matched:
                        return matched
                matched = cls._match_aweme_id(url)
                if matched:
                    return matched
                tail = url.split("?")[0].rstrip("/").split("/")[-1]
                return tail if tail.isdigit() else None
        except Exception as err:
            logger.error(f"[ABogus] resolve_aweme_id failed: {err}")
            return None

    @classmethod
    def _build_detail_url(cls, aweme_id: str) -> str:
        params = {**BASE_PARAMS, "aweme_id": aweme_id}
        return f"{DETAIL_API}?{urlencode(params)}"

    @classmethod
    async def _sign(
        cls, url: str, ua: str, engine: SignEngine, fp: str, cookie: str
    ) -> str:
        """Append `a_bogus=`, then run the whole URL through webSignUrl.

        The order is fixed: webSignUrl signs the full query string, so it has
        to see `a_bogus` already in place.
        """
        if engine == "python":
            bogus = await asyncio.to_thread(cls._sign_with_python, url, ua, fp)
        elif engine == "node":
            bogus = await asyncio.to_thread(cls._sign_with_node, url, ua)
        else:
            raise ValueError(f"unknown sign engine: {engine!r}")

        sep = "&" if "?" in url else "?"
        a_bogus_url = f"{url}{sep}a_bogus={bogus}"
        return await asyncio.to_thread(cls._sign_with_websign, a_bogus_url, ua, cookie)

    @staticmethod
    def _sign_with_python(url: str, ua: str, fp: str) -> str:
        """Pure-Python sign via vendored f2 ABogus (GET options = [0,1,8])."""
        from app.services.media.parsers.douyin_parse._f2_abogus import ABogus

        query = url.split("?", 1)[1] if "?" in url else ""
        ab = ABogus(user_agent=ua, fp=fp or "", options=[0, 1, 8])
        _, bogus, _, _ = ab.generate_abogus(query)
        if not bogus:
            raise RuntimeError("python a_bogus empty")
        return bogus

    @classmethod
    def _sign_with_node(cls, url: str, ua: str) -> str:
        """Legacy: spawn node env.js to compute a_bogus via douyin_bdms.js."""
        node_bin = shutil.which("node") or "node"
        completed = subprocess.run(
            [node_bin, str(_ENV_JS), url, ua],
            capture_output=True,
            text=True,
            timeout=cls.SIGN_TIMEOUT,
            check=False,
            # env rides safe_popen_kwargs (scrubbed of our secrets); the
            # child-specific UA goes through env_extra. Passing env= here as
            # well is a duplicate kwarg — TypeError at spawn.
            **safe_popen_kwargs(env_extra={"DOUYIN_UA": ua}),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"node a_bogus sign failed (rc={completed.returncode}): "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        bogus = completed.stdout.strip()
        if not bogus:
            raise RuntimeError("node a_bogus empty")
        return bogus

    @classmethod
    def _sign_with_websign(cls, url: str, ua: str, cookie: str) -> str:
        """Add Argus `timestamp` / `uifid` / `x-secsdk-web-signature` to `url`.

        Runs douyin's bundled `webSignUrl` VM (websign_runtime.js) in a Node
        subprocess. Cookie, UIFID and UA go in through the environment so they
        match what the final GET sends — a mismatch between the signed and the
        sent fingerprint is itself a rejection.
        """
        uifid = _cookie_value(cookie, "UIFID")
        if not uifid:
            # Argus rejects the request without it, and nothing in this tier
            # can mint one: it is issued to a real browser session. The user's
            # next move is refreshing the saved cookie, which is exactly what
            # AUTH_REQUIRED tells them. Fail before spawning Node.
            raise DouyinParseError(
                DouyinFailure.AUTH_REQUIRED,
                "Douyin cookie has no UIFID; Argus web signing needs it",
            )

        node_bin = shutil.which("node") or "node"
        completed = subprocess.run(
            [
                node_bin,
                # Confines douyin's third-party VM bytecode to reading its own
                # directory. Node 20/21 spell this --experimental-permission;
                # the image pins Node 22 for it (see Dockerfile). Do not drop
                # the flag to accommodate an older runtime.
                "--permission",
                f"--allow-fs-read={_WEBSIGN_ENV_JS.parent}",
                str(_WEBSIGN_ENV_JS),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=cls.SIGN_TIMEOUT,
            check=False,
            **safe_popen_kwargs(
                env_extra={
                    "DOUYIN_COOKIE": cookie,
                    "DOUYIN_UIFID": uifid,
                    "DOUYIN_UA": ua,
                }
            ),
        )
        if completed.returncode != 0:
            detail = _redact(
                completed.stderr.strip() or completed.stdout.strip(),
                cookie,
                uifid,
                *(part.partition("=")[2].strip() for part in cookie.split(";")),
            )
            if "bad option: --permission" in detail:
                # Name the real cause, otherwise the operator goes looking at
                # douyin. Measured 2026-09-16: node 20.19.2 / 21.7.3 reject the
                # flag, 22.23.2 accepts it.
                raise RuntimeError(
                    "node webSign sign failed: this Node.js does not support "
                    "--permission (needs Node >= 22; 20 and 21 call it "
                    "--experimental-permission). Upgrade the runtime; do not "
                    "drop the flag, it sandboxes third-party VM bytecode."
                )
            raise RuntimeError(
                f"node webSign sign failed (rc={completed.returncode}): {detail}"
            )

        signed_url = completed.stdout.strip()
        if not parse_qs(urlsplit(signed_url).query).get("x-secsdk-web-signature"):
            # Not echoing the URL: it carries uifid as a query parameter.
            raise RuntimeError("node webSign returned no x-secsdk-web-signature")
        return signed_url

    @staticmethod
    def _extract_verify_fp(cookie: str) -> str:
        """Pull `s_v_web_id` from a cookie string — used as verifyFp / fp.

        Absent or malformed cookies → "" (f2 ABogus will generate a random
        Edge fingerprint instead).
        """
        if not cookie:
            return ""
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "s_v_web_id" and v:
                return v
        return ""

    @classmethod
    async def _resolve_cookie(
        cls, user_id: str | None, aweme_id: str
    ) -> tuple[str, dict[str, str]]:
        """返回 (cookie_header, extra_headers)。"""
        # 1) Redis 中 DrissionPageParser 留下的会话 cookie
        redis_cookie = await cls._cookie_from_redis(aweme_id)
        if redis_cookie:
            logger.debug(f"[ABogus] using Redis-cached cookies for {aweme_id}")
            return redis_cookie, {}

        # 2) user_cookies 表里用户配置的 cookie / 自定义 headers
        if user_id:
            return await cls._cookie_from_user_config(user_id)

        return "", {}

    @classmethod
    async def _cookie_from_redis(cls, aweme_id: str) -> str:
        try:
            from app.core.redis import get_sync_redis

            def _read() -> str:
                value = get_sync_redis().get(f"douyin_browser_cookies:{aweme_id}")
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")
                return value or ""

            return await asyncio.to_thread(_read)
        except Exception as err:
            logger.debug(f"[ABogus] redis cookie read failed: {err}")
            return ""

    @classmethod
    async def _cookie_from_user_config(cls, user_id: str) -> tuple[str, dict[str, str]]:
        extras: dict[str, str] = {}
        cookie = ""
        try:
            from app.repositories.cookies_repository import (
                get_cookies_repository,
            )

            row = await get_cookies_repository().get_by_user_and_platform(
                user_id, "douyin"
            )
            if not row:
                return "", {}

            cookie = (row.get("cookie_text") or row.get("cookie_file") or "").strip()

            for line in (row.get("custom_headers") or "").splitlines():
                line = line.strip()
                if ":" in line:
                    k, _, v = line.partition(":")
                    k, v = k.strip(), v.strip()
                    if k and v and k.lower() != "cookie":
                        extras[k] = v
        except Exception as err:
            logger.debug(f"[ABogus] user cookie lookup failed: {err}")
        return cookie, extras

    @classmethod
    async def _fetch_detail(
        cls,
        signed_url: str,
        ua: str,
        cookie: str,
        extra_headers: dict[str, str],
    ) -> dict[str, Any] | None:
        headers: dict[str, str] = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.douyin.com/",
            **extra_headers,
        }
        if cookie:
            headers["Cookie"] = cookie
        # Douyin's Argus plugin reads the device fingerprint from a `uifid`
        # REQUEST HEADER, not from the cookie of the same name — even though
        # the browser sends both and we already hold the value.
        #
        # Measured against production on 2026-09-15, same URL, same cookie:
        #   without the header → 403 "ArgusSecurityPlugin Uifid Not Found"
        #   with the header    → 403 "ArgusSecurityPlugin Signature Not Found"
        # The error MOVED, which is what proves the header is read. The second
        # gate is the webSignUrl signature, which `_sign_with_websign` binds to
        # this same UIFID — the header and the signed query must agree.
        uifid = _cookie_value(cookie, "UIFID")
        if uifid:
            headers.setdefault("uifid", uifid)

        async with safe_async_client(timeout=cls.DETAIL_TIMEOUT) as client:
            resp = await client.get(signed_url, headers=headers)

            if not resp.text:
                logger.warning(
                    f"[ABogus] empty body status={resp.status_code} — likely blocked"
                )
                return None

            try:
                data = resp.json()
            except json.JSONDecodeError:
                body = resp.text[:500]
                logger.warning(f"[ABogus] non-JSON body: {body}")
                # Douyin's anti-bot plugin answers in plain text when it
                # rejects the request outright. Distinguishing this from a
                # generic miss matters: a rejected signature fails identically
                # on every retry, so telling the user to "try again" would be
                # a lie (2026-09-15 — `ArgusSecurityPlugin Uifid Not Found`).
                if "ArgusSecurityPlugin" in body or "Uifid" in body:
                    raise DouyinParseError(
                        DouyinFailure.SIGNATURE_REJECTED, body.strip()[:200]
                    )
                return None

            aweme_detail = data.get("aweme_detail")
            if not aweme_detail:
                logger.warning(
                    "[ABogus] no aweme_detail | "
                    f"status_code={data.get('status_code')} "
                    f"status_msg={data.get('status_msg')}"
                )
                return None
            return aweme_detail


async def abogus_parse(
    share_url: str,
    *,
    user_id: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any] | None:
    """便捷函数，与 `lightweight_parse` 保持一致。"""
    return await ABogusDouyinParser.parse(
        share_url, user_id=user_id, user_agent=user_agent
    )
