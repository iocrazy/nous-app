"""Unit tests for ``system_session``'s cross-user audit trail (app/db/scope.py).

``system_session(reason)`` is the explicit "cross-user / system access — no
tenant injection" entry point. Every loguru sink in this project is registered
at ``level="INFO"`` (see ``app/core/utils.py``), so the audit line MUST be
emitted at INFO or above — a DEBUG line is silently dropped in dev/staging/prod,
leaving cross-user access with zero runtime audit trail.

These tests capture loguru output via a temporary in-memory sink and assert:
  * the audit record is emitted at INFO level (not DEBUG),
  * the mandatory ``reason`` string is present,
  * caller context (this test's module/function) is present so the audit trail
    identifies WHERE the cross-user access originated.

No DB needed — ``system_session`` logs BEFORE it touches the sessionmaker, so we
never enter the ``async with get_sessionmaker()`` body. The context-manager
``__aenter__`` is awaited just far enough to run the log line, then the generator
is closed.

Run: ``uv run pytest tests/db/test_scope_audit.py -v``
"""

from __future__ import annotations

import pytest
from loguru import logger

from app.db.scope import system_session


@pytest.fixture
def captured_records():
    """A loguru sink registered at INFO that collects every record it receives.

    Mirrors the project's real sinks (all ``level="INFO"``): a DEBUG line will
    NOT reach this sink, so an assertion on it proves the audit line is emitted
    at INFO or above.
    """
    records: list = []
    sink_id = logger.add(records.append, level="INFO", format="{message}")
    try:
        yield records
    finally:
        logger.remove(sink_id)


async def _open_and_close_system_session(reason: str) -> None:
    """Drive ``system_session`` far enough to fire its audit log, then close.

    Uses a REAL ``async with`` (the production entry path, which goes through
    contextlib's ``__aenter__``) so the caller-context assertion covers the actual
    frame walk. The audit line runs at the TOP of the context manager, before any
    DB work. With no DSN configured, ``get_sessionmaker()`` raises ``RuntimeError``
    right after — which is fine: the log has already fired. We swallow that so the
    test needs no live DB, and assert on the captured record instead.
    """
    try:
        async with system_session(reason):
            pass  # pragma: no cover - unreachable without a DSN
    except RuntimeError:
        # Expected when no SUPAVISOR_DATABASE_URL is set — the audit line fired
        # before the sessionmaker was touched. That is exactly what we assert on.
        pass


@pytest.mark.asyncio
async def test_system_session_audit_emitted_at_info(captured_records) -> None:
    """The audit record lands at INFO (not DEBUG) with the reason present."""
    await _open_and_close_system_session("sweeper: reap orphans")

    matching = [r for r in captured_records if "system_session" in r.record["message"]]
    assert matching, "no system_session audit record reached the INFO sink"
    rec = matching[0].record
    assert rec["level"].name == "INFO", f"expected INFO, got {rec['level'].name}"
    assert "sweeper: reap orphans" in rec["message"]


@pytest.mark.asyncio
async def test_system_session_audit_includes_caller_context(captured_records) -> None:
    """The audit line identifies the immediate caller (module:function:line)."""
    await _open_and_close_system_session("admin analytics export")

    matching = [r for r in captured_records if "system_session" in r.record["message"]]
    assert matching, "no system_session audit record reached the INFO sink"
    message = matching[0].record["message"]
    # Caller context = THIS module's helper, not scope.py / contextlib internals.
    assert (
        "test_scope_audit" in message
    ), f"caller context missing from audit line: {message!r}"
    assert (
        "_open_and_close_system_session" in message
    ), f"caller function missing from audit line: {message!r}"
