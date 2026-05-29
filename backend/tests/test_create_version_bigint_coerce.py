"""Pin the snowflake-str → int8 coercion in
``ResourcesRepositoryAsyncpg.create_version``.

Real-world breakage (prod log, 2026-05-29):

    [download.finalize] resource_version backfill: invalid input for query
    argument $1: '311118399798162' ('str' object cannot be interpreted as
    an integer)
    INSERT INTO resource_versions ...

The repo previously forwarded ``data.values()`` straight to asyncpg, and
asyncpg's int8 codec refuses to coerce a string. This test simulates the
download.finalize call site and asserts every BIGINT column lands as an
``int`` in the bind tuple.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories.resources_repository_asyncpg import (
    ResourcesRepositoryAsyncpg,
)


@pytest.mark.asyncio
async def test_create_version_coerces_bigint_columns_from_str():
    repo = ResourcesRepositoryAsyncpg()
    repo.fetch_one = AsyncMock(return_value={"id": 1, "resource_id": 311118399798162})

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

    assert repo.fetch_one.await_count == 1
    sql, *bind = repo.fetch_one.await_args.args
    assert "INSERT INTO" in sql
    bind_by_col = dict(
        zip(
            (
                "resource_id",
                "version_number",
                "filename",
                "file_path",
                "file_size_bytes",
                "mime_type",
                "uploaded_by",
            ),
            bind,
        )
    )
    assert bind_by_col["resource_id"] == 311118399798162
    assert isinstance(bind_by_col["resource_id"], int)
    assert bind_by_col["file_size_bytes"] == 10485760
    assert isinstance(bind_by_col["file_size_bytes"], int)
    # Text columns pass through unchanged
    assert bind_by_col["mime_type"] == "video/mp4"
    assert (
        bind_by_col["uploaded_by"] == "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
    )  # UUID stays str


@pytest.mark.asyncio
async def test_create_version_leaves_int_inputs_untouched():
    """A caller that already passes ints (e.g. resources_service) shouldn't
    trip the coerce path."""
    repo = ResourcesRepositoryAsyncpg()
    repo.fetch_one = AsyncMock(return_value={"id": 1})

    await repo.create_version(
        {
            "resource_id": 311118399798162,  # already int
            "version_number": 1,
            "file_size_bytes": 10485760,
        }
    )

    bind = repo.fetch_one.await_args.args[1:]
    assert all(isinstance(v, int) for v in bind)
