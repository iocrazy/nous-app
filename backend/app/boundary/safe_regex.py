"""Safe regex — defend against ReDoS (regular-expression DoS).

Sprint 7 (P2 safety bundle). Python's ``re`` module is a backtracking
engine vulnerable to catastrophic backtracking on adversarial inputs
(``(a+)+$`` style). When users supply patterns (search filters, log
parsers, custom skill matchers) the worst case is a single request
that pegs a CPU core for minutes.

This module gives static + runtime defenses:

  - ``vet_pattern(p)``  — static lint: rejects patterns over a length
    cap or with obviously dangerous shapes (nested unbounded quantifiers).
  - ``compile_safe(p)`` — vet then compile.
  - ``search_safe(p, text, timeout_s=...)`` — compile + run with a wall
    clock timeout, raising RegexComplexityError on overrun.

The runtime timeout uses signal-free monitoring (a watchdog thread that
sets a flag the regex engine doesn't actually check), so it can only
abort BETWEEN calls — i.e. it bounds total wall time of repeated
``finditer`` iterations or "many quick searches", not a single
catastrophic match. To bound a single match we compile against the
``regex`` package if available (which natively supports timeout) and
fall back to ``re`` with a static-only check otherwise.

The ``regex`` package is optional — missing it does NOT degrade
correctness, it just downgrades runtime protection. The static lint
catches the high-frequency patterns either way.
"""

from __future__ import annotations

import re
from typing import Optional, Pattern

from app.boundary.errors import RegexComplexityError

DEFAULT_MAX_PATTERN_LEN = 1000  # chars in the regex itself
DEFAULT_RUNTIME_TIMEOUT_S = 1.0

# Heuristic for "obviously dangerous" patterns: nested unbounded
# quantifiers (a typical catastrophic-backtracking shape).
# Catches: (a+)+, (.*)*, (\d+)+ — group containing a +/* quantifier
# whose group itself is then quantified.
# Does NOT catch (false negatives — undecidable in pure static lint):
#   - (a|aa)+ on adversarial inputs (alternation overlap)
#   - (.+)\1 (backreference explosions)
# Runtime timeout in search_safe is the second line of defense.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)[+*]")

# Try to use the third-party `regex` package which supports a real
# per-call timeout. Falls back to stdlib `re` if not installed.
try:
    import regex as _regex_lib  # type: ignore[import-not-found]

    _HAS_REGEX_PKG = True
except ImportError:
    _regex_lib = None  # type: ignore[assignment]
    _HAS_REGEX_PKG = False


def vet_pattern(pattern: str, *, max_len: int = DEFAULT_MAX_PATTERN_LEN) -> None:
    """Static lint. Raises ``RegexComplexityError`` if the pattern is
    over the length cap or matches a known-dangerous shape.

    Does NOT compile — caller can use the same pattern with their own
    flags afterward."""
    if not isinstance(pattern, str):
        raise RegexComplexityError("regex pattern must be a string")
    if len(pattern) > max_len:
        raise RegexComplexityError(
            f"regex pattern exceeds length cap ({len(pattern)} > {max_len})"
        )
    if _NESTED_QUANTIFIER.search(pattern):
        raise RegexComplexityError(
            "regex pattern contains nested unbounded quantifier "
            "(catastrophic-backtracking risk)"
        )


def compile_safe(
    pattern: str,
    *,
    flags: int = 0,
    max_len: int = DEFAULT_MAX_PATTERN_LEN,
) -> Pattern[str]:
    """Vet then compile. Returns a stdlib ``re.Pattern`` regardless of
    whether the optional ``regex`` package is available — callers that
    want runtime timeouts use ``search_safe`` instead."""
    vet_pattern(pattern, max_len=max_len)
    try:
        return re.compile(pattern, flags)
    except re.error as e:
        # Don't echo the bad pattern in the error — it might contain
        # secrets the caller put in by mistake. Just say "invalid".
        raise RegexComplexityError(f"invalid regex syntax: {e}") from None


def search_safe(
    pattern: str,
    text: str,
    *,
    flags: int = 0,
    timeout_s: float = DEFAULT_RUNTIME_TIMEOUT_S,
    max_len: int = DEFAULT_MAX_PATTERN_LEN,
) -> Optional[re.Match[str]]:
    """Vet, compile, and run a single search with a wall-clock timeout.

    Returns the match object or None.

    If the optional ``regex`` package is installed, uses its native
    per-call timeout (real abort mid-match). Otherwise relies on the
    static lint only — a malicious pattern that escapes the lint will
    still hang up to the OS / framework limit.
    """
    vet_pattern(pattern, max_len=max_len)
    if _HAS_REGEX_PKG:
        try:
            return _regex_lib.search(  # type: ignore[no-any-return]
                pattern, text, flags=flags, timeout=timeout_s
            )
        except _regex_lib.error as e:  # type: ignore[union-attr]
            raise RegexComplexityError(f"invalid regex syntax: {e}") from None
        except TimeoutError as e:
            raise RegexComplexityError(
                f"regex match exceeded {timeout_s}s timeout"
            ) from e
    # Fallback: stdlib re. No real per-call timeout available.
    return compile_safe(pattern, flags=flags, max_len=max_len).search(text)


__all__ = [
    "DEFAULT_MAX_PATTERN_LEN",
    "DEFAULT_RUNTIME_TIMEOUT_S",
    "compile_safe",
    "search_safe",
    "vet_pattern",
]
