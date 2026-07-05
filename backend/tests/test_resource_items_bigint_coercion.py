"""Regression: create_resource_item must coerce str snowflake ids to int.

2026-07-05 prod 500: the AI-chat/resources upload path passed scope_id /
resource_id / folder_id as strings; asyncpg's strict int8 codec rejects str
even with a ::BIGINT cast. Coercion lives at the repo choke point so all nine
call sites are covered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.resources_repository import ResourcesRepository


@pytest.mark.asyncio
async def test_create_resource_item_coerces_str_ids(monkeypatch):
    captured: dict = {}

    class _FakeResult:
        def mappings(self):
            m = MagicMock()
            m.first.return_value = None
            return m

    class _FakeSession:
        async def execute(self, stmt):
            # insert().values() stores params on the statement
            captured.update(
                {c.name: v for c, v in stmt._values.items()}  # type: ignore[attr-defined]
            )
            return _FakeResult()

    class _Scope:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.resources_repository.write_scope", return_value=_Scope()
    ):
        repo = ResourcesRepository()
        await repo.create_resource_item(
            {
                "scope_id": "310812366953241",
                "resource_id": "324506377071507",
                "folder_id": "310152630363703",
                "library_id": None,
                "added_by": "8e1584e3-9c29-4a5b-90fe-125b74259f7f",
            }
        )

    v = {k: getattr(val, "value", val) for k, val in captured.items()}
    assert v["scope_id"] == 310812366953241 and isinstance(v["scope_id"], int)
    assert v["resource_id"] == 324506377071507 and isinstance(v["resource_id"], int)
    assert v["folder_id"] == 310152630363703 and isinstance(v["folder_id"], int)
    # UUID str passes through untouched; None stays None.
    assert v["added_by"] == "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
    assert v.get("library_id") is None
