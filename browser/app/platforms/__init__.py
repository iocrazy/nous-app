"""Platform registry.

`platform -> validator` indirection so adding Kuaishou/Xiaohongshu is a new
module plus one `register()` call, with no branching in the HTTP layer. The
registry stores callables rather than DOM specs on purpose: a platform whose
session check is not a DOM walk (the "browser as signing machine" tier) has to
be expressible without reshaping the registry.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..schemas import EnvironmentConfig, SessionResult

SessionValidator = Callable[
    [dict[str, Any], "EnvironmentConfig | None"], Awaitable[SessionResult]
]

_VALIDATORS: dict[str, SessionValidator] = {}


def register(platform: str, validator: SessionValidator) -> None:
    key = platform.strip().lower()
    if key in _VALIDATORS:
        # Two validators for one platform is the exact failure mode this
        # registry exists to prevent (one copy gets fixed, the other rots).
        raise ValueError(f"session validator for '{key}' is already registered")
    _VALIDATORS[key] = validator


def get_validator(platform: str) -> SessionValidator | None:
    return _VALIDATORS.get((platform or "").strip().lower())


def supported_platforms() -> list[str]:
    return sorted(_VALIDATORS)


# Importing the module performs its registration.
from . import douyin as _douyin  # noqa: E402,F401
