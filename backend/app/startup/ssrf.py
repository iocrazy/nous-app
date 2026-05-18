"""SsrfProxy startup/teardown (B9-D/E boundary).

Populates `settings.SSRF_PROXY_URL` so downstream subprocess + browser
clients (yt-dlp, DrissionPage, ffmpeg) read the actual port.
"""

from fastapi import FastAPI
from loguru import logger

from app.core.config import settings


async def start_ssrf_proxy(app: FastAPI) -> None:
    app.state.ssrf_proxy = None
    try:
        from app.boundary import SsrfProxy

        proxy = SsrfProxy()
        await proxy.start()
        settings.SSRF_PROXY_URL = proxy.url
        app.state.ssrf_proxy = proxy
        logger.info(f"Boundary SsrfProxy started at {proxy.url}")
    except Exception as e:
        logger.warning(f"Failed to start SsrfProxy: {e}")


async def stop_ssrf_proxy(app: FastAPI) -> None:
    proxy = getattr(app.state, "ssrf_proxy", None)
    if proxy is None:
        return
    try:
        await proxy.stop()
        logger.info("Boundary SsrfProxy stopped")
    except Exception as e:
        logger.warning(f"SsrfProxy shutdown error: {e}")
