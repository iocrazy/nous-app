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


# ---------------------------------------------------------------------------
# Task 2 — consolidation service (pure, injected LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_parses_dedups_caps():
    from app.services.ai.memory.agent_memory_consolidation import (
        MemoryDraft,
        consolidate_pair,
        make_fingerprint,
    )

    # The LLM returns two drafts (one already stored → must be deduped).
    dup = MemoryDraft(title="Deploy", body_md="x", when_to_use="w", kind="fact")
    dup_fp = make_fingerprint("u1", "a1", dup)

    async def fake_consolidator(prompt: str) -> str:
        return (
            '[{"title":"Deploy","body_md":"x","when_to_use":"w","kind":"fact"},'
            '{"title":"Prefers brevity","body_md":"y","when_to_use":"always","kind":"preference"}]'
        )

    out = await consolidate_pair(
        owner_user_id="u1",
        agent_id="a1",
        recent_activity="...",
        existing_titles=["Deploy"],
        existing_fingerprints={dup_fp},
        consolidator=fake_consolidator,
        max_entries=10,
    )
    titles = [d.title for d, _ in out]
    assert titles == ["Prefers brevity"]  # dup dropped


def test_parse_tolerates_fenced_json():
    from app.services.ai.memory.agent_memory_consolidation import (
        parse_consolidation_output,
    )

    drafts = parse_consolidation_output(
        '```json\n[{"title":"T","body_md":"B","when_to_use":"W","kind":"fact"}]\n```'
    )
    assert len(drafts) == 1 and drafts[0].title == "T"


def test_parse_returns_empty_on_garbage():
    from app.services.ai.memory.agent_memory_consolidation import (
        parse_consolidation_output,
    )

    assert parse_consolidation_output("not json at all") == []


def test_make_fingerprint_stable_and_title_normalized():
    from app.services.ai.memory.agent_memory_consolidation import (
        MemoryDraft,
        make_fingerprint,
    )

    a = MemoryDraft(title="Deploy", body_md="x", when_to_use="w", kind="fact")
    b = MemoryDraft(
        title=" deploy ", body_md="DIFFERENT", when_to_use="z", kind="decision"
    )
    # fingerprint keys off normalized title only → same key (dedup by topic)
    assert make_fingerprint("u1", "a1", a) == make_fingerprint("u1", "a1", b)
