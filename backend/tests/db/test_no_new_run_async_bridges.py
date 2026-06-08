"""Ratchet guard: no NEW ``run_async(...)`` DB bridges (ORM 2.0 plan §2.4b).

The migration's Q3 goal is "fully async — no sync DB path". ``run_async`` (in
``app/tasks/utils.py``) runs a coroutine from a sync context via a *fresh event
loop per call* — a pattern that has caused a prod connection-leak incident
(2026-05-22, see the ``run_async`` docstring) and is fundamentally incompatible
with the SQLAlchemy ORM (asyncpg connections are event-loop-bound). The plan
calls for converting every ``run_async(repo.X())`` site to async-native and
adding a CI guard so no new ones creep in.

This is a **ratchet**: ``_ALLOWLIST`` enumerates the files that STILL contain a
``run_async`` usage (each a documented sync-library boundary or a deferred
conversion). Any ``run_async(`` outside the allowlist fails the test. As a file
is converted to async-native, REMOVE it from the allowlist — that locks the
conversion in (a regression that re-introduces ``run_async`` there will fail).

Converted so far:
  * ``app/workflows/scheduled_cleanup.py`` — 3 housekeeping steps → async
    (OFF the allowlist; no run_async left).
  * ``app/workflows/storyboard.py`` — the 2 DB persist steps → async (still ON
    the allowlist for its 6 remaining AI/export-service bridges).
  * ``app/services/ai/providers/ai_provider_helpers.py`` — the 2 repo reads
    (user_settings + analyze agent) → async; sole caller
    ``analyze_l1.resolve_analyze_provider`` is now an async @DBOS.step awaited
    from the async workflow (OFF the allowlist; no run_async left).
  * ``app/services/media/transcode/transcode_service.py`` — the single
    ``_get_db_setting`` system_settings read → async; ``_select_tiers`` + the
    4 call sites (all inside the async ``transcode_version``) await it (OFF
    the allowlist; no run_async left).
"""

from __future__ import annotations

import re
from pathlib import Path

# Files permitted to still contain ``run_async``/``_run_async``. Shrink this set
# as conversions land — never add to it. Each entry is a genuine sync→async
# boundary (sync 3rd-party lib: yt-dlp / ffmpeg / parser subprocess) or a
# deferred conversion tracked in the ORM rollout follow-up.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Download path: run_async is entangled with yt-dlp / ffmpeg / httpx
        # subprocess dispatch inside sync @DBOS.step bodies (genuine sync-lib
        # boundary). DB calls here await a follow-up async-hoist pass.
        "app/tasks/download_helpers.py",
        "app/tasks/download_strategies.py",
        # Media parse path: parser subprocess chain inside sync @DBOS.step.
        "app/services/media/parsers/parse_helpers.py",
        # Storyboard workflow: the 2 DB persist steps are now async-native; the
        # 6 remaining run_async sites wrap AI / export SERVICES (not repos) — a
        # service-bridge, not a §2.4b DB bridge. Deferred.
        "app/workflows/storyboard.py",
    }
)

# Matches a CALL ``run_async(`` or ``_run_async(`` but NOT a ``def run_async(``
# / ``def _run_async(`` definition.
_CALL_RE = re.compile(r"(?<!def )\b_?run_async\(")
_DEF_RE = re.compile(r"\bdef _?run_async\(")

_APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _iter_app_py_files():
    for path in _APP_DIR.rglob("*.py"):
        yield path


def test_no_new_run_async_bridges() -> None:
    """Fail if any app file OUTSIDE the allowlist calls ``run_async``."""
    backend_root = _APP_DIR.parent
    offenders: list[str] = []

    for path in _iter_app_py_files():
        rel = path.relative_to(backend_root).as_posix()
        if rel in _ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _DEF_RE.search(line):
                continue
            if _CALL_RE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "New run_async() usage found outside the §2.4b allowlist. Convert the "
        "DB call to async-native (await the repo directly from an async "
        "@DBOS.step/workflow) instead of bridging through run_async:\n  "
        + "\n  ".join(offenders)
    )


def test_allowlist_entries_still_exist_and_use_run_async() -> None:
    """Keep the allowlist honest: every entry must exist AND still contain a
    ``run_async`` call. When an entry no longer needs it, REMOVE the entry
    (that tightens the ratchet) rather than leaving a stale exemption."""
    backend_root = _APP_DIR.parent
    stale: list[str] = []

    for rel in _ALLOWLIST:
        path = backend_root / rel
        if not path.exists():
            stale.append(f"{rel} (file missing)")
            continue
        if not _CALL_RE.search(path.read_text(encoding="utf-8")):
            stale.append(f"{rel} (no run_async call left — remove from allowlist)")

    assert not stale, "Stale §2.4b allowlist entries:\n  " + "\n  ".join(stale)
