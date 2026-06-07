from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.tags_repository import TagsRepository


@pytest.mark.asyncio
async def test_merge_tags_calls_rpc_and_returns_count():
    repo = TagsRepository()

    rpc_exec = AsyncMock(return_value=MagicMock(data=12))
    rpc = MagicMock(return_value=MagicMock(execute=rpc_exec))
    client = MagicMock(rpc=rpc)
    repo._get_client = AsyncMock(return_value=client)

    count = await repo.merge_tags("100", ["200", "300"], "user-uuid")

    assert count == 12
    rpc.assert_called_once_with(
        "merge_tags",
        {"p_target": "100", "p_sources": ["200", "300"], "p_user": "user-uuid"},
    )


@pytest.mark.asyncio
async def test_merge_tags_handles_list_scalar_payload():
    repo = TagsRepository()
    rpc_exec = AsyncMock(return_value=MagicMock(data=[7]))
    client = MagicMock(rpc=MagicMock(return_value=MagicMock(execute=rpc_exec)))
    repo._get_client = AsyncMock(return_value=client)

    assert await repo.merge_tags("1", ["2"], "u") == 7
