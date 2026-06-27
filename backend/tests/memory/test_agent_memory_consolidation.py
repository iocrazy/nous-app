"""Agent memory consolidation (Phase B) — write path + dedup + parse."""

from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.agent_memory_repository import (
    existing_fingerprints,
    write_memory_row,
)


@pytest.mark.asyncio
async def test_write_memory_row_inserts_private_and_returns_true():
    captured = {}

    class _Session:
        async def execute(self, stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.agent_memory_repository.write_scope", return_value=_Scope()
    ):
        ok = await write_memory_row(
            owner_user_id="u1",
            agent_id="a1",
            scope="agent_user",
            kind="fact",
            title="deploy",
            body_md="run x",
            when_to_use="when deploying",
            fingerprint="fp1",
        )
    assert ok is True
    assert captured["params"]["owner_user_id"] == "u1"
    assert captured["params"]["fingerprint"] == "fp1"
    # always private — never writes a shared row in Phase B
    assert (
        "'private'" in captured["sql"]
        or captured["params"].get("visibility") == "private"
    )


@pytest.mark.asyncio
async def test_write_memory_row_swallows_errors():
    with patch(
        "app.repositories.agent_memory_repository.write_scope",
        side_effect=RuntimeError("db down"),
    ):
        assert (
            await write_memory_row(
                owner_user_id="u1",
                agent_id="a1",
                scope="agent_user",
                kind="fact",
                title="t",
                body_md="b",
                when_to_use="w",
                fingerprint="fp",
            )
            is False
        )


@pytest.mark.asyncio
async def test_existing_fingerprints_returns_set():
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return ["fp1", "fp2"]

    class _Session:
        async def execute(self, stmt, params=None):
            return _Result()

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.agent_memory_repository.write_scope", return_value=_Scope()
    ):
        fps = await existing_fingerprints(owner_user_id="u1", agent_id="a1")
    assert fps == {"fp1", "fp2"}
