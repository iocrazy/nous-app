"""Regression test: ``soft_delete_resource`` must send an ISO timestamp.

PostgREST ``.update({...})`` sends values as JSON — it does NOT evaluate
SQL expressions. Passing the literal string ``"NOW()"`` to a timestamptz
column raises a PostgREST type error and the sweep silently fails.
"""

from datetime import datetime as _dt
from unittest.mock import AsyncMock

import pytest

from app.repositories.resources_repository import ResourcesRepository


@pytest.mark.asyncio
async def test_soft_delete_resource_sends_iso_timestamp(monkeypatch):
    """trashed_at must be an ISO-format string (not the literal 'NOW()')."""
    captured: dict = {}

    fake_table = type("T", (), {})()
    fake_table.update = lambda data: (captured.update({"data": data}) or fake_table)
    fake_table.eq = lambda *a, **kw: fake_table
    fake_table.execute = AsyncMock()

    fake_client = type("C", (), {})()
    fake_client.table = lambda name: fake_table

    monkeypatch.setattr(
        ResourcesRepository, "_get_client", AsyncMock(return_value=fake_client)
    )

    repo = ResourcesRepository()
    await repo.soft_delete_resource("r-1")

    payload = captured["data"]
    assert payload["is_trashed"] is True
    # Must be a parseable ISO timestamp, not the literal 'NOW()'.
    assert payload["trashed_at"] != "NOW()"
    parsed = _dt.fromisoformat(payload["trashed_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
