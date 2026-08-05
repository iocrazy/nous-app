"""Regression: TranscodeService._get_db_setting must coerce jsonb values to str.

system_settings.value is jsonb. A setting saved as JSON `true` (e.g.
transcode_enabled) deserializes to a Python bool, but every consumer treats
the result as a string (`.lower()`, `.split(",")`, `int(...)`). Before the fix,
`db_enabled.lower()` raised "'bool' object has no attribute 'lower'" and EVERY
gated transcode failed at the enabled-check.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services.media.transcode.transcode_service import TranscodeService


class _FakeScopeSession:
    """Stand-in for the ORM AsyncSession — only ``scalar()`` is used by
    ``_get_db_setting`` (Phase B2 Task 2 ORM rewrite)."""

    def __init__(self, scalar_value):
        self._scalar_value = scalar_value

    async def scalar(self, stmt):
        return self._scalar_value


def _fake_read_scope(scalar_value):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(scalar_value)

    return _read_scope


@pytest.mark.asyncio
async def test_coerces_jsonb_bool_to_str(monkeypatch):
    from app.db import engine as db_engine
    from app.db import session as db_session

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope(True))

    result = await TranscodeService._get_db_setting("transcode_enabled")
    assert result == "True"
    # The enabled-check at transcode_service.py:181 now works.
    assert result.lower() in ("true", "1", "yes")


@pytest.mark.asyncio
async def test_coerces_jsonb_number_to_str(monkeypatch):
    from app.db import engine as db_engine
    from app.db import session as db_session

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope(100))

    result = await TranscodeService._get_db_setting("transcode_min_size_mb")
    assert result == "100"
    assert int(result) == 100  # downstream int parse still works


@pytest.mark.asyncio
async def test_passes_str_through_and_none(monkeypatch):
    from app.db import engine as db_engine
    from app.db import session as db_session

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope("480p,720p,1080p"))
    assert (
        await TranscodeService._get_db_setting("transcode_tiers") == "480p,720p,1080p"
    )

    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope(None))
    assert await TranscodeService._get_db_setting("missing") is None
