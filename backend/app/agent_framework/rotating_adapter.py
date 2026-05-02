"""RotatingAdapter — multi-key adapter wrapper that rotates on 429/auth fail.

Wraps a callable that produces an adapter for a given API key. On
``call``, picks the next non-cooled key, builds the adapter, and
delegates. On 429 / 401 / 403, marks the key in cooldown and retries
with the next key.

Used by ai_adapters factory when ai_providers[provider].api_key is a
List[str] (multi-key BYO config).

Usage:

    from app.agent_framework import RotatingAdapter, KeyRotator

    rotator = KeyRotator(["sk-key1", "sk-key2", "sk-key3"])

    def build(key: str) -> AIAdapter:
        return DoubaoAdapter(api_url=..., api_key=key, default_model=...)

    adapter = RotatingAdapter(rotator, build)
    response = await adapter.call(composed, messages)  # auto-rotates
"""
from __future__ import annotations

from typing import Any, Callable

from loguru import logger

from app.agent_framework.key_rotation import (
    AllKeysCooledDown,
    KeyRotator,
)


class RotatingAdapter:
    """Adapter facade that rotates the underlying API key on rate-limit
    or auth failure.

    Quacks like an ``AIAdapter`` (must expose ``async def call(composed,
    messages) -> dict``) — the underlying single-key adapter is built
    fresh per attempt via the factory callable.

    Not stored as an AIAdapter subclass to avoid circular imports —
    duck typing via ``call()`` shape is sufficient for AgentRunner.
    """

    def __init__(
        self,
        rotator: KeyRotator,
        adapter_factory: Callable[[str], Any],
    ) -> None:
        """Args:
            rotator: KeyRotator pre-configured with the user's keys.
            adapter_factory: callable(api_key: str) -> AIAdapter.
                Closure over the rest of the config (base_url, model,
                etc.) supplied by the caller (typically the
                ai_adapters factory).
        """
        self._rotator = rotator
        self._factory = adapter_factory

    async def call(
        self,
        composed: Any,
        messages: list,
    ) -> dict:
        """Try each available key. Rotate on 429/401/403, surface
        anything else immediately.

        Raises:
            AllKeysCooledDown: every key exhausted (caller surfaces
              "rate limited, try later" to the user).
            (any other exception): propagated from the adapter.
        """
        attempts = 0
        max_attempts = len(self._rotator)
        last_exc: Exception | None = None

        while attempts < max_attempts:
            attempts += 1
            try:
                key = self._rotator.next_key()
            except AllKeysCooledDown:
                if last_exc is not None:
                    raise last_exc from None
                raise

            adapter = self._factory(key)
            try:
                return await adapter.call(composed, messages)
            except Exception as exc:
                status = _extract_http_status(exc)
                if status in (429, 401, 403, 500, 502, 503, 504):
                    logger.info(
                        f"[RotatingAdapter] key cooldown: status={status} "
                        f"({attempts}/{max_attempts})"
                    )
                    self._rotator.report_status(key, status)
                    last_exc = exc
                    continue
                # Non-rotatable error — surface immediately
                raise

        # Out of attempts — re-raise last error or the cooldown exception
        if last_exc is not None:
            raise last_exc
        raise AllKeysCooledDown("rotated through all keys without success")


def _extract_http_status(exc: BaseException) -> int | None:
    """Best-effort extract HTTP status from various exception shapes
    (httpx HTTPStatusError / openai APIError / generic with .status_code
    or .response.status_code)."""
    # httpx.HTTPStatusError, openai.APIError, etc.
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    # Last-ditch: parse status from message
    msg = str(exc)
    for code in (429, 401, 403, 500, 502, 503, 504):
        if str(code) in msg:
            return code
    return None
