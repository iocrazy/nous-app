"""Regression sweep — no loguru caller may use stdlib %-format.

Loguru does NOT interpolate ``%s`` / ``%d`` / ``%r`` / ``%f`` from
positional args — those are silently dropped, so a call like
``logger.error("oops %s", e)`` renders literal ``%s`` in production.
That bit issue #194 Bug D (PR #191's APIError logging never actually
surfaced) and motivated the 92-site sweep PR fixing the rest of the
codebase.

This test scans every file that imports ``loguru`` and fails if any
``logger.{level}("...%X...")`` pattern returns. The fix recipe is
always the same: convert to f-string.

Source-level grep is the right tool. AST analysis would catch subtler
cases (concat / multi-line) but loguru's silent-drop is so quiet that
even one literal ``%s`` in the wild is a regression worth flagging.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"

# Match logger.LEVEL("...%X..." (start of a %-formatted call). We scope
# to single-line opens because loguru calls are almost never split
# across lines for the format-string portion specifically.
_PAT = re.compile(
    r'logger\.(?:error|warning|info|debug|exception|success)\(\s*'
    r'"[^"]*%[srdfx]'
)

# Files that legitimately use %-formatting for non-loguru reasons
# (e.g. format strings stored as constants, never passed to logger).
# Add new exemptions with a clear justification.
EXEMPT_RELATIVE_PATHS: set[str] = set()


def test_no_loguru_percent_format_calls_in_backend():
    """Scan every backend/app/**/*.py that imports loguru. Fail with a
    file:line list if any ``logger.X("...%X..."`` call survives."""
    offenders: list[tuple[str, int, str]] = []

    for py_file in BACKEND_APP.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_APP).as_posix()
        if rel in EXEMPT_RELATIVE_PATHS:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "from loguru import" not in content:
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if _PAT.search(line):
                offenders.append((rel, line_no, line.strip()))

    assert not offenders, (
        "loguru %-format calls re-introduced — these will silently "
        "render literal '%s' in production logs. Convert to f-strings:\n"
        + "\n".join(
            f"  {p}:{n}  {l[:120]}" for p, n, l in offenders[:20]
        )
        + (f"\n  ... and {len(offenders) - 20} more" if len(offenders) > 20 else "")
    )
