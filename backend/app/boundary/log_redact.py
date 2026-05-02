"""Sensitive-token redaction for log output.

Layer 1 defense against accidental secret leakage via loguru. Mask
known secret shapes (sk-…, Bearer …, JWT eyJ…, KEY=value) before the
sink writes the record. Tightened patterns (CEO H3 / Eng review):
require a known prefix + minimum length so random IDs (UUIDs, Snowflake
BIGINTs, commit SHAs) DON'T get falsely masked into noise.

Two usage modes:

    # Direct call — useful for ad-hoc message construction:
    from app.boundary.log_redact import redact
    safe_message = redact(f"Bearer {token}")  # → "Bearer ***"

    # Loguru sink patcher — install once at app startup:
    from loguru import logger
    from app.boundary.log_redact import make_loguru_patcher
    logger.configure(patcher=make_loguru_patcher())

The patcher walks BOTH ``record["message"]`` (the formatted string)
AND ``record["extra"]`` dict values (Eng review E6 — bound context via
``logger.bind(token=...)`` lives here, not in message).
"""
from __future__ import annotations

import re
from typing import Any, Callable

# Patterns, ordered most-specific first. Each pattern requires a known
# prefix + minimum length. The minimum lengths are calibrated:
# - Bearer/sk-/token=: 20 chars after prefix (Snowflake BIGINTs are
#   19 digits, so 20 keeps them clear)
# - JWT 3-segment: each segment ≥ 16 chars (eliminates version.module.commit)
# - KEY=value env: matches only if KEY name ends in KEY/TOKEN/SECRET/PASSWORD

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # KEY=value env style — runs FIRST so it captures the full value
    # before more specific patterns (sk-/Bearer) shrink it. The KEY
    # portion must end in KEY/TOKEN/SECRET/PASSWORD to qualify, value
    # must be ≥ 12 non-space chars.
    (
        re.compile(
            r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*)\S{12,}",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
    # Authorization: Bearer XXX (case-insensitive header form)
    (
        re.compile(
            r"(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._\-]{20,}",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
    # Bearer XXX (anywhere)
    (
        re.compile(r"\b(Bearer\s+)[A-Za-z0-9._\-]{20,}"),
        r"\1***",
    ),
    # JWT-shaped: 3 base64 segments each ≥ 16 chars (calibrated to
    # exclude version.module.commit identifiers and similar)
    (
        re.compile(
            r"\b[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"
        ),
        "***JWT***",
    ),
    # sk-... API key (Anthropic/OpenAI/Doubao convention) — 20 chars
    # after the literal "sk-" prefix. Real keys are well over 20 chars;
    # this threshold defeats short random IDs that happen to start "sk-".
    (
        re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
        "sk-***",
    ),
    # token=xxx in query strings (URL form, distinct from KEY=value above)
    (
        re.compile(r"\b(token=)[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
        r"\1***",
    ),
]


def redact(value: Any) -> Any:
    """Mask secret-shaped tokens in ``value`` if it is a string.

    Non-string types pass through unchanged. Empty strings pass through.
    Always returns the same type as the input.
    """
    if not isinstance(value, str):
        return value
    if not value:
        return value
    out = value
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _redact_extra_inplace(extra: dict[str, Any]) -> None:
    """Walk one level of the extra dict and redact string values.

    Nested dicts are NOT recursed (documented contract — keep this
    cheap; deep-walk would add cost on every log call). If a nested
    structure is needed, call redact() at the bind site.
    """
    for key, val in extra.items():
        if isinstance(val, str):
            extra[key] = redact(val)


def make_loguru_patcher() -> Callable[[dict[str, Any]], None]:
    """Return a loguru patcher that mutates the formatted message and
    extra dict values in-place, masking secret-shaped tokens.

    Install once at startup:

        logger.configure(patcher=make_loguru_patcher())

    The patcher runs at record creation time (BEFORE sinks fire), so
    every sink — file rotation, NAS log forwarder, db_log_sink, console
    — sees the redacted form.
    """

    def patcher(record: dict[str, Any]) -> None:
        msg = record.get("message")
        if isinstance(msg, str):
            record["message"] = redact(msg)
        extra = record.get("extra")
        if isinstance(extra, dict):
            _redact_extra_inplace(extra)

    return patcher
