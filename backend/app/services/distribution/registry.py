from app.services.distribution.douyin_adapter import DouyinAdapter, DouyinCredentials
from app.services.distribution.platform_base import PlatformAdapter

_ADAPTERS = {"douyin": DouyinAdapter}


def get_adapter(platform: str, creds: DouyinCredentials) -> PlatformAdapter:
    cls = _ADAPTERS.get(platform)
    if cls is None:
        raise ValueError(f"Unsupported platform: {platform}")
    return cls(creds)
