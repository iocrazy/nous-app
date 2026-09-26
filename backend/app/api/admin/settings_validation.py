"""Known-key allowlist for the generic ``PATCH /admin/settings/{key}`` path.

The typed admin endpoints (``/agent-cost-anomaly``, ``/memory/slot``,
``/ai-governance``, transcode ``/settings`` …) validate what they write; the
generic path used to accept any JSON for any existing key, a second door that
bypassed all of them. Readers fall back to defaults on garbage, so this is
defence in depth: a wrong shape here does not crash anything, it silently
turns a feature off (``nous.user_enabled`` is read with ``is True``; a string
``"true"`` reads as off).

Each rule accepts the shapes production already stores and normalises to the
shape the key's reader (and its typed writer) expects, so re-saving an
existing row is a no-op. Unknown keys pass through unchanged — the generic
path stays generic.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.ai.governance.ai_governance import ALL_MODULES, NOUS_GLOBAL_KEY

# Legal providers per memory slot (moved here from settings_router so the
# generic PATCH and PUT /memory/slot share one table). Phase 1 only —
# Mem0/Hindsight (Phase 3) extend these sets.
VALID_SLOT_PROVIDERS: dict[str, frozenset[str]] = {
    "l2": frozenset({"honcho", "none"}),
    "l3": frozenset({"graphiti", "none"}),
}

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})


class SettingValidationError(ValueError):
    """A known setting key got a value its reader cannot use."""

    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"{key}: {reason}")


class _Invalid(ValueError):
    """Raised by a rule's check; re-raised as SettingValidationError with the key."""


@dataclass(frozen=True)
class SettingRule:
    check: Callable[[Any], Any]
    #: One line: which reader dictates the shape. Guarded by a test.
    why: str


def _unquote(raw: Any) -> str:
    # jsonb strings can surface as raw JSON text ('"2"'); readers strip quotes.
    return str(raw).strip().strip('"').strip()


def _as_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        word = _unquote(raw).lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise _Invalid(f"expected a boolean or one of true/false, got {raw!r}")


def _json_bool(raw: Any) -> bool:
    return _as_bool(raw)


def _string_bool(raw: Any) -> str:
    return "true" if _as_bool(raw) else "false"


def _number(raw: Any) -> float:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise _Invalid(f"expected a number, got {raw!r}")
    try:
        value = float(_unquote(raw))
    except ValueError as exc:
        raise _Invalid(f"expected a number, got {raw!r}") from exc
    if not math.isfinite(value):
        raise _Invalid("must be finite")
    return value


