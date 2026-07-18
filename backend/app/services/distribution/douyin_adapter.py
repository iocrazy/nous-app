import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx
from loguru import logger

from app.services.distribution.platform_base import PlatformAdapter

# ── Douyin API URLs ──────────────────────────────────────────
DOUYIN_AUTH_URL = "https://open.douyin.com/platform/oauth/connect/"
DOUYIN_TOKEN_URL = "https://open.douyin.com/oauth/access_token/"
DOUYIN_REFRESH_URL = "https://open.douyin.com/oauth/refresh_token/"
DOUYIN_USER_URL = "https://open.douyin.com/oauth/userinfo/"
DOUYIN_VIDEO_CREATE_URL = "https://open.douyin.com/api/douyin/v1/video/create/"
DOUYIN_VIDEO_UPLOAD_URL = "https://open.douyin.com/api/douyin/v1/video/upload/"
DOUYIN_CLIENT_TOKEN_URL = "https://open.douyin.com/oauth/client_token/"
DOUYIN_TICKET_URL = "https://open.douyin.com/open/getticket/"

# ── H5 Share: token/ticket cache ────────────────────────────
_client_token_cache: dict = {"token": None, "expires_at": 0}
_ticket_cache: dict = {"ticket": None, "expires_at": 0}


@dataclass(frozen=True)
class DouyinCredentials:
    client_key: str
    client_secret: str
    redirect_uri: str


