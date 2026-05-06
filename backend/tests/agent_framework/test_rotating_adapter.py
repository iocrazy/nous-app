"""RotatingAdapter — multi-key rotation on 429/auth fail."""
from __future__ import annotations

import pytest

from app.agent_framework import (
    AllKeysCooledDown,
    KeyRotator,
    RotatingAdapter,
)


class _StubAdapter:
    """Minimal adapter — records the API key it was built with."""

    def __init__(self, api_key: str, behavior: dict[str, dict]) -> None:
        self.api_key = api_key
        self._behavior = behavior

    async def call(self, composed, messages):
        spec = self._behavior.get(self.api_key, {"ok": True})
        if "raise_status" in spec:
            class HTTPErr(Exception):
                pass
            err = HTTPErr(f"HTTP {spec['raise_status']}")
            err.status_code = spec["raise_status"]  # type: ignore[attr-defined]
            raise err
        if "raise_other" in spec:
            raise spec["raise_other"]
        return {"content": f"ok from {self.api_key}", "raw": None}


def _factory(behavior: dict[str, dict]):
    def build(api_key: str) -> _StubAdapter:
        return _StubAdapter(api_key, behavior)
    return build


@pytest.mark.unit
async def test_first_key_succeeds():
    rotator = KeyRotator(["k1", "k2"])
    adapter = RotatingAdapter(rotator, _factory({}))
    result = await adapter.call(None, [])
    assert "k1" in result["content"]


@pytest.mark.unit
async def test_429_rotates_to_next_key():
    """k1 rate limited → swap to k2 → success."""
    rotator = KeyRotator(["k1", "k2"])
    adapter = RotatingAdapter(
        rotator,
        _factory({"k1": {"raise_status": 429}}),
    )
    result = await adapter.call(None, [])
    assert "k2" in result["content"]


@pytest.mark.unit
async def test_401_rotates():
    rotator = KeyRotator(["k1", "k2"])
    adapter = RotatingAdapter(
        rotator,
        _factory({"k1": {"raise_status": 401}}),
    )
    result = await adapter.call(None, [])
    assert "k2" in result["content"]


@pytest.mark.unit
async def test_all_keys_429_raises():
    """Every key cooled down → AllKeysCooledDown after exhaustion."""
    rotator = KeyRotator(["k1", "k2"])
    adapter = RotatingAdapter(
        rotator,
        _factory({"k1": {"raise_status": 429}, "k2": {"raise_status": 429}}),
    )
    with pytest.raises(Exception):  # AllKeysCooledDown OR last 429
        await adapter.call(None, [])


@pytest.mark.unit
async def test_400_does_not_rotate_surfaced_immediately():
    """400 = bad request, NOT key's fault. Don't rotate, raise immediately."""
    rotator = KeyRotator(["k1", "k2"])
    adapter = RotatingAdapter(
        rotator,
        _factory({"k1": {"raise_status": 400}}),
    )
    with pytest.raises(Exception, match="400"):
        await adapter.call(None, [])


@pytest.mark.unit
async def test_non_http_exception_surfaced_immediately():
    """A bug (non-HTTP exception) is not a rotation trigger."""
    rotator = KeyRotator(["k1", "k2"])
    adapter = RotatingAdapter(
        rotator,
        _factory({"k1": {"raise_other": ValueError("bug")}}),
    )
    with pytest.raises(ValueError, match="bug"):
        await adapter.call(None, [])
