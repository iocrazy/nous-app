# backend/app/services/ytdlp_service.py

"""
yt-dlp Service

Universal video metadata fetching and downloading via yt-dlp.
Supports YouTube, Bilibili, Twitter/X, TikTok, Instagram, Xiaohongshu, and more.
"""

import asyncio
import concurrent.futures
import json
import os
import tempfile
from datetime import datetime
from typing import Callable, Optional

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.boundary import ValidatedURL, safe_async_client
from app.core.utils import Utils
from app.services.media.parsers.url_router import URLRouter

# ---------------------------------------------------------------------------
# Cookie format helpers
# ---------------------------------------------------------------------------

# Platform → default cookie domain mapping
_PLATFORM_COOKIE_DOMAINS: dict[str, str] = {
    "douyin": ".douyin.com",
    "bilibili": ".bilibili.com",
    "youtube": ".youtube.com",
    "tiktok": ".tiktok.com",
    "twitter": ".twitter.com",
    "x": ".x.com",
    "instagram": ".instagram.com",
    "xiaohongshu": ".xiaohongshu.com",
    "weibo": ".weibo.com",
}

_NETSCAPE_HEADER = "# Netscape HTTP Cookie File\n# This file was auto-converted from browser cookie string\n\n"


def _is_netscape_format(text: str) -> bool:
    """Return True if *text* already looks like a Netscape cookie file."""
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # A valid Netscape line has 7 tab-separated fields
        if len(line.split("\t")) == 7:
            return True
        return False
    return False


def _browser_cookie_to_netscape(cookie_str: str, platform: str) -> str:
    """Convert a browser-style cookie string to Netscape format.

    Browser cookie strings look like: ``key1=value1; key2=value2; ...``
    Netscape format is one tab-separated line per cookie:
        domain  include_subdomains  path  secure  expiry  name  value

    If the input is already in Netscape format it is returned as-is.
    """
    if _is_netscape_format(cookie_str):
        return cookie_str

    domain = _PLATFORM_COOKIE_DOMAINS.get(platform, f".{platform}.com")
    lines = [_NETSCAPE_HEADER]

    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        # domain  flag  path  secure  expiry  name  value
        lines.append(f"{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}")

    return "\n".join(lines) + "\n"