class DouyinAdapter(PlatformAdapter):
    """Douyin platform adapter wrapping the full Douyin Open API."""

    platform_name = "douyin"

    def __init__(self, creds: DouyinCredentials):
        self._creds = creds

    # ── OAuth ────────────────────────────────────────────────

    def get_auth_url(self, state: str) -> str:
        """Generate Douyin OAuth authorization URL."""
        return (
            f"{DOUYIN_AUTH_URL}"
            f"?client_key={self._creds.client_key}"
            f"&response_type=code"
            f"&scope=trial.whitelist,user_info"
            f"&redirect_uri={self._creds.redirect_uri}"
            f"&state={state}"
        )

    async def exchange_token(self, code: str) -> dict:
        """Exchange authorization code for access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                DOUYIN_TOKEN_URL,
                data={
                    "client_key": self._creds.client_key,
                    "client_secret": self._creds.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            data = response.json()
            if data.get("data", {}).get("error_code", 0) != 0:
                raise RuntimeError(
                    data.get("data", {}).get("description", "Token exchange failed")
                )
            return data["data"]

    async def refresh_token(self, refresh_token: str) -> dict:
        """Refresh expired access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                DOUYIN_REFRESH_URL,
                data={
                    "client_key": self._creds.client_key,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            data = response.json()
            if data.get("data", {}).get("error_code", 0) != 0:
                raise RuntimeError(
                    data.get("data", {}).get("description", "Token refresh failed")
                )
            return data["data"]

    # ── User Info ────────────────────────────────────────────

    async def get_user_info(self, access_token: str, open_id: str) -> dict:
        """Get user info from Douyin. Returns normalized {username, avatar_url}."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                DOUYIN_USER_URL,
                params={"access_token": access_token, "open_id": open_id},
            )
            data = response.json()
            if data.get("data", {}).get("error_code", 0) != 0:
                raise RuntimeError(
                    data.get("data", {}).get("description", "Get user info failed")
                )
            user_data = data["data"]
            return {
                "username": user_data["nickname"],
                "avatar_url": user_data["avatar"],
            }

    # ── Video Publishing ─────────────────────────────────────

    async def _upload_video(
        self, access_token: str, open_id: str, video_url: str
    ) -> str:
        """
        Upload video to Douyin.
        Returns video_id for creating the post.
        """
        # TODO(distribution): switch to Douyin chunked upload
        # (init/part/complete) before enabling the official channel —
        # whole-file in-memory upload won't survive large videos.
        async with httpx.AsyncClient(timeout=300.0) as client:
            # First download the video from Supabase Storage
            video_response = await client.get(video_url)
            video_content = video_response.content

            # Upload to Douyin
            response = await client.post(
                DOUYIN_VIDEO_UPLOAD_URL,
                params={"access_token": access_token, "open_id": open_id},
                files={"video": ("video.mp4", video_content, "video/mp4")},
            )
            data = response.json()
            if data.get("data", {}).get("error_code", 0) != 0:
                raise RuntimeError(
                    data.get("data", {}).get("description", "Video upload failed")
                )
            return data["data"]["video"]["video_id"]

    async def _create_video_post(
        self,
        access_token: str,
        open_id: str,
        video_id: str,
        title: str,
        description: Optional[str] = None,
    ) -> str:
        """
        Create a video post on Douyin.
        Returns the published item_id.
        """
        async with httpx.AsyncClient() as client:
            text = title
            if description:
                text = f"{title}\n{description}"

            response = await client.post(
                DOUYIN_VIDEO_CREATE_URL,
                params={"access_token": access_token, "open_id": open_id},
                json={
                    "video_id": video_id,
                    "text": text,
                },
            )
            data = response.json()
            if data.get("data", {}).get("error_code", 0) != 0:
                raise RuntimeError(
                    data.get("data", {}).get("description", "Video create failed")
                )
            return data["data"]["item_id"]

    async def publish_video(
        self,
        access_token: str,
        open_id: str,
        video_url: str,
        title: str,
        description: Optional[str] = None,
    ) -> str:
        """
        Full flow: upload video and create post.
        Returns the item_id (can be used to construct the video URL).
        """
        video_id = await self._upload_video(access_token, open_id, video_url)
        item_id = await self._create_video_post(
            access_token, open_id, video_id, title, description
        )
        return item_id

    # ── H5 Share Schema ──────────────────────────────────────

    async def _get_client_token(self) -> str:
        """Get client_token (cached, auto-refresh)."""
        now = time.time()
        if (
            _client_token_cache["token"]
            and now < _client_token_cache["expires_at"] - 60
        ):
            return _client_token_cache["token"]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                DOUYIN_CLIENT_TOKEN_URL,
                json={
                    "client_key": self._creds.client_key,
                    "client_secret": self._creds.client_secret,
                    "grant_type": "client_credential",
                },
            )
            data = response.json()
            if data.get("data", {}).get("error_code", 0) != 0:
                raise RuntimeError(
                    data.get("data", {}).get(
                        "description", "Failed to get client_token"
                    )
                )

            token = data["data"]["access_token"]
            expires_in = data["data"]["expires_in"]
            _client_token_cache["token"] = token
            _client_token_cache["expires_at"] = now + expires_in
            logger.info(f"douyin client_token refreshed expires_in={expires_in}")
            return token

    async def _get_ticket(self) -> str:
        """Get jsapi ticket for H5 share signature (cached, valid 2h)."""
        now = time.time()
        if _ticket_cache["ticket"] and now < _ticket_cache["expires_at"] - 60:
            return _ticket_cache["ticket"]

        client_token = await self._get_client_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                DOUYIN_TICKET_URL,
                params={"access_token": client_token},
            )
            data = response.json()
            if data.get("data", {}).get("error_code", 0) != 0:
                raise RuntimeError(
                    data.get("data", {}).get("description", "Failed to get ticket")
                )

            ticket = data["data"]["ticket"]
            expires_in = data["data"]["expires_in"]
            _ticket_cache["ticket"] = ticket
            _ticket_cache["expires_at"] = now + expires_in
            logger.info(f"douyin ticket refreshed expires_in={expires_in}")
            return ticket

    @staticmethod
    def _generate_signature(ticket: str, timestamp: int, nonce_str: str) -> str:
        """Generate MD5 signature for H5 share Schema URL."""
        sign_str = f"nonce_str={nonce_str}&ticket={ticket}&timestamp={timestamp}"
        return hashlib.md5(sign_str.encode()).hexdigest()

    async def generate_share_url(self, **kwargs) -> Optional[str]:
        """
        Generate the Schema URL for H5 share to Douyin.

        Required kwargs:
            video_url (str): URL of the video to share.
            title (str): Title for the shared content.
            share_id (str): Unique share identifier (maps to state param).

        Optional kwargs:
            hashtags (list[str]): Bare topic words (no leading '#'). Encoded as
                a JsonArray string ``["tag1","tag2"]`` for the schema's
                ``hashtag_list`` param — Douyin's documented H5 format (NOT
                comma-separated). On iOS ``hashtag_list`` is ignored when
                ``title`` is empty; we always send a title, so this is moot.

        Returns:
            Schema URL string that opens Douyin app with content pre-filled.
        """
        video_url: str = kwargs["video_url"]
        title: str = kwargs["title"]
        share_id: str = kwargs["share_id"]
        hashtags: list[str] = list(kwargs.get("hashtags") or [])

        ticket = await self._get_ticket()
        timestamp = int(time.time())
        nonce_str = secrets.token_hex(16)
        signature = self._generate_signature(ticket, timestamp, nonce_str)

        params = {
            "client_key": self._creds.client_key,
            "nonce_str": nonce_str,
            "timestamp": str(timestamp),
            "signature": signature,
            "state": share_id,
            "video_url": video_url,
            "title": title,
        }
        if hashtags:
            # JsonArray string, e.g. ["城市","4k"]. Compact separators (no space
            # after comma) keep the URL param tight; ensure_ascii=False keeps
            # CJK topics readable; quote() below percent-encodes the whole value.
            params["hashtag_list"] = json.dumps(
                hashtags, ensure_ascii=False, separators=(",", ":")
            )

        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return f"snssdk1128://openplatform/share?{query}"
