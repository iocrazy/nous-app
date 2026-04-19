# backend/app/services/douyin_parse/ies_parser.py

"""
轻量级抖音解析服务

使用 HTTP 请求解析抖音分享页面，无需浏览器自动化。
作为主要解析方案，失败时回退到浏览器自动化方案。

参考：toolkit/douyin-creator-toolkit 的实现
"""

import json
import re
from typing import Any, Dict, Optional

import httpx
from loguru import logger


class IesDouyinParser:
    """轻量级抖音解析器，通过 HTTP 请求解析分享页面"""

    # 非 UA 的固定请求头。UA 由 parse() 的调用方注入，统一来自
    # `ua_pool.pick_ua()`，保证同一次任务所有 Douyin 请求共用同一个 UA。
    BASE_HEADERS: dict[str, str] = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }

    # 请求超时设置
    TIMEOUT = 15.0

    @classmethod
    async def _get_user_overrides(
        cls, user_id: Optional[str]
    ) -> Dict[str, str]:
        """Fetch user's Douyin cookie and custom headers from user_cookies table.

        Returns a dict of extra headers to merge (may include Cookie header).
        """
        if not user_id:
            return {}
        extra: Dict[str, str] = {}
        try:
            from app.repositories.cookies_repository import CookiesRepository
            repo = CookiesRepository()
            row = await repo.get_by_user_and_platform(user_id, "douyin")
            if not row:
                return {}

            # Cookie
            cookie_val = row.get("cookie_text") or row.get("cookie_file")
            if cookie_val:
                extra["Cookie"] = cookie_val

            # Custom headers (Key: Value\n format)
            custom_headers_text = row.get("custom_headers") or ""
            for line in custom_headers_text.strip().splitlines():
                line = line.strip()
                if ":" in line:
                    key, _, value = line.partition(":")
                    key, value = key.strip(), value.strip()
                    if key and value:
                        extra[key] = value

        except Exception as e:
            logger.debug(f"[IesDouyinParser] Failed to load user overrides: {e}")
        if extra:
            logger.info(f"[IesDouyinParser] Loaded user overrides: {list(extra.keys())}")
        return extra

    @classmethod
    async def parse(
        cls,
        share_url: str,
        *,
        user_id: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        解析抖音分享链接，返回 aweme_detail 格式的数据

        Args:
            share_url: 抖音分享链接 (如 https://v.douyin.com/xxx)
            user_id: 可选，用于获取用户配置的 Cookie 提高解析成功率
            user_agent: 统一的 Douyin UA（由 ua_pool.pick_ua() 生成）。
                调用方必须传入，以保证与后续 ABogus 签名、yt-dlp 下载共用。
                未传入时从 ua_pool 取一条兜底。

        Returns:
            Dict: aweme_detail 格式的数据，与浏览器方案返回格式一致
            None: 解析失败
        """
        if not user_agent:
            from app.services.douyin_parse.ua_pool import pick_ua
            user_agent = pick_ua()

        try:
            logger.info(f"[IesDouyinParser] 开始解析: {share_url}")

            # Load user cookie + custom headers if available
            extra_headers = await cls._get_user_overrides(user_id)

            # 1. 跟随重定向获取视频 ID 和内容类型
            result = await cls._get_video_id(share_url, user_agent, extra_headers)
            if not result:
                logger.warning("[IesDouyinParser] 无法获取视频 ID")
                return None

            video_id, content_type = result
            logger.info(f"[IesDouyinParser] 获取到视频 ID: {video_id}, type: {content_type}")

            # 2. 访问分享页面获取数据
            aweme_detail = await cls._fetch_share_page(
                video_id, content_type, user_agent, extra_headers
            )
            if not aweme_detail:
                logger.warning("[IesDouyinParser] 无法从分享页面获取数据")
                return None

            logger.success(
                f"[IesDouyinParser] 解析成功: aweme_id={aweme_detail.get('aweme_id')}"
            )
            return aweme_detail

        except Exception as e:
            logger.error(f"[IesDouyinParser] 解析失败: {e}")
            return None

    @classmethod
    async def _get_video_id(
        cls,
        share_url: str,
        user_agent: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Optional[tuple[str, str]]:
        """
        从分享链接获取视频 ID 和内容类型。

        Returns:
            (video_id, content_type) where content_type is "video", "note", or "slides".
            None on failure.
        """
        try:
            headers = {
                **cls.BASE_HEADERS,
                "User-Agent": user_agent,
                **(extra_headers or {}),
            }
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=cls.TIMEOUT
            ) as client:
                response = await client.get(share_url, headers=headers)
                final_url = str(response.url)

                logger.info(f"[IesDouyinParser] 重定向后 URL: {final_url}")

                # 提取内容类型和数字 ID
                patterns = [
                    (r"/share/slides/(\d+)", "slides"),
                    (r"/share/video/(\d+)", "video"),
                    (r"/video/(\d+)", "video"),
                    (r"/note/(\d+)", "note"),
                ]

                for pattern, content_type in patterns:
                    match = re.search(pattern, final_url)
                    if match:
                        return match.group(1), content_type

                # 尝试从 URL 末尾提取
                video_id = final_url.split("?")[0].strip("/").split("/")[-1]
                if video_id.isdigit():
                    return video_id, "video"

                logger.warning(f"[IesDouyinParser] URL 模式不匹配, final_url={final_url}")
                return None

        except Exception as e:
            logger.error(f"[IesDouyinParser] 获取视频 ID 失败: {e}")
            return None

    @classmethod
    async def _fetch_share_page(
        cls,
        video_id: str,
        content_type: str = "video",
        user_agent: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        访问抖音分享页面，提取 _ROUTER_DATA 中的视频数据。
        content_type: "video", "note", or "slides"

        user_agent 为空时从 ua_pool 兜底——所有 Douyin 请求都必须带 UA。
        """
        if not user_agent:
            from app.services.douyin_parse.ua_pool import pick_ua
            user_agent = pick_ua()

        path_segment = "slides" if content_type == "slides" else "video"
        share_page_url = f"https://www.iesdouyin.com/share/{path_segment}/{video_id}"

        try:
            headers = {
                **cls.BASE_HEADERS,
                "User-Agent": user_agent,
                **(extra_headers or {}),
            }
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                response = await client.get(share_page_url, headers=headers)
                response.raise_for_status()

                html_content = response.text
                page_len = len(html_content)

                # ── Failure analysis: classify page and log on any failure ──
                # Reference: Notion doc "抖音验证码检测脚本"
                has_router_data = "_ROUTER_DATA" in html_content
                captcha_keywords = ["captcha_container", "verifycenter", "请完成下列验证后继续"]
                has_captcha = any(kw in html_content for kw in captcha_keywords)
                has_slardar = "slardar" in html_content
                has_login_wall = "登录后免费畅享高清视频" in html_content

                def _classify_failure() -> str:
                    """Classify why the page failed to return video data."""
                    if has_captcha:
                        return "CAPTCHA"
                    if has_login_wall and not has_router_data:
                        return "LOGIN_WALL"
                    if has_slardar and not has_router_data:
                        return "BLOCKED_BY_SLARDAR"
                    if not has_router_data and page_len < 1000:
                        return "EMPTY_PAGE"
                    if not has_router_data:
                        return "NO_ROUTER_DATA"
                    return ""

                # 解析 window._ROUTER_DATA
                pattern = re.compile(
                    r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", flags=re.DOTALL
                )
                match = pattern.search(html_content)

                if match:
                    logger.info(
                        f"[IesDouyinParser] ✅ _ROUTER_DATA found | "
                        f"status={response.status_code} | size={page_len} | "
                        f"captcha={has_captcha}"
                    )
                else:
                    reason = _classify_failure()
                    snippet = html_content[:2000].replace("\n", " ")
                    logger.warning(
                        f"[IesDouyinParser] ⚠️ Parse failed | reason={reason} | "
                        f"status={response.status_code} | size={page_len} | "
                        f"url={share_page_url}"
                    )
                    logger.info(
                        f"[IesDouyinParser] Page HTML ({reason}, first 2000 chars): {snippet}"
                    )
                    return None

                json_str = match.group(1).strip()
                router_data = json.loads(json_str)

                # 提取视频数据
                ROUTE_KEYS = [
                    "video_(id)/page",
                    "note_(id)/page",
                    "slides_(id)/page",
                ]

                loader_data = router_data.get("loaderData", {})

                item_list = None
                for key in ROUTE_KEYS:
                    if key in loader_data:
                        item_list = (
                            loader_data[key]
                            .get("videoInfoRes", {})
                            .get("item_list", [])
                        )
                        break

                if item_list is None:
                    logger.warning(
                        f"[IesDouyinParser] ⚠️ Parse failed | reason=UNKNOWN_ROUTE_KEY | "
                        f"keys={list(loader_data.keys())}"
                    )
                    return None

                if not item_list:
                    logger.warning(
                        f"[IesDouyinParser] ⚠️ Parse failed | reason=EMPTY_ITEM_LIST"
                    )
                    return None

                # 返回第一个视频数据（与浏览器方案格式一致）
                aweme_detail = item_list[0]

                # 处理视频下载 URL：去水印
                cls._process_video_urls(aweme_detail)

                return aweme_detail

        except httpx.HTTPStatusError as e:
            logger.error(f"[IesDouyinParser] HTTP 错误: {e.response.status_code}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"[IesDouyinParser] JSON 解析错误: {e}")
            return None
        except Exception as e:
            logger.error(f"[IesDouyinParser] 获取分享页面失败: {e}")
            return None

    @classmethod
    def _process_video_urls(cls, aweme_detail: Dict[str, Any]) -> None:
        """
        处理视频 URL，去除水印

        抖音视频 URL 中的 playwm 表示带水印版本，
        替换为 play 即可获得无水印版本
        """
        try:
            video_data = aweme_detail.get("video", {})
            play_addr = video_data.get("play_addr", {})
            url_list = play_addr.get("url_list", [])

            # 替换所有 URL 中的 playwm 为 play
            processed_urls = []
            for url in url_list:
                if isinstance(url, str):
                    processed_url = url.replace("playwm", "play")
                    processed_urls.append(processed_url)

            if processed_urls:
                play_addr["url_list"] = processed_urls

            # 同样处理 bit_rate 中的 URL
            bit_rate_list = video_data.get("bit_rate", [])
            for bit_rate in bit_rate_list:
                if isinstance(bit_rate, dict):
                    br_play_addr = bit_rate.get("play_addr", {})
                    br_url_list = br_play_addr.get("url_list", [])
                    br_processed = [
                        url.replace("playwm", "play") if isinstance(url, str) else url
                        for url in br_url_list
                    ]
                    if br_processed:
                        br_play_addr["url_list"] = br_processed

        except Exception as e:
            logger.warning(f"[IesDouyinParser] 处理视频 URL 时出错: {e}")


# 便捷函数
async def lightweight_parse(
    share_url: str, *, user_agent: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    轻量级解析抖音分享链接

    Args:
        share_url: 抖音分享链接
        user_agent: 可选，与 IesDouyinParser.parse 保持一致（未传则从 ua_pool 取）

    Returns:
        aweme_detail 数据或 None
    """
    return await IesDouyinParser.parse(share_url, user_agent=user_agent)
