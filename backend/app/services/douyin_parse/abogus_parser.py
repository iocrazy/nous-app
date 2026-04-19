# backend/app/services/douyin_parse/abogus_parser.py

"""
a_bogus 签名版抖音解析器

链路：share_url → aweme_id → 构造 /aweme/v1/web/aweme/detail/ 完整 URL
     → node env.js 计算 a_bogus → 拼到 URL 尾部 → httpx GET → aweme_detail

与另外两个 parser 的定位区别：
- `IesDouyinParser`     抓 iesdouyin.com 分享页 _ROUTER_DATA，碰到验证码/登录墙就挂
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
from typing import Any
from urllib.parse import urlencode

import httpx
from loguru import logger


DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)

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
_ID_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/share/slides/(\d+)"), "slides"),
    (re.compile(r"/share/video/(\d+)"), "video"),
    (re.compile(r"/video/(\d+)"), "video"),
    (re.compile(r"/note/(\d+)"), "note"),
)


class ABogusDouyinParser:
    """轻量级抖音解析器（HTTP + Node 子进程签名）。"""

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
    ) -> dict[str, Any] | None:
        """解析抖音分享链接 / aweme_id，返回 aweme_detail 字典。"""
        ua = user_agent or DEFAULT_UA
        try:
            aweme_id = await cls._resolve_aweme_id(share_url_or_aweme_id, ua)
            if not aweme_id:
                logger.warning(
                    f"[ABogus] cannot resolve aweme_id from {share_url_or_aweme_id}"
                )
                return None

            cookie, extra_headers = await cls._resolve_cookie(user_id, aweme_id)

            url = cls._build_detail_url(aweme_id)
            signed_url = await cls._append_a_bogus(url, ua)

            return await cls._fetch_detail(signed_url, ua, cookie, extra_headers)
        except Exception as err:
            logger.error(f"[ABogus] parse failed: {err}")
            return None

    # ───────────────── internals ─────────────────

    @classmethod
    async def _resolve_aweme_id(cls, src: str, ua: str) -> str | None:
        if src.isdigit():
            return src
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=cls.SHARE_REDIRECT_TIMEOUT
            ) as client:
                resp = await client.get(src, headers={"User-Agent": ua})
                final_url = str(resp.url)
                for pattern, _ in _ID_PATTERNS:
                    m = pattern.search(final_url)
                    if m:
                        return m.group(1)
                tail = final_url.split("?")[0].rstrip("/").split("/")[-1]
                return tail if tail.isdigit() else None
        except Exception as err:
            logger.error(f"[ABogus] resolve_aweme_id failed: {err}")
            return None

    @classmethod
    def _build_detail_url(cls, aweme_id: str) -> str:
        params = {**BASE_PARAMS, "aweme_id": aweme_id}
        return f"{DETAIL_API}?{urlencode(params)}"

    @classmethod
    async def _append_a_bogus(cls, url: str, ua: str) -> str:
        """调用 node env.js 计算 a_bogus 并拼到 URL 尾部。"""
        node_bin = shutil.which("node") or "node"

        def _run() -> str:
            completed = subprocess.run(
                [node_bin, str(_ENV_JS), url, ua],
                capture_output=True,
                text=True,
                timeout=cls.SIGN_TIMEOUT,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"a_bogus sign failed (rc={completed.returncode}): "
                    f"{completed.stderr.strip() or completed.stdout.strip()}"
                )
            bogus = completed.stdout.strip()
            if not bogus:
                raise RuntimeError("a_bogus empty")
            return bogus

        bogus = await asyncio.to_thread(_run)
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}a_bogus={bogus}"

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
    async def _cookie_from_user_config(
        cls, user_id: str
    ) -> tuple[str, dict[str, str]]:
        extras: dict[str, str] = {}
        cookie = ""
        try:
            from app.repositories.cookies_repository import CookiesRepository

            row = await CookiesRepository().get_by_user_and_platform(user_id, "douyin")
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

        async with httpx.AsyncClient(timeout=cls.DETAIL_TIMEOUT) as client:
            resp = await client.get(signed_url, headers=headers)

            if not resp.text:
                logger.warning(
                    f"[ABogus] empty body status={resp.status_code} — likely blocked"
                )
                return None

            try:
                data = resp.json()
            except json.JSONDecodeError:
                logger.warning(f"[ABogus] non-JSON body: {resp.text[:500]}")
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
    share_url: str, *, user_id: str | None = None
) -> dict[str, Any] | None:
    """便捷函数，与 `lightweight_parse` 保持一致。"""
    return await ABogusDouyinParser.parse(share_url, user_id=user_id)
