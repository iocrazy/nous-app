"""平台适配器抽象 — port 自 media-router 原型（已双通道验证）。"""

from abc import ABC, abstractmethod
from typing import Optional


class PlatformAdapter(ABC):
    platform_name: str

    @abstractmethod
    def get_auth_url(self, state: str) -> str: ...

    @abstractmethod
    async def exchange_token(self, code: str) -> dict:
        """→ {access_token, refresh_token, open_id, expires_in}"""

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> dict: ...

    @abstractmethod
    async def get_user_info(self, access_token: str, open_id: str) -> dict:
        """→ {username, avatar_url}"""

    @abstractmethod
    async def publish_video(
        self,
        access_token: str,
        open_id: str,
        video_url: str,
        title: str,
        description: Optional[str] = None,
    ) -> str:
        """→ platform item_id"""

    async def generate_share_url(self, **kwargs) -> Optional[str]:
        return None