def _integer(raw: Any) -> int:
    if isinstance(raw, bool):
        raise _Invalid(f"expected an integer, got {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(_unquote(raw))
        except ValueError as exc:
            raise _Invalid(f"expected an integer, got {raw!r}") from exc
    raise _Invalid(f"expected an integer, got {raw!r}")


def _non_negative_number(raw: Any) -> float:
    value = _number(raw)
    if value < 0:
        raise _Invalid("must be >= 0")
    return value


def _non_negative_int(raw: Any) -> int:
    value = _integer(raw)
    if value < 0:
        raise _Invalid("must be >= 0")
    return value


def _positive_int(raw: Any) -> int:
    value = _integer(raw)
    if value < 1:
        raise _Invalid("must be >= 1")
    return value


def _string(raw: Any) -> str:
    if not isinstance(raw, str):
        raise _Invalid(f"expected a string, got {raw!r}")
    return raw.strip()


def _provider(slot: str) -> Callable[[Any], str]:
    allowed = VALID_SLOT_PROVIDERS[slot]

    def check(raw: Any) -> str:
        choice = _unquote(_string(raw))
        if choice not in allowed:
            raise _Invalid(f"provider {choice!r} not in {sorted(allowed)}")
        return choice

    return check


def _object_of(fields: dict[str, Callable[[Any], Any]]) -> Callable[[Any], dict]:
    def check(raw: Any) -> dict:
        if not isinstance(raw, dict):
            raise _Invalid(f"expected an object with keys {sorted(fields)}")
        unknown = set(raw) - set(fields)
        if unknown:
            raise _Invalid(f"unknown fields {sorted(unknown)}")
        return {name: fields[name](value) for name, value in raw.items()}

    return check


#: Admin AI Models page: custom names for provider cards. A card is every
#: ``nous_models`` row sharing ``actual_provider`` + ``base_url``, keyed
#: ``"<actual_provider>|<base_url>"`` (blank base URL → ``"nous|"``).
AI_PROVIDER_CARD_LABELS_KEY = "ai_provider_card_labels"
CARD_LABEL_MAX_LEN = 64
CARD_LABEL_KEY_MAX_LEN = 512
CARD_LABELS_MAX_ENTRIES = 500


def _card_labels(raw: Any) -> dict[str, str]:
    """``{"<provider>|<base_url>": name}``; a blank name drops the override.

    Returns a new dict — the input is never mutated."""
    if not isinstance(raw, dict):
        raise _Invalid("expected an object mapping '<provider>|<base_url>' to a name")
    out: dict[str, str] = {}
    for card_key, label in raw.items():
        if not isinstance(card_key, str) or "|" not in card_key:
            raise _Invalid(
                f"card key must look like '<provider>|<base_url>', got {card_key!r}"
            )
        provider = card_key.split("|", 1)[0]
        if not provider.strip() or len(card_key) > CARD_LABEL_KEY_MAX_LEN:
            raise _Invalid(f"invalid card key {card_key!r}")
        if not isinstance(label, str):
            raise _Invalid(f"name for {card_key!r} must be a string, got {label!r}")
        name = label.strip()
        if len(name) > CARD_LABEL_MAX_LEN:
            raise _Invalid(
                f"name for {card_key!r} exceeds {CARD_LABEL_MAX_LEN} characters"
            )
        if name:
            out[card_key] = name
    if len(out) > CARD_LABELS_MAX_ENTRIES:
        raise _Invalid(f"at most {CARD_LABELS_MAX_ENTRIES} card names")
    return out


_MODULE_BOOL_WHY = "ai_governance reads it with isinstance(bool); strings are ignored"


def _shots_mode(raw: Any) -> str:
    from app.services.library.shot_policy import PolicyValueError, check_mode

    try:
        return check_mode(raw)
    except PolicyValueError as e:
        raise _Invalid(e.reason) from e


def _shots_bounded(lo: int, hi: int) -> Callable[[Any], int]:
    def check(raw: Any) -> int:
        from app.services.library.shot_policy import (
            PolicyValueError,
            check_bounded_int,
        )

        try:
            return check_bounded_int(raw, lo, hi)
        except PolicyValueError as e:
            raise _Invalid(e.reason) from e

    return check


_shots_batch = _shots_bounded(1, 50)
_shots_daily_cap = _shots_bounded(0, 10_000)

SETTING_VALIDATORS: dict[str, SettingRule] = {
    "agent_cost_anomaly.min_hour_cost_cents": SettingRule(
        _non_negative_number,
        "parse_min_hour_cost_cents: finite float >= 0, same as PUT /agent-cost-anomaly",
    ),
    "inspiration.max_attachment_mb": SettingRule(
        _positive_int,
        "attachment_service int()s it; 0 would reject every upload",
    ),
    "transcode_min_size_mb": SettingRule(
        _non_negative_int,
        "transcode_service int()s it; 0 means transcode everything",
    ),
    "transcode_enabled": SettingRule(
        _json_bool,
        "transcode PUT writes a JSON bool and startup copies it onto settings",
    ),
    NOUS_GLOBAL_KEY: SettingRule(
        _json_bool,
        "ai_governance and the admin bundle read it with `is True`",
    ),
    "graph_memory_enabled": SettingRule(
        _string_bool,
        "graph memory PUT and mig 291 store 'true'/'false' strings",
    ),
    "honcho_memory_enabled": SettingRule(
        _string_bool,
        "honcho connection PUT stores 'true'/'false' strings",
    ),
    "issue_agent_auto_close": SettingRule(
        _string_bool,
        "mig 293 seeds 'false'; issue_lifecycle matches truthy words on str()",
    ),
    "memory.l2_provider": SettingRule(
        _provider("l2"), "memory registry; same table as PUT /memory/slot"
    ),
    "memory.l3_provider": SettingRule(
        _provider("l3"), "memory registry; same table as PUT /memory/slot"
    ),
    "maintenance_llm_model": SettingRule(
        _string,
        "get_maintenance_model reads a string; blank means the default model",
    ),
    "storage.unified_storage": SettingRule(
        _object_of({"enabled": _json_bool, "visible": _json_bool}),
        "modules registry reads {enabled, visible} and only honours JSON bools",
    ),
    "workflow_autopilot": SettingRule(
        _object_of({"daily_auto_runs": _non_negative_int}),
        "autopilot reads {daily_auto_runs: int} and ignores non-dict values",
    ),
    AI_PROVIDER_CARD_LABELS_KEY: SettingRule(
        _card_labels,
        "admin AI Models page reads {card_key: name}; blank names are dropped",
    ),
    # Shot-index automation (spec 2026-09-26 §3.1); readers in
    # services.library.shot_policy fall back to defaults on anything else.
    "ai_module.shots.auto_index": SettingRule(
        _shots_mode, "shot_policy.parse_mode: off / local_only / always"
    ),
    "ai_module.shots.backfill": SettingRule(
        _shots_mode, "shot_policy.parse_mode: off / local_only / always"
    ),
    "ai_module.shots.backfill_batch": SettingRule(
        _shots_batch, "shot_policy: int in [1, 50], videos per sweeper tick"
    ),
    "ai_module.shots.backfill_daily_cap": SettingRule(
        _shots_daily_cap, "shot_policy: int in [0, 10000], 0 = no cap"
    ),
    **{
        f"ai_module.{module}.{flag}": SettingRule(_json_bool, _MODULE_BOOL_WHY)
        for module in sorted(ALL_MODULES)
        for flag in ("user_allowed", "nous_allowed")
    },
}


def validate_setting_value(key: str, value: Any) -> Any:
    """Return ``value`` normalised for ``key``; unknown keys pass through.

    Raises ``SettingValidationError`` when a known key gets a value its
    reader cannot use."""
    rule = SETTING_VALIDATORS.get(key)
    if rule is None:
        return value
    try:
        return rule.check(value)
    except _Invalid as exc:
        raise SettingValidationError(key, str(exc)) from exc
