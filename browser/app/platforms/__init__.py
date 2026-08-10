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
    from ..publish import PlatformIntentRules, Publisher
    from ..verify import VerifySpec

SessionValidator = Callable[
    [dict[str, Any], "EnvironmentConfig | None"], Awaitable[SessionResult]
]

_VALIDATORS: dict[str, SessionValidator] = {}
_LOGIN_FLOWS: dict[str, "LoginFlowSpec"] = {}
_PUBLISHERS: dict[str, "Publisher"] = {}
_INTENT_RULES: dict[str, "PlatformIntentRules"] = {}
_VERIFY_SPECS: dict[str, "VerifySpec"] = {}


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


# Publishing is a third independent registry, and stores a coroutine rather than
# a description of DOM steps. Design doc 6.1c: the second platform (Xiaohongshu)
# is expected *not* to publish through the DOM at all - the browser signs the
# request and the upload goes over plain HTTP. An abstraction that assumed
# "publish == a sequence of clicks" would have to be rebuilt to accept it.


def register_publisher(platform: str, publisher: "Publisher") -> None:
    key = platform.strip().lower()
    if key in _PUBLISHERS:
        raise ValueError(f"publisher for '{key}' is already registered")
    _PUBLISHERS[key] = publisher


def get_publisher(platform: str) -> "Publisher | None":
    return _PUBLISHERS.get((platform or "").strip().lower())


def publish_platforms() -> list[str]:
    return sorted(_PUBLISHERS)


# A fourth registry, and the only one holding something *pure*. It is separate
# from the publisher rather than a field on it because it must be reachable
# without the publisher: `run_publish` consults it before a browser exists, and
# a test wanting to check "would this intent be accepted" should not have to
# construct a publish job to ask.
#
# Absence is meaningful here. `validate_intent` reads a missing entry as "this
# platform cannot schedule", so a publisher that forgets to register rules
# degrades into refusing scheduled posts - not into publishing them now.


def register_intent_rules(platform: str, rules: "PlatformIntentRules") -> None:
    key = platform.strip().lower()
    if key in _INTENT_RULES:
        raise ValueError(f"intent rules for '{key}' are already registered")
    _INTENT_RULES[key] = rules


def get_intent_rules(platform: str) -> "PlatformIntentRules | None":
    return _INTENT_RULES.get((platform or "").strip().lower())


# A fifth registry: publish READ-BACK (P1-3). Separate from the publisher for
# the same reason intent rules are — the two are reachable independently. A
# read-back runs on a schedule, hours after the publish that created the post,
# in a different process tick; tying it to the publisher entry would suggest
# they are one operation.
#
# Absence is meaningful, and it is what keeps the honest answer available: a
# platform with no read-back gets a typed `not_supported`, which the backend
# records as "verification is not available here" rather than pretending the
# post was confirmed. Xiaohongshu and Bilibili cannot publish at all, so they
# will never need one; a future platform that can publish but cannot be read
# back must say so out loud rather than inherit Douyin's answer.


def register_verify_spec(platform: str, spec: "VerifySpec") -> None:
    key = platform.strip().lower()
    if key in _VERIFY_SPECS:
        raise ValueError(f"verify spec for '{key}' is already registered")
    _VERIFY_SPECS[key] = spec


def get_verify_spec(platform: str) -> "VerifySpec | None":
    return _VERIFY_SPECS.get((platform or "").strip().lower())


def verify_platforms() -> list[str]:
    return sorted(_VERIFY_SPECS)


# Importing the module performs its registration.
from . import douyin as _douyin  # noqa: E402,F401
from . import douyin_publish as _douyin_publish  # noqa: E402,F401
from . import douyin_verify as _douyin_verify  # noqa: E402,F401

# Xiaohongshu / Bilibili register a validator + a login flow, but deliberately
# NO publisher and NO intent rules: binding an account and keeping its session
# alive is implemented, publishing is not.
#
# That split is load-bearing rather than an oversight. `get_validator` /
# `get_login_flow` find them, so accounts bind and stay healthy; `get_publisher`
# does not, so a publish request for these platforms is refused by the same
# typed path that refuses any unknown platform — it can never reach a browser
# and improvise on an unwritten flow. Registering an empty publisher "to be
# filled in later" is what would turn a missing feature into a silent one.
from . import xiaohongshu as _xiaohongshu  # noqa: E402,F401
from . import bilibili as _bilibili  # noqa: E402,F401
