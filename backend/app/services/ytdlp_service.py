# backend/app/services/ytdlp_service.py

"""
yt-dlp Service

Universal video metadata fetching and downloading via yt-dlp.
Supports YouTube, Bilibili, Twitter/X, TikTok, Instagram, Xiaohongshu, and more.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Callable, Optional

import httpx
from loguru import logger

from app.core.utils import Utils
from app.services.url_router import URLRouter


class YtdlpService:
    """Universal video download via yt-dlp"""

    @staticmethod
    async def fetch_metadata(url: str) -> dict:
        """
        Fetch video metadata using yt-dlp --dump-json.

        Args:
            url: Video URL

        Returns:
            dict: yt-dlp info_dict with video metadata

        Raises:
            RuntimeError: If yt-dlp fails or returns no data
        """
        logger.info(f"[yt-dlp] Fetching metadata: {url}")

        cmd = [
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "--no-warnings",
            "--no-playlist",
            url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            logger.error(f"[yt-dlp] Metadata fetch timed out: {url}")
            raise RuntimeError(f"yt-dlp metadata fetch timed out for {url}")

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
        url: str,
        output_dir: str,
        platform_id: str,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Download video file via yt-dlp with real-time progress tracking.

        Args:
            url: Video URL
            output_dir: Directory to save the file
            platform_id: Used for filename
            progress_callback: Optional callback(downloaded, total, speed) for progress updates

        Returns:
            dict: {file_path, file_size}
        """
        os.makedirs(output_dir, exist_ok=True)

        output_template = os.path.join(output_dir, f"{platform_id}.%(ext)s")

        cmd = [
            "yt-dlp",
            "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "--no-playlist",
            "--no-warnings",
            "--newline",
            "--progress-template",
            "download:%(progress._downloaded_bytes)s %(progress._total_bytes_estimate)s %(progress._speed_str)s",
            "-o",
            output_template,
            url,
        ]

        logger.info(f"[yt-dlp] Downloading video: {platform_id}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stderr_lines = []

            async def read_stdout():
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded.startswith("download:") and progress_callback:
                        parts = decoded[len("download:") :].split()
                        if len(parts) >= 2:
                            try:
                                downloaded = int(float(parts[0]))
                                total = int(float(parts[1]))
                                speed = parts[2] if len(parts) > 2 else "0 B/s"
                                progress_callback(downloaded, total, speed)
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
        file_path = YtdlpService._find_downloaded_file(output_dir, platform_id)
        if not file_path:
            raise RuntimeError(f"Downloaded file not found for {platform_id}")

        file_size = os.path.getsize(file_path)
        logger.success(f"[yt-dlp] Video downloaded: {file_path} ({file_size} bytes)")

        return {
            "file_path": file_path,
            "file_size": file_size,
        }

    @staticmethod
    async def download_audio(url: str, output_dir: str, platform_id: str) -> dict:
        """
        Extract audio only via yt-dlp.

        Args:
            url: Video URL
            output_dir: Directory to save the file
            platform_id: Used for filename

        Returns:
            dict: {file_path, file_size}
        """
        os.makedirs(output_dir, exist_ok=True)

        output_template = os.path.join(output_dir, f"{platform_id}_audio.%(ext)s")

        cmd = [
            "yt-dlp",
            "-x",  # Extract audio
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",  # Best quality
            "--no-playlist",
            "--no-warnings",
            "-o",
            output_template,
            url,
        ]

        logger.info(f"[yt-dlp] Extracting audio: {platform_id}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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
        file_path = YtdlpService._find_downloaded_file(
            output_dir, f"{platform_id}_audio"
        )
        if not file_path:
            raise RuntimeError(f"Downloaded audio file not found for {platform_id}")

        file_size = os.path.getsize(file_path)
        logger.success(f"[yt-dlp] Audio extracted: {file_path} ({file_size} bytes)")

        return {
            "file_path": file_path,
            "file_size": file_size,
        }

    @staticmethod
    def _map_metadata_to_video(ytdlp_info: dict, url: str) -> dict:
        """
        Map yt-dlp info_dict to our Video schema fields.

        Args:
            ytdlp_info: yt-dlp --dump-json output
            url: Original input URL

        Returns:
            dict: Data compatible with VideoCreate schema
        """
        platform, handler_type = URLRouter.detect_platform(url)

        # Build platform_id: {platform}_{yt-dlp id}
        video_id = ytdlp_info.get("id", "")
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

        # Video download URL (the original URL, yt-dlp will handle actual download)
        original_url = ytdlp_info.get("webpage_url") or url

        # Description / title
        title = ytdlp_info.get("title") or ytdlp_info.get("fulltitle") or "Untitled"
        description = ytdlp_info.get("description") or ""

        # Engagement metrics
        like_count = ytdlp_info.get("like_count") or 0
        comment_count = ytdlp_info.get("comment_count") or 0
        view_count = ytdlp_info.get("view_count") or 0

        # Hashtags
        tags = ytdlp_info.get("tags") or []
        hashtags = " ".join(f"#{t}" for t in tags[:20]) if tags else None

        return {
            "platform_id": platform_id,
            "external_id": video_id,
            "source_platform": platform,
            "source_url": url,
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
            "video_download_urls": [original_url],
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
            async with httpx.AsyncClient() as client:
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
    def _find_downloaded_file(directory: str, prefix: str) -> Optional[str]:
        """Find a downloaded file in directory matching the given prefix."""
        if not os.path.isdir(directory):
            return None

        for filename in os.listdir(directory):
            if filename.startswith(prefix):
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
