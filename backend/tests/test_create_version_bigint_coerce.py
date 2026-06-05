"""Pin the snowflake-str → int8 coercion in
``ResourcesRepositoryOrm.create_version``.

Real-world breakage (prod log, 2026-05-29):

    [download.finalize] resource_version backfill: invalid input for query
    argument $1: '311118399798162' ('str' object cannot be interpreted as
    an integer)
    INSERT INTO resource_versions ...

asyncpg's int8 codec refuses to coerce a string. ``create_version`` runs
each BIGINT column through ``_bigint`` BEFORE binding. This test simulates
the download.finalize call site and asserts every BIGINT column lands as an
``int`` in the bound INSERT ``.values()``, while text columns pass through.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories import resources_repository_orm as orm_mod
from app.repositories.resources_repository_orm import ResourcesRepositoryOrm


def _patch_write_scope_capturing(captured: dict):
    """Return a fake write_scope() whose session.execute records the bound
    ``.values()`` of the INSERT statement it receives."""

    class _Mappings:
        def first(self):
            return {"id": 1}

    class _Result:
        def mappings(self):
            return _Mappings()

    async def _execute(stmt):
        # SQLAlchemy Insert exposes the bound values via compile().params.
        captured["values"] = dict(stmt.compile().params)
        return _Result()

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=_execute)

    @asynccontextmanager
    async def _fake_write_scope():
        yield fake_session

    return _fake_write_scope


@pytest.mark.asyncio
async def test_create_version_coerces_bigint_columns_from_str():
    repo = ResourcesRepositoryOrm()
    captured: dict = {}

    original = orm_mod.write_scope
    orm_mod.write_scope = _patch_write_scope_capturing(captured)  # type: ignore
    try:
        await repo.create_version(
            {
                "resource_id": "311118399798162",  # snowflake-as-str (the bug)
                "version_number": 1,
                "filename": "video.mp4",
                "file_path": "teams/x/uploads/311118399798162/v1/video.mp4",
                "file_size_bytes": "10485760",  # also coerced
                "mime_type": "video/mp4",
                "uploaded_by": "8e1584e3-9c29-4a5b-90fe-125b74259f7f",
            }
        )
    finally:
        orm_mod.write_scope = original  # type: ignore[assignment]

    values = captured["values"]
    assert values["resource_id"] == 311118399798162
    assert isinstance(values["resource_id"], int)
    assert values["file_size_bytes"] == 10485760
    assert isinstance(values["file_size_bytes"], int)
    # Text columns pass through unchanged.
    assert values["mime_type"] == "video/mp4"
    # UUID stays str (not a BIGINT column).
    assert values["uploaded_by"] == "8e1584e3-9c29-4a5b-90fe-125b74259f7f"


@pytest.mark.asyncio
async def test_create_version_leaves_int_inputs_untouched():
    """A caller that already passes ints (e.g. resources_service) shouldn't
    trip the coerce path."""
    repo = ResourcesRepositoryOrm()
    captured: dict = {}

    original = orm_mod.write_scope
    orm_mod.write_scope = _patch_write_scope_capturing(captured)  # type: ignore
    try:
        await repo.create_version(
            {
                "resource_id": 311118399798162,  # already int
                "version_number": 1,
                "file_size_bytes": 10485760,
            }
        )
    finally:
        orm_mod.write_scope = original  # type: ignore[assignment]

    values = captured["values"]
    assert isinstance(values["resource_id"], int)
    assert isinstance(values["file_size_bytes"], int)
    assert isinstance(values["version_number"], int)
