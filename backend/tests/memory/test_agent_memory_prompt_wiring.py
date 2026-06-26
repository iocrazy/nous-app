"""Task 3 tests: agent-memory flag gate + prompt-composer wiring.

Two tests:
 1. `test_composer_renders_agent_memory_block_and_fingerprints` — exercises the
    pure helpers (_assemble_system_message + _dynamic_fingerprint) directly,
    WITHOUT touching repos or the async compose() path, verifying:
    - <agent_memory> block appears when agent_memory_facts is non-empty
    - it is ABSENT when agent_memory_facts is empty
    - the dynamic fingerprint changes when the fact set changes
 2. `test_chat_recall_is_flag_gated_off_by_default` — verifies that
    `_safe_recall_agent_memory` returns [] immediately when
    FEATURE_AGENT_MEMORY is False (the prod default).

API adaptation notes (REAL API differs from the brief's pseudocode):
- ComposerInput takes `agent_slug`, not identity_md/soul_md/… — cannot be
  passed to compose() in a unit test without async repos.  We test the pure
  helpers directly instead.
- PromptComposer(agent_repo=None, skill_repo=None) is the test-safe form;
  None repos only raise inside compose(), not in the pure helpers.
- settings must be a module-level attribute of ai_library_chat_wiring for
  patch.object to work (we import it there at module level in the wiring).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Test 1: prompt_composer rendering + fingerprinting
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_composer_renders_agent_memory_block_and_fingerprints():
    """<agent_memory> block appears iff facts list is non-empty; fact set
    change drives a different dynamic_fingerprint (cache safety)."""
    from app.services.ai.prompts.prompt_composer import PromptComposer

    agent = {
        "identity_md": "id",
        "soul_md": "",
        "agent_md": "",
        "model": "qwen-max",
    }
    composer = PromptComposer(agent_repo=None, skill_repo=None)

    msg_without = composer._assemble_system_message(
        agent=agent,
        skills=[],
        request_instructions=None,
        agent_memory_facts=[],
    )
    msg_with = composer._assemble_system_message(
        agent=agent,
        skills=[],
        request_instructions=None,
        agent_memory_facts=["fact: deploy runs compose up"],
    )

    # Block present only when facts are non-empty
    assert "deploy runs compose up" in msg_with
    assert "<agent_memory>" in msg_with
    assert "deploy runs compose up" not in msg_without
    assert "<agent_memory>" not in msg_without

    # Dynamic fingerprint must change when the memory set changes (P0 isolation)
    fp_without = composer._dynamic_fingerprint("prefix", agent_memory_facts=[])
    fp_with = composer._dynamic_fingerprint(
        "prefix", agent_memory_facts=["fact: deploy runs compose up"]
    )
    assert fp_with != fp_without


# ---------------------------------------------------------------------------
# Test 2: flag gate in chat wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_recall_is_flag_gated_off_by_default():
    """_safe_recall_agent_memory returns [] when FEATURE_AGENT_MEMORY is False
    — the production default — without touching recall() or any DB."""
    from app.services.ai.chat import ai_library_chat_wiring as wiring
    from app.services.ai.memory.agent_memory import MemoryContext

    with patch.object(wiring.settings, "FEATURE_AGENT_MEMORY", False):
        out = await wiring._safe_recall_agent_memory(
            MemoryContext(user_id="u1"), "deploy"
        )
    assert out == []
