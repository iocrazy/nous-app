"""Platform registry.

`platform -> validator` indirection so adding Kuaishou/Xiaohongshu is a new
module plus one `register()` call, with no branching in the HTTP layer. The
registry stores callables rather than DOM specs on purpose: a platform whose
session check is not a DOM walk (the "browser as signing machine" tier) has to
be expressible without reshaping the registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..schemas import EnvironmentConfig, SessionResult

if TYPE_CHECKING:  # pragma: no cover - import cycle: login imports schemas only
    from ..login import LoginFlowSpec

SessionValidator = Callable[
    [dict[str, Any], "EnvironmentConfig | None"], Awaitable[SessionResult]
]

_VALIDATORS: dict[str, SessionValidator] = {}
_LOGIN_FLOWS: dict[str, "LoginFlowSpec"] = {}


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


# Login is a *separate* registry rather than a second field on one entry: a
# platform can be validatable without being bindable here (an account imported
# by hand, or one whose login is not a QR scan at all), and forcing both to
# arrive together would mean stubbing one of them.


def register_login(platform: str, spec: "LoginFlowSpec") -> None:
    key = platform.strip().lower()
    if key in _LOGIN_FLOWS:
        raise ValueError(f"login flow for '{key}' is already registered")
    _LOGIN_FLOWS[key] = spec


def get_login_flow(platform: str) -> "LoginFlowSpec | None":
    return _LOGIN_FLOWS.get((platform or "").strip().lower())


def login_platforms() -> list[str]:
    return sorted(_LOGIN_FLOWS)


# Importing the module performs its registration.
from . import douyin as _douyin  # noqa: E402,F401