class YtdlpService:
    """Universal video download via yt-dlp"""

    @staticmethod
    async def fetch_metadata(
        url: ValidatedURL,
        user_id: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        """
        Fetch video metadata using yt-dlp --dump-json.

        Args:
            url: Video URL (must have passed app.boundary.url_guard.validate_url)
            user_id: Optional user ID for per-user cookie lookup
            user_agent: Optional explicit UA (used for Douyin so the same UA
                threads through LightHTTP/ABogus/yt-dlp/download_video).

        Returns:
            dict: yt-dlp info_dict with video metadata

        Raises:
            RuntimeError: If yt-dlp fails or returns no data
            AssertionError: If url is not a ValidatedURL (caller bug)
        """
        # Boundary: runtime guard. Caller must validate first (mypy not in CI).
        assert isinstance(url, ValidatedURL), (
            "YtdlpService.fetch_metadata requires ValidatedURL — "
            "call app.boundary.validate_url[_async] first"
        )
        logger.info(f"[yt-dlp] Fetching metadata: {url}")

        ua_args = ["--user-agent", user_agent] if user_agent else []
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "--no-warnings",
            "--no-playlist",
            # 2026-05-13: bumped from 15s/2 → 30s/3 after 4 bilibili tasks
            # silently failed (cover-only). bilibili was hitting the yt-dlp
            # internal 20s requests-read-timeout when its CDN was slow,
            # blowing past our 15s subprocess timeout before retries fired.
            "--socket-timeout",
            "30",
            "--retries",
            "3",
            *ua_args,
            *YtdlpService._get_proxy_args(url),
            *YtdlpService._get_cookie_args(url, user_id=user_id),
            url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            # Keep timeout < Celery's soft_time_limit (120s) so we surface the
            # real stderr instead of getting killed with SoftTimeLimitExceeded.
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                # Try to read whatever stderr yt-dlp has written so far
                _out, _err = await asyncio.wait_for(proc.communicate(), timeout=2)
                stderr_snippet = (
                    _err.decode("utf-8", errors="replace")[:500] if _err else ""
                )
            except Exception:
                stderr_snippet = ""
            logger.error(
                f"[yt-dlp] Metadata fetch timed out after 90s: {url}\n--- stderr ---\n{stderr_snippet}"
            )
            raise RuntimeError(
                f"yt-dlp timed out (90s). Likely network unreachable. stderr: {stderr_snippet[:200]}"
            )

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            parsed_error = YtdlpService._parse_error(error_msg)
            logger.error(f"[yt-dlp] Metadata fetch failed: {parsed_error}")
            raise RuntimeError(f"yt-dlp failed: {parsed_error}")

        try:
            info = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            logger.error(f"[yt-dlp] Failed to parse JSON output: {e}")
            raise RuntimeError(f"yt-dlp returned invalid JSON: {e}")

        logger.success(f"[yt-dlp] Metadata fetched: {info.get('title', 'unknown')}")
        return info

    @staticmethod
    async def download_video(
        url: ValidatedURL,
        output_dir: str,
        platform_id: str,
        progress_callback: Optional[Callable] = None,
        user_id: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        """
        Download video file via yt-dlp with real-time progress tracking.

        Args:
            url: Video URL (must have passed validate_url[_async])
            output_dir: Directory to save the file
            platform_id: Used for filename
            progress_callback: Optional callback(downloaded, total, speed) for progress updates
            user_id: Optional user ID for per-user cookie lookup
            user_agent: Optional explicit UA (reuses the same UA that was
                used for the parse phase so the Douyin CDN sees a
                consistent client across metadata + download).

        Returns:
            dict: {file_path, file_size}

        Raises:
            AssertionError: If url is not a ValidatedURL (caller bug)
        """
        # Boundary: runtime guard. Caller must validate first (mypy not in CI).
        assert isinstance(url, ValidatedURL), (
            "YtdlpService.download_video requires ValidatedURL — "
            "call app.boundary.validate_url[_async] first"
        )
        os.makedirs(output_dir, exist_ok=True)

        output_template = os.path.join(output_dir, "video.%(ext)s")

        ua_args = ["--user-agent", user_agent] if user_agent else []
        cmd = [
            "yt-dlp",
            "-f",
            "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "--merge-output-format",
            "mp4",
            "--no-playlist",
            "--no-warnings",
            "--newline",
            # 2026-05-13: without these, yt-dlp uses the internal
            # requests 20s read-timeout and no retries, so a slow
            # bilibili CDN response sinks the whole download (the
            # exact failure that hit 4 tasks on 2026-05-13).
            "--socket-timeout",
            "60",
            "--retries",
            "3",
            "--progress-template",
            "download:%(progress._percent_str)s %(progress._downloaded_bytes)s %(progress._total_bytes_estimate)s %(progress._speed_str)s",
            *ua_args,
            *YtdlpService._get_proxy_args(url),
            *YtdlpService._get_cookie_args(url, user_id=user_id),
            "-o",
            output_template,
            url,
        ]

        logger.info(f"[yt-dlp] Downloading video to {output_dir}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )

            stderr_lines = []

            async def read_stdout():
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded.startswith("download:") and progress_callback:
                        # Format: "download:<percent_str> <downloaded_bytes> <total_bytes> <speed_str>"
                        parts = decoded[len("download:") :].split()
                        if parts:
                            try:
                                # Try byte-level progress first (parts[1]=downloaded, parts[2]=total)
                                downloaded = int(float(parts[1]))
                                total = int(float(parts[2]))
                                speed = parts[3] if len(parts) > 3 else "0 B/s"
                                # Support both sync and async callbacks
                                result = progress_callback(downloaded, total, speed)
                                if asyncio.iscoroutine(result):
                                    await result
                            except (ValueError, IndexError):
                                # Fallback: parse percent string (e.g. "45.2%")
                                # Needed for DASH streams where byte totals are N/A
                                try:
                                    pct = float(parts[0].rstrip("%"))
                                    speed = parts[3] if len(parts) > 3 else "0 B/s"
                                    result = progress_callback(
                                        int(pct * 100), 10000, speed
                                    )
                                    if asyncio.iscoroutine(result):
                                        await result
                                except (ValueError, IndexError):
                                    pass

            async def read_stderr():
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    stderr_lines.append(line.decode("utf-8", errors="replace"))

            await asyncio.gather(read_stdout(), read_stderr())
            await asyncio.wait_for(proc.wait(), timeout=600)

        except asyncio.TimeoutError:
            proc.kill()
            logger.error(f"[yt-dlp] Download timed out: {platform_id}")
            raise RuntimeError(f"yt-dlp download timed out for {platform_id}")

        if proc.returncode != 0:
            error_msg = "".join(stderr_lines).strip()
            parsed_error = YtdlpService._parse_error(error_msg)
            logger.error(f"[yt-dlp] Download failed: {parsed_error}")
            raise RuntimeError(f"yt-dlp download failed: {parsed_error}")

        # Find the downloaded file
        file_path = YtdlpService._find_downloaded_file(output_dir, "video")
        if not file_path:
            raise RuntimeError(f"Downloaded file not found for {platform_id}")

        file_size = os.path.getsize(file_path)
        logger.success(f"[yt-dlp] Video downloaded: {file_path} ({file_size} bytes)")

        return {
            "file_path": file_path,
            "file_size": file_size,
        }

    @staticmethod
    async def download_audio(
        url: ValidatedURL,
        output_dir: str,
        platform_id: str,
        user_id: Optional[str] = None,
    ) -> dict:
        """
        Extract audio only via yt-dlp.

        Args:
            url: Video URL (must have passed validate_url[_async])
            output_dir: Directory to save the file
            platform_id: Used for filename
            user_id: Optional user ID for per-user cookie lookup

        Raises:
            AssertionError: If url is not a ValidatedURL (caller bug)

        Returns:
            dict: {file_path, file_size}
        """
        # Boundary: runtime guard. Caller must validate first.
        assert isinstance(url, ValidatedURL), (
            "YtdlpService.download_audio requires ValidatedURL — "
            "call app.boundary.validate_url[_async] first"
        )
        os.makedirs(output_dir, exist_ok=True)

        output_template = os.path.join(output_dir, "audio.%(ext)s")

        cmd = [
            "yt-dlp",
            "-x",  # Extract audio
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",  # Best quality
            "--no-playlist",
            "--no-warnings",
            *YtdlpService._get_proxy_args(url),
            *YtdlpService._get_cookie_args(url, user_id=user_id),
            "-o",
            output_template,
            url,
        ]

        logger.info(f"[yt-dlp] Extracting audio to {output_dir}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            logger.error(f"[yt-dlp] Audio extraction timed out: {platform_id}")
            raise RuntimeError(f"yt-dlp audio extraction timed out for {platform_id}")

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            parsed_error = YtdlpService._parse_error(error_msg)
            logger.error(f"[yt-dlp] Audio extraction failed: {parsed_error}")
            raise RuntimeError(f"yt-dlp audio extraction failed: {parsed_error}")

        # Find the downloaded audio file
        file_path = YtdlpService._find_downloaded_file(output_dir, "audio")
        if not file_path:
            raise RuntimeError(f"Downloaded audio file not found for {platform_id}")

        file_size = os.path.getsize(file_path)
        logger.success(f"[yt-dlp] Audio extracted: {file_path} ({file_size} bytes)")

        return {
            "file_path": file_path,
            "file_size": file_size,
        }

    @staticmethod
    def _map_metadata_to_media(ytdlp_info: dict, url: str) -> dict:
        """
        Map yt-dlp info_dict to our Media schema fields.

        Args:
            ytdlp_info: yt-dlp --dump-json output
            url: Original input URL

        Returns:
            dict: Data compatible with MediaCreate schema
        """
        platform, handler_type = URLRouter.detect_platform(url)

        # Build platform_id
        # Douyin: use raw aweme_id (backward-compatible with existing DB records)
        # Others: {platform}_{yt-dlp id}
        video_id = ytdlp_info.get("id", "")
        if platform == "douyin":
            platform_id = video_id if video_id else str(hash(url))
        else:
            platform_id = (
                f"{platform}_{video_id}" if video_id else f"{platform}_{hash(url)}"
            )

        # Resolution
        width = ytdlp_info.get("width")
        height = ytdlp_info.get("height")
        resolution = f"{width}x{height}" if width and height else None

        # Duration in seconds → format as MM:SS or HH:MM:SS
        duration_secs = ytdlp_info.get("duration")
        if duration_secs is not None:
            duration_ms = int(float(duration_secs) * 1000)
            duration_str = Utils.format_duration(duration_ms)
        else:
            duration_str = None

        # File size
        filesize = ytdlp_info.get("filesize") or ytdlp_info.get("filesize_approx")
        datasize = Utils.format_file_size(filesize) if filesize else None

        # Published date
        upload_date = ytdlp_info.get("upload_date")  # YYYYMMDD format
        published_at = None
        if upload_date and len(upload_date) == 8:
            try:
                published_at = datetime.strptime(upload_date, "%Y%m%d")
            except ValueError:
                pass

        # Thumbnail
        thumbnail = ytdlp_info.get("thumbnail")
        thumbnails = ytdlp_info.get("thumbnails", [])
        cover_urls = []
        if thumbnail:
            # Upgrade HTTP to HTTPS for CDN URLs
            if thumbnail.startswith("http://"):
                thumbnail = thumbnail.replace("http://", "https://", 1)
            cover_urls.append(thumbnail)
        elif thumbnails:
            # Get the best quality thumbnail
            best = max(thumbnails, key=lambda t: t.get("height", 0) or 0)
            if best.get("url"):
                cover_urls.append(best["url"])

        # webpage_url for original_url field (canonical landing page).
        # NOT used as a download URL — see video_download_urls below.
        original_url = ytdlp_info.get("webpage_url") or url

        # Description / title
        title = ytdlp_info.get("title") or ytdlp_info.get("fulltitle") or "Untitled"
        description = ytdlp_info.get("description") or ""

        # Engagement metrics
        like_count = ytdlp_info.get("like_count") or 0
        comment_count = ytdlp_info.get("comment_count") or 0

        # Hashtags
        tags = ytdlp_info.get("tags") or []
        hashtags = " ".join(f"#{t}" for t in tags[:20]) if tags else None

        return {
            "platform_id": platform_id,
            "source_platform": platform,
            "original_url": original_url,
            "title": title,
            "description": description,
            "author": ytdlp_info.get("uploader") or ytdlp_info.get("channel") or "",
            "duration": duration_str,
            "resolution": resolution,
            "datasize": datasize,
            "datasize_bytes": filesize,
            "media_type": "0",  # Standard video
            "like_count": like_count,
            "comment_count": comment_count,
            "share_count": 0,
            "favorite_count": 0,
            "hashtags": hashtags,
            "published_at": published_at,
            "cover_urls": cover_urls,
            # Empty list — yt-dlp platforms (bilibili / youtube / twitter)
            # have token-protected DASH streams that yt-dlp resolves at
            # download time. Writing the page URL here (PR #266 did this)
            # made download_strategies.py treat it as an httpx GET target,
            # which silently downloaded the bilibili HTML page into
            # video.mp4 and reported "moov atom not found" — every
            # bilibili Fetch wasted ~3s before falling back to the real
            # yt-dlp path. download_video_by_platform_id sees the empty
            # list and returns FAILED early, so the fallback runs cleanly.
            "video_download_urls": [],
        }

    @staticmethod
    async def _fetch_bilibili_stats(bvid: str) -> Optional[dict]:
        """Fetch detailed stats from Bilibili public API.

        Args:
            bvid: Bilibili video BV ID

        Returns:
            dict with keys like 'favorite', 'share', 'view', 'like', 'coin', etc.
            None on failure.
        """
        try:
            async with safe_async_client() as client:
                resp = await client.get(
                    f"https://api.bilibili.com/x/web-interface/archive/stat?bvid={bvid}",
                    timeout=10.0,
                )
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("data", {})
        except Exception as e:
            logger.warning(f"[yt-dlp] Failed to fetch Bilibili stats for {bvid}: {e}")
        return None

    @staticmethod
    def _get_proxy_args(url: str) -> list[str]:
        """Return ['--proxy', 'http://...'] for the platform.

        Priority order:
          1. EXTERNAL proxy for international platforms (YouTube/Twitter)
             via YT_DLP_PROXY_YOUTUBE / YT_DLP_PROXY / HTTPS_PROXY env.
             yt-dlp can only use ONE --proxy at a time, so when the user
             needs an external VPN proxy for international content the
             boundary SsrfProxy is bypassed for that single hop.
          2. TRUSTED PLATFORM direct connect (no proxy) for any URL whose
             host matches URLRouter.PLATFORM_PATTERNS (douyin / bilibili /
             xhs / tiktok / instagram). The entry URL was already validated
             at parse time via app.boundary.validate_url, and yt-dlp's
             internal hops then target the platform's hardcoded extractor
             endpoints (api.bilibili.com, *.bilivideo.com, ...) which are
             NOT user-controlled — there is no SSRF surface to defend at
             this layer. 2026-05-13 incident: routing every hop through
             SsrfProxy added a transparent TCP tunnel hop without idle
             timeout, so when bilibili upstream went silent post-TLS-
             handshake the tunnel stalled for 60s before yt-dlp's own
             socket-timeout fired (ssrf_proxy.py::_pipe). Bypassing for
             trusted hosts lets yt-dlp see upstream failures directly and
             retry via --retries 3 within its own timeout budget.
          3. BOUNDARY SsrfProxy (settings.SSRF_PROXY_URL) only for
             *unknown* hostnames — defense-in-depth, since unknown URLs
             could theoretically be user-controlled and might dispatch
             yt-dlp's generic webpage scraper.
          4. No proxy if neither path applies (degraded — happens during
             dev before lifespan starts the proxy).
        """
        import os
        from urllib.parse import urlparse

        from app.core.config import settings
        from app.services.media.parsers.url_router import URLRouter

        host = (urlparse(url).hostname or "").lower()

        # Step 1: international VPN proxy for YouTube/Twitter.
        needs_external_hosts = ("youtube.com", "youtu.be", "twitter.com", "x.com")
        if any(h in host for h in needs_external_hosts):
            external = (
                os.environ.get("YT_DLP_PROXY_YOUTUBE")
                or os.environ.get("YT_DLP_PROXY")
                or os.environ.get("HTTPS_PROXY")
                or os.environ.get("HTTP_PROXY")
                or os.environ.get("ALL_PROXY")
            )
            if external:
                return ["--proxy", external]
            # No external configured → fall through to trusted-platform
            # direct connect (YouTube is in PLATFORM_PATTERNS).

        # Step 2: any URL detected as a known platform connects directly.
        # Entry URL was validated at boundary; yt-dlp's downstream hops
        # are platform-internal endpoints, not user-influenced.
        platform, _ = URLRouter.detect_platform(url)
        if platform != "unknown":
            return []

        # Step 3: unknown host → SsrfProxy defense-in-depth (if running).
        boundary_proxy = settings.SSRF_PROXY_URL
        if boundary_proxy:
            return ["--proxy", boundary_proxy]
        return []

    @staticmethod
    def _get_cookie_args(url: str, user_id: Optional[str] = None) -> list[str]:
        """Return ['--cookies', '/path/to/cookies.txt'] for the URL's platform.

        Priority 1: Per-user cookie from DB (if user_id provided).
        Priority 2: Filesystem cookie from COOKIES_DIR.
        """
        platform, _ = URLRouter.detect_platform(url)
        if not platform or platform == "unknown":
            return []

        # Priority 1: Per-user DB cookie
        if user_id:
            try:
                from app.repositories.cookies_repository import CookiesRepository

                repo = CookiesRepository()
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        record = pool.submit(
                            asyncio.run,
                            repo.get_by_user_and_platform(user_id, platform),
                        ).result()
                else:
                    record = asyncio.run(
                        repo.get_by_user_and_platform(user_id, platform)
                    )

                if record:
                    cookie_content = record.get("cookie_file") or record.get(
                        "cookie_text"
                    )
                    if cookie_content:
                        # Convert browser cookie string to Netscape format
                        # so yt-dlp can parse it correctly.
                        cookie_content = _browser_cookie_to_netscape(
                            cookie_content, platform
                        )
                        tmp = tempfile.NamedTemporaryFile(
                            mode="w",
                            suffix=f"_{platform}.txt",
                            delete=False,
                        )
                        tmp.write(cookie_content)
                        tmp.flush()
                        tmp.close()
                        logger.info(
                            f"[yt-dlp] Using DB cookie for user={user_id} platform={platform}: {tmp.name}"
                        )
                        return ["--cookies", tmp.name]
            except Exception as e:
                logger.warning(
                    f"[yt-dlp] Failed to load DB cookie for user={user_id} platform={platform}: {e}"
                )

        # Priority 2: Filesystem cookie fallback
        from app.core.config import settings

        cookies_dir = settings.COOKIES_DIR
        if not cookies_dir:
            return []

        cookie_file = os.path.join(cookies_dir, f"{platform}.txt")
        if os.path.isfile(cookie_file):
            logger.info(
                f"[yt-dlp] Using filesystem cookie for {platform}: {cookie_file}"
            )
            return ["--cookies", cookie_file]

        return []

    @staticmethod
    async def user_has_cookie(user_id: str, platform: str) -> bool:
        """Check whether a user has a stored cookie for the given platform.

        Args:
            user_id: User ID to look up.
            platform: Platform identifier (e.g. 'douyin', 'bilibili').

        Returns:
            True if a cookie record exists, False otherwise.
        """
        from app.repositories.cookies_repository import CookiesRepository

        repo = CookiesRepository()
        record = await repo.get_by_user_and_platform(user_id, platform)
        return record is not None

    @staticmethod
    def _find_downloaded_file(directory: str, prefix: str) -> Optional[str]:
        """Find a downloaded file in directory matching the given prefix."""
        if not os.path.isdir(directory):
            return None

        for filename in os.listdir(directory):
            if filename.startswith(f"{prefix}."):
                return os.path.join(directory, filename)
        return None

    @staticmethod
    def _parse_error(stderr_output: str) -> str:
        """Parse yt-dlp stderr to extract meaningful error messages."""
        if not stderr_output:
            return "Unknown error"

        # Common error patterns
        error_patterns = {
            "Video unavailable": "Video is unavailable or has been removed",
            "Private video": "Video is private",
            "Sign in to confirm your age": "Video is age-restricted",
            "This video is not available": "Video is not available in your region (geo-blocked)",
            "Unsupported URL": "URL format is not supported by yt-dlp",
            "Unable to extract": "Failed to extract video data from page",
            "HTTP Error 403": "Access forbidden (403)",
            "HTTP Error 404": "Video not found (404)",
            "HTTP Error 429": "Rate limited, please try again later",
        }

        for pattern, message in error_patterns.items():
            if pattern.lower() in stderr_output.lower():
                return message

        # Return last line of stderr (often the most informative)
        lines = [line.strip() for line in stderr_output.split("\n") if line.strip()]
        if lines:
            last_line = lines[-1]
            # Truncate very long error messages
            return last_line[:200] if len(last_line) > 200 else last_line

        return "Unknown yt-dlp error"
