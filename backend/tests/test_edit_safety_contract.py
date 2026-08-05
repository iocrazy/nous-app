"""Adversarial tests for A5 — the edit-safety contract (agent-layer spec
§5.2 / §5.3).

The scenario every test here is really about: a writer selects a passage,
asks the agent to rework it, and keeps typing while the agent thinks. Two
things must not happen — the agent's write silently landing on top of what
they typed, and the agent having no stable handle on what was selected.

Structured to match where each guarantee actually lives, because they fail
independently:

  1. **The precondition** (``scoped_script_gateway.apply_element_edit``) —
     tested against a REAL op ledger replayed by the REAL ``replay_to``, so
     the "did this element move?" answer comes from the same code the
     version service uses, not from a mock that agrees with the test.
  2. **The selection attachment** (``schemas/script_selection`` +
     ``scope/script_selection``) — shape validation, and element ids that
     are foreign / invented / stale being rejected AND audited.
  3. **The A1 gate** — a propose-graded agent reaching ProposeEdit but not
     ApplyEdit, and an ungraded one reaching neither. This is the layer that
     runs before any handler is entered, so it is driven directly.

The whole-scene-vs-element-level decision is asserted DELIBERATELY here
(``test_a_third_party_edit_to_a_DIFFERENT_element_rebases_and_applies``),
not left to fall out of whatever the implementation happens to do — the plan
made that call the implementer's, so the test states which way it went and
what it costs.

Unit suite, no DSN: fake session / fake repository, same style as
``test_screenwriting_tools.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

import app.services.ai.scope.scene_observations as observations_mod
import app.services.ai.scope.scoped_script_gateway as gateway_mod
import app.services.ai.scope.script_selection as selection_mod
import app.services.ai.tools.screenwriting_tools as tools_mod
from app.repositories.script_scene_repository import VersionConflict
from app.schemas.script_selection import (
    MAX_SELECTION_ELEMENTS,
    ParsedSelection,
    ScriptSelectionAttachment,
    SelectionRejected,
    parse_selection,
)
from app.services.ai.scope.agent_run_scope import AgentRunScope
from app.services.ai.scope.scope_resolver import Denied, ResolvedScene
from app.services.ai.tools.screenwriting_tools import SCREENWRITING_HANDLERS
from app.services.infra.hooks import HookContext
from app.services.infra.hooks.high_risk_capability_gate import (
    HighRiskCapabilityGateHook,
)
from app.services.script.scene_ops import OpError

_RUN_ID = "800100000000000009"
_USER_ID = "22222222-2222-2222-2222-222222222222"
_AGENT_ID = "00000000-0000-0000-0000-000000000002"
_PROJECT_A = 900100000000000001
_TEAM_A = 900100000000000003
_SCRIPT_ID = 700100000000000004
_SCENE_ID = 700100000000000001

_RUN_CONTEXT = {
    "run_id": _RUN_ID,
    "user_id": _USER_ID,
    "team_id": _TEAM_A,
    "agent_id": _AGENT_ID,
}

# The passage under test: one action line and one line of dialogue. The agent
# is asked to rework el_2; the writer keeps typing in one or the other.
_EL_1 = {"id": "el_1", "type": "action", "text": "She waits by the window."}
_EL_2 = {
    "id": "el_2",
    "type": "dialogue",
    "text": "I told you I would come.",
    "character_id": "ch_9",
}


def _scope() -> AgentRunScope:
    return AgentRunScope(
        run_id=_RUN_ID,
        user_id=_USER_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
    )


def _resolved_scene(content=None, version=2) -> ResolvedScene:
    return ResolvedScene(
        id=_SCENE_ID,
        script_id=_SCRIPT_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
        heading_int_ext="INT",
        location_text="Kitchen",
        time_of_day="NIGHT",
        content_json=[dict(_EL_1), dict(_EL_2)] if content is None else content,
        content_version=version,
    )


# --------------------------------------------------------------------- #
# A real op ledger. Building the scene through actual insert ops means the
# "what did this element look like at version N?" answer comes from the same
# replay the version service uses — a hand-written expectation would let a
# replay bug pass.
# --------------------------------------------------------------------- #


def _ledger(*extra_rows) -> list[dict]:
    rows = [
        {
            "op_seq": 1,
            "op_json": {
                "ops": [
                    {
                        "op": "insert",
                        "element_id": "el_1",
                        "payload": {k: v for k, v in _EL_1.items() if k != "id"},
                    }
                ]
            },
        },
        {
            "op_seq": 2,
            "op_json": {
                "ops": [
                    {
                        "op": "insert",
                        "element_id": "el_2",
                        "payload": {k: v for k, v in _EL_2.items() if k != "id"},
                    }
                ]
            },
        },
    ]
    rows.extend(extra_rows)
    return rows


def _writer_typed_in(element_id: str, text: str, op_seq: int) -> dict:
    """One editor op: the writer edits an element's text. Exactly the shape
    ``apply_element_ops`` records (96% of real op rows touch one element —
    see the granularity note in scoped_script_gateway)."""
    return {
        "op_seq": op_seq,
        "op_json": {
            "ops": [
                {"op": "update", "element_id": element_id, "payload": {"text": text}}
            ]
        },
    }


class _FakeSceneRepo:
    """Records what the ops channel was asked to do, so a test can assert
    NOTHING was written rather than only that an error came back."""

    def __init__(self, ledger, *, raises=None, new_version=None):
        self.ledger = ledger
        self.raises = raises
        self.new_version = new_version
        self.calls: list[dict] = []

    async def list_ops_by_scene(self, scene_id):
        return self.ledger

    async def apply_element_ops(self, scene_id, ops, expected_version, actor):
        self.calls.append(
            {
                "scene_id": scene_id,
                "ops": ops,
                "expected_version": expected_version,
                "actor": actor,
            }
        )
        if self.raises is not None:
            raise self.raises
        return {
            "content_version": self.new_version or expected_version + 1,
            "elements": [],
        }


def _observe(elements, version, run_id=_RUN_ID, scene_id=_SCENE_ID):
    """Seed the server's record of what this run was SHOWN.

    Every write test goes through here rather than passing a version as an
    argument, because that is now the only thing the precondition trusts —
    a test that could set the base directly would not be testing the
    contract that shipped."""
    observations_mod.record_scene_read(run_id, scene_id, version, list(elements))


def _patched_gateway(repo, *, current_version, current_elements):
    """Patch the two IO seams: the fresh read at write time and the
    repository behind the ops channel."""
    return (
        patch.object(
            gateway_mod,
            "_current_scene_state",
            AsyncMock(return_value=(current_version, current_elements)),
        ),
        patch.object(gateway_mod, "get_script_scene_repository", lambda: repo),
    )


@pytest.fixture(autouse=True)
def _clean_observations():
    """No record leaks between tests — a stale one would silently satisfy a
    precondition the test meant to leave unmet."""
    observations_mod._reset_for_tests()
    yield
    observations_mod._reset_for_tests()


# ===================================================================== #
# 1. The precondition — the core of the contract.
# ===================================================================== #


@pytest.mark.asyncio
async def test_a_third_party_edit_to_the_SAME_element_refuses_and_writes_nothing():
    """THE failure this whole stage exists to prevent.

    The agent read el_2 at version 2 and spent its turn rewriting it. The
    writer edited el_2 in the meantime. The agent's rewrite is based on text
    that no longer exists, so it must be refused — and refused BEFORE the
    ops channel is touched, which is why the assertion is on
    ``repo.calls == []`` and not merely on the returned error."""
    repo = _FakeSceneRepo(_ledger(_writer_typed_in("el_2", "Actually, I lied.", 3)))
    current = [dict(_EL_1), {**_EL_2, "text": "Actually, I lied."}]
    _observe([_EL_1, _EL_2], 2)

    patches = _patched_gateway(repo, current_version=3, current_elements=current)
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "A rewrite of text the writer has already replaced."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditRefused)
    assert outcome.code == "version_conflict"
    assert outcome.conflicting_element_ids == ("el_2",)
    # NOTHING was written. The ops channel was never entered.
    assert repo.calls == []


@pytest.mark.asyncio
async def test_a_third_party_edit_to_a_DIFFERENT_element_rebases_and_applies():
    """THE GRANULARITY DECISION, asserted deliberately.

    The writer typed in el_1 while the agent reworked el_2. The whole-scene
    watermark says "conflict" (content_version moved 2 -> 3); the
    element-level precondition says "el_2 is untouched, land it".

    A5 implements the second, on measured evidence (agent turns p90 26s vs a
    1.4s median gap between editor ops, but 96% of op rows touch exactly one
    element — so during an active writing session whole-scene would refuse
    nearly every agent write while protecting keystrokes that were never at
    risk). The cost of that choice is stated here too: the write is
    submitted at the CURRENT version, meaning the agent's edit deliberately
    lands on top of content it never read — safe only because every element
    it actually rewrites was verified byte-identical first."""
    repo = _FakeSceneRepo(
        _ledger(_writer_typed_in("el_1", "She waits by the door instead.", 3)),
        new_version=4,
    )
    current = [{**_EL_1, "text": "She waits by the door instead."}, dict(_EL_2)]
    _observe([_EL_1, _EL_2], 2)

    patches = _patched_gateway(repo, current_version=3, current_elements=current)
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "You said that last time."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditApplied)
    assert outcome.content_version == 4
    # Rebased, and it says so — the tool turns this into a sentence telling
    # the writer their concurrent work was kept.
    assert outcome.rebased_from == 2
    # Submitted at the CURRENT version, not the stale one it read.
    assert repo.calls[0]["expected_version"] == 3


@pytest.mark.asyncio
async def test_an_untouched_scene_applies_at_the_version_the_agent_read():
    """No drift, no rebase: the precondition is the plain optimistic one and
    the write carries exactly the version the agent quoted."""
    repo = _FakeSceneRepo(_ledger(), new_version=3)
    current = [dict(_EL_1), dict(_EL_2)]
    _observe([_EL_1, _EL_2], 2)

    patches = _patched_gateway(repo, current_version=2, current_elements=current)
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "You said that last time."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditApplied)
    assert outcome.rebased_from is None
    assert repo.calls[0]["expected_version"] == 2


@pytest.mark.asyncio
async def test_a_targeted_element_the_writer_deleted_is_refused_as_gone():
    """Deleted is not the same as changed and must not read as it: a changed
    passage can be re-read and rebased, a deleted one cannot, and the
    message says which."""
    repo = _FakeSceneRepo(
        _ledger(
            {
                "op_seq": 3,
                "op_json": {"ops": [{"op": "delete", "element_id": "el_2"}]},
            }
        )
    )

    _observe([_EL_1, _EL_2], 2)
    patches = _patched_gateway(repo, current_version=3, current_elements=[dict(_EL_1)])
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "A rewrite of a line that is gone."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditRefused)
    assert "no longer exist" in outcome.message
    assert repo.calls == []


@pytest.mark.asyncio
async def test_a_conflict_raised_INSIDE_the_ops_channel_is_surfaced_not_swallowed():
    """The precondition narrows the race; it cannot close it. If the writer
    lands an op between our read and the UPDATE, ``apply_element_ops``
    raises the SAME ``VersionConflict`` the editor's own path raises — and
    that must come back as an explicit refusal rather than a retry loop
    (retrying against a 1.4s typing cadence is a livelock) or a swallowed
    error."""
    repo = _FakeSceneRepo(
        _ledger(),
        raises=VersionConflict(7, [dict(_EL_1), {**_EL_2, "text": "Racing edit."}]),
    )

    _observe([_EL_1, _EL_2], 2)
    patches = _patched_gateway(
        repo, current_version=2, current_elements=[dict(_EL_1), dict(_EL_2)]
    )
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "You said that last time."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditRefused)
    assert outcome.code == "version_conflict"
    assert outcome.current_version == 7
    # Exactly one attempt. No silent retry.
    assert len(repo.calls) == 1
    # The current text comes back so the model can rebase without another
    # round trip.
    assert outcome.current_elements[0]["text"] == "Racing edit."


@pytest.mark.asyncio
async def test_a_model_quoted_version_does_not_govern_anything():
    """A5 review, Critical — the demotion, asserted directly.

    The model quotes a version that matches NOTHING it was shown (and that
    happens to equal the scene's current version, which is exactly the value
    the old code treated as "no drift, skip the check"). The server's record
    still says the agent read v2 and that el_2 said something else, so the
    edit is refused. The number the model supplied changes nothing."""
    repo = _FakeSceneRepo(_ledger())
    current = [dict(_EL_1), {**_EL_2, "text": "Actually, I lied."}]
    _observe([_EL_1, _EL_2], 2)

    patches = _patched_gateway(repo, current_version=3, current_elements=current)
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "A rewrite of text the author has already replaced."},
            quoted_base_version=3,  # == current: the old bypass
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditRefused)
    assert outcome.code == "version_conflict"
    assert outcome.conflicting_element_ids == ("el_2",)
    assert repo.calls == []


@pytest.mark.asyncio
async def test_a_run_that_never_read_the_scene_cannot_write_to_it():
    """ "You cannot write what you never read" — enforced, not asserted.

    No observation means no basis for any precondition, so there is nothing
    to compare and nothing to trust. This is also the fail-closed answer when
    a record was evicted or the run spans processes: refuse, tell the model to
    read, and let it proceed on the next call."""
    repo = _FakeSceneRepo(_ledger())
    patches = _patched_gateway(
        repo, current_version=2, current_elements=[dict(_EL_1), dict(_EL_2)]
    )
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "..."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditRefused)
    assert outcome.code == "scene_not_read"
    assert "ReadScene" in outcome.message
    assert repo.calls == []


@pytest.mark.asyncio
async def test_a_refusal_is_not_a_retry_oracle():
    """The refusal hands back the author's current text so the panel can show
    it and the model can rebase. That must not be enough to then succeed: the
    server's record still holds the OLD text until ReadScene refreshes it, so
    a model that copies the returned text/version straight into a retry is
    refused again. "Re-read the scene" is literal."""
    repo = _FakeSceneRepo(_ledger())
    current = [dict(_EL_1), {**_EL_2, "text": "Actually, I lied."}]
    _observe([_EL_1, _EL_2], 2)
    patches = _patched_gateway(repo, current_version=3, current_elements=current)

    with patches[0], patches[1]:
        first = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "v1"},
            quoted_base_version=2,
            actor="agent:test",
        )
        # Retry quoting everything the refusal just disclosed.
        retry = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "v2"},
            quoted_base_version=first.current_version,
            actor="agent:test",
        )

    assert isinstance(first, gateway_mod.EditRefused)
    assert isinstance(retry, gateway_mod.EditRefused)
    assert repo.calls == []


@pytest.mark.asyncio
async def test_a_target_the_author_only_MOVED_is_treated_as_changed():
    """A5 review, M1. Comparing element dicts alone is position-blind, so a
    reordered target used to read as untouched. The comparison defers to
    ``version_service.diff_scenes`` — the same classifier the version history
    uses — which counts a genuine reorder as a change."""
    repo = _FakeSceneRepo(_ledger())
    el_3 = {"id": "el_3", "type": "action", "text": "A third beat."}
    el_4 = {"id": "el_4", "type": "action", "text": "A fourth beat."}
    _observe([_EL_1, _EL_2, el_3, el_4], 2)
    # Same four elements, same text — el_2 has been dragged to the end.
    current = [dict(_EL_1), dict(el_3), dict(el_4), dict(_EL_2)]

    patches = _patched_gateway(repo, current_version=3, current_elements=current)
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "A rewrite of a moved line."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditRefused)
    assert outcome.conflicting_element_ids == ("el_2",)
    assert "moved" in outcome.message
    assert repo.calls == []


@pytest.mark.asyncio
async def test_an_author_INSERTING_elsewhere_does_not_block_the_edit():
    """The boundary of M1's strictness, pinned so nobody tightens it by
    accident.

    ``diff_scenes`` uses an LCS, so an element the author inserted somewhere
    else reports as ``added`` and leaves untouched elements in place — it does
    NOT cascade a "moved" onto everything after it. That matters: inserts are
    the single most common op in production (1146 of 1519), and treating a
    position shift as a conflict would refuse most legitimate rebases while
    protecting nothing — the agent's target still says exactly what it said."""
    repo = _FakeSceneRepo(_ledger(), new_version=4)
    _observe([_EL_1, _EL_2], 2)
    inserted = {"id": "el_new", "type": "action", "text": "A new beat above."}
    current = [dict(_EL_1), inserted, dict(_EL_2)]

    patches = _patched_gateway(repo, current_version=3, current_elements=current)
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "You said that last time."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditApplied)
    assert outcome.rebased_from == 2


@pytest.mark.asyncio
async def test_the_write_carries_only_text_so_element_type_survives():
    """An agent rewriting a line of dialogue must not be able to turn it
    into an action line, or reassign the character speaking it. The ops it
    emits carry ``text`` and nothing else."""
    repo = _FakeSceneRepo(_ledger(), new_version=3)
    _observe([_EL_1, _EL_2], 2)
    patches = _patched_gateway(
        repo, current_version=2, current_elements=[dict(_EL_1), dict(_EL_2)]
    )
    with patches[0], patches[1]:
        await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "You said that last time."},
            quoted_base_version=2,
            actor="agent:run-9",
        )

    ops = repo.calls[0]["ops"]
    assert ops == [
        {
            "op": "update",
            "element_id": "el_2",
            "payload": {"text": "You said that last time."},
        }
    ]
    # Attributed to the agent, never to the writer — the ledger is what the
    # editor's history reads to say who typed this.
    assert repo.calls[0]["actor"] == "agent:run-9"


@pytest.mark.asyncio
async def test_an_op_protocol_rejection_gets_its_own_code_not_conflict():
    """``OpError`` and ``VersionConflict`` mean different things to the
    model: one says 'your batch was malformed', the other says 'rebase'.
    Collapsing them would send the model into a pointless re-read loop."""
    repo = _FakeSceneRepo(
        _ledger(), raises=OpError("unknown_element", "element 'el_2' not found")
    )
    _observe([_EL_1, _EL_2], 2)
    patches = _patched_gateway(
        repo, current_version=2, current_elements=[dict(_EL_1), dict(_EL_2)]
    )
    with patches[0], patches[1]:
        outcome = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "..."},
            quoted_base_version=2,
            actor="agent:test",
        )

    assert isinstance(outcome, gateway_mod.EditRefused)
    assert outcome.code == "op_rejected"


# ===================================================================== #
# 2. The conflict result serves BOTH audiences.
# ===================================================================== #


def test_the_conflict_result_is_machine_readable_and_human_actionable():
    """Spec §5.2: 冲突必须明示. One object has to satisfy two readers — the
    model, which branches on a code and needs the current text to rebase
    against, and the writer, who needs a sentence saying what happened and
    what to do. A conflict that is only one of those is the silent-ish
    failure this contract forbids."""
    refused = gateway_mod.EditRefused(
        code="version_conflict",
        message=gateway_mod._conflict_message({"el_2": "changed"}),
        scene_id=_SCENE_ID,
        element_ids=("el_2",),
        observed_version=2,
        current_version=3,
        conflicting_element_ids=("el_2",),
        current_elements=({**_EL_2, "text": "Actually, I lied."},),
    )
    payload = refused.as_dict()

    # Machine-readable: a stable code, and the state needed to act on it.
    assert payload["ok"] is False
    assert payload["applied"] is False
    assert payload["error_code"] == "version_conflict"
    assert payload["observed_content_version"] == 2
    assert payload["current_content_version"] == 3
    assert payload["conflicting_element_ids"] == ["el_2"]
    assert payload["current_elements"][0]["text"] == "Actually, I lied."

    # Human-actionable: says nothing was written, what changed, and what to
    # do next — not the word "conflict" and a version number.
    assert "Nothing was written" in payload["error"]
    assert "changed after you read them" in payload["error"]
    assert "rebase" in payload["error"]


# ===================================================================== #
# 3. The selection attachment (spec §5.3) — a handle, not a blob.
# ===================================================================== #


def test_the_attachment_shape_is_ids_and_a_human_summary():
    """The payload is ``{scene_id, element_ids, summary_text}``, and
    ``summary_text`` is explicitly for the chip and the log — never the
    thing edited."""
    attachment = ScriptSelectionAttachment(
        scene_id=str(_SCENE_ID),
        element_ids=["el_2", "el_1"],
        summary_text="I told you I would come.",
    )
    assert attachment.element_ids == ["el_2", "el_1"]


def test_selection_order_is_preserved_and_duplicates_collapse():
    """Selection order is the passage's reading order (what the chip shows
    and what the agent reasons about), so it must survive de-duplication."""
    attachment = ScriptSelectionAttachment(
        scene_id="1", element_ids=[" el_2 ", "el_1", "el_2", ""]
    )
    assert attachment.element_ids == ["el_2", "el_1"]


@pytest.mark.parametrize(
    "raw",
    [
        "el_1",  # not an object at all
        {"element_ids": ["el_1"]},  # no scene
        {"scene_id": "  ", "element_ids": ["el_1"]},
        {"scene_id": "1", "element_ids": []},
        {"scene_id": "1", "element_ids": ["", "  "]},
        {"scene_id": "1"},
    ],
)
def test_malformed_selection_payloads_are_rejected_not_raised(raw):
    """These arrive as LLM tool arguments, where malformed is an everyday
    occurrence — so the answer is a typed rejection the model can read, not
    an exception in the agent loop."""
    result = parse_selection(raw)
    assert isinstance(result, SelectionRejected)
    assert result.code == "invalid_selection"
    assert result.message


def test_a_single_element_id_may_arrive_as_a_bare_string():
    parsed = parse_selection({"scene_id": "1", "element_ids": "el_7"})
    assert isinstance(parsed, ParsedSelection)
    assert parsed.element_ids == ("el_7",)


def test_an_oversized_selection_is_capped_at_the_same_size_both_paths():
    parsed = parse_selection(
        {"scene_id": "1", "element_ids": [f"el_{i}" for i in range(200)]}
    )
    assert len(parsed.element_ids) == MAX_SELECTION_ELEMENTS


@pytest.mark.asyncio
async def test_foreign_or_invented_element_ids_are_rejected_and_audited():
    """An element id that is not in the resolved scene — copied from another
    scene, remembered from a previous turn, or invented — is refused, and
    the attempt is recorded. The resolver only ever saw the SCENE id, so
    without this row the attempt leaves no trace at all."""
    audit = AsyncMock()
    with (
        patch.object(
            selection_mod, "resolve_scene", AsyncMock(return_value=_resolved_scene())
        ),
        patch.object(selection_mod, "audit_resolution", audit),
    ):
        result = await selection_mod.resolve_selection(
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_2", "el_from_another_script"],
            },
            _scope(),
        )

    assert isinstance(result, SelectionRejected)
    assert result.code == "unknown_element"
    assert "el_from_another_script" in result.message

    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert kwargs["granted"] is False
    assert kwargs["detail_code"] == "element_not_in_scene"
    assert audit.await_args.args[1] == "scene_element"


@pytest.mark.asyncio
async def test_a_selection_naming_a_scene_outside_the_scope_is_denied_by_A2():
    """The selection path adds a check; it does not replace one. The scene
    id still goes through the single resolver, and its ``Denied`` comes back
    untouched."""
    denied = Denied("scene", "999", detail_code="project_mismatch")
    with patch.object(selection_mod, "resolve_scene", AsyncMock(return_value=denied)):
        result = await selection_mod.resolve_selection(
            {"scene_id": "999", "element_ids": ["el_1"]}, _scope()
        )
    assert result is denied


@pytest.mark.asyncio
async def test_a_resolved_selection_is_ordered_by_the_scene_not_the_caller():
    """The agent should reason about the passage in reading order; a
    selection listing ids backwards must not make it rewrite them
    backwards."""
    with patch.object(
        selection_mod, "resolve_scene", AsyncMock(return_value=_resolved_scene())
    ):
        result = await selection_mod.resolve_selection(
            {"scene_id": str(_SCENE_ID), "element_ids": ["el_2", "el_1"]}, _scope()
        )
    assert result.element_ids == ("el_1", "el_2")
    assert result.base_content_version == 2


# ===================================================================== #
# 4. The A1 gate — nobody reaches any of this without the grading.
# ===================================================================== #


def _hook_ctx(tool_name: str) -> HookContext:
    return HookContext(
        run_id=_RUN_ID,
        agent_id=UUID(_AGENT_ID),
        agent_slug="screenwriter",
        user_id=UUID(_USER_ID),
        session_id=None,
        tool_name=tool_name,
        tool_args={},
        accumulated_prompt_tokens=0,
        accumulated_completion_tokens=0,
        accumulated_cost_cents=0.0,
        iteration=1,
    )


def _agent(write_level: str) -> dict:
    return {"capability_profile": {"capabilities": {"write_level": write_level}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["ProposeEdit", "ApplyEdit"])
@pytest.mark.parametrize("write_level", ["none", "read"])
async def test_an_agent_without_propose_grading_reaches_neither_edit_tool(
    tool_name, write_level
):
    """Fail-closed: the edit contract is unreachable before any handler runs,
    so none of the guarantees above are even load-bearing for these agents."""
    result = await HighRiskCapabilityGateHook(agent=_agent(write_level))(
        _hook_ctx(tool_name)
    )
    assert result.decision == "abort"
    assert tool_name in result.abort_reason


@pytest.mark.asyncio
async def test_a_propose_graded_agent_may_propose_but_not_apply():
    """The line A1's ordinal ladder exists to draw, and A5's reason for two
    tool names instead of one tool that sometimes writes: an agent trusted
    to suggest revisions is not thereby trusted to commit them."""
    hook = HighRiskCapabilityGateHook(agent=_agent("propose"))
    assert (await hook(_hook_ctx("ProposeEdit"))).decision == "continue"

    denied = await hook(_hook_ctx("ApplyEdit"))
    assert denied.decision == "abort"
    assert "write" in denied.abort_reason


@pytest.mark.asyncio
async def test_a_write_graded_agent_reaches_both():
    hook = HighRiskCapabilityGateHook(agent=_agent("write"))
    assert (await hook(_hook_ctx("ProposeEdit"))).decision == "continue"
    assert (await hook(_hook_ctx("ApplyEdit"))).decision == "continue"


# ===================================================================== #
# 5. The tool handlers.
# ===================================================================== #


def _tool_patches(scene=None):
    return (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            selection_mod,
            "resolve_scene",
            AsyncMock(return_value=scene or _resolved_scene()),
        ),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="4")),
    )


@pytest.mark.asyncio
async def test_apply_edit_without_a_quoted_version_refuses_rather_than_defaulting():
    """The one default that would have quietly destroyed the contract:
    filling ``base_content_version`` in from the scene's current version
    makes the precondition compare a value to itself and pass every time —
    a guard-shaped no-op. It is required, with no fallback."""
    applied = AsyncMock()
    patches = _tool_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(gateway_mod, "apply_element_edit", applied),
    ):
        result = await SCREENWRITING_HANDLERS["ApplyEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_2"],
                "edits": [{"element_id": "el_2", "text": "New line."}],
            },
            _RUN_CONTEXT,
        )

    assert result["ok"] is False
    assert result["error_code"] == "missing_precondition"
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_edit_forwards_the_quoted_version_as_a_cross_check_only():
    """The tool still requires and forwards ``base_content_version``, but it
    arrives at the gateway under a name that says what it is — a claim to be
    checked against the server's record, not the precondition itself."""
    applied = AsyncMock(
        return_value=gateway_mod.EditApplied(
            scene_id=_SCENE_ID,
            element_ids=("el_2",),
            content_version=4,
            rebased_from=2,
            observed_version=2,
            quoted_base_version=2,
        )
    )
    patches = _tool_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(gateway_mod, "apply_element_edit", applied),
    ):
        result = await SCREENWRITING_HANDLERS["ApplyEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_2"],
                "edits": [{"element_id": "el_2", "text": "New line."}],
                "base_content_version": 2,
            },
            _RUN_CONTEXT,
        )

    assert applied.await_args.kwargs["quoted_base_version"] == 2
    assert applied.await_args.args[2] == {"el_2": "New line."}
    assert result["ok"] is True and result["applied"] is True
    # A rebase is reported to the human as their work being kept, not as a
    # silent success.
    assert "their changes were kept" in result["note"]


@pytest.mark.asyncio
async def test_proposed_text_across_several_elements_is_refused_as_ambiguous():
    """A4 accepted one blob of text for N element ids and returned it for a
    human to sort out. A5 writes, so the same input would be a guess about
    which line gets the text — refuse instead of guessing."""
    patches = _tool_patches()
    with patches[0], patches[1], patches[2]:
        result = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_1", "el_2"],
                "proposed_text": "One blob for two elements.",
            },
            _RUN_CONTEXT,
        )
    assert result["ok"] is False
    assert result["error_code"] == "invalid_args"
    assert "ambiguous" in result["error"]


@pytest.mark.asyncio
async def test_edits_naming_an_element_outside_the_selection_are_refused():
    """The selection is the boundary of the passage the writer handed over.
    An edit aimed past it is not the edit they asked for."""
    patches = _tool_patches()
    with patches[0], patches[1], patches[2]:
        result = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_2"],
                "edits": [{"element_id": "el_1", "text": "Not what was selected."}],
            },
            _RUN_CONTEXT,
        )
    assert result["ok"] is False
    assert result["error_code"] == "unknown_element"


@pytest.mark.asyncio
async def test_propose_edit_still_writes_nothing():
    """ProposeEdit runs the whole contract short of the write. If it ever
    starts calling the ops channel, a propose-graded agent has silently been
    given write access."""
    applied = AsyncMock()
    patches = _tool_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(gateway_mod, "apply_element_edit", applied),
    ):
        result = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_2"],
                "edits": [{"element_id": "el_2", "text": "New line."}],
                "base_content_version": 2,
            },
            _RUN_CONTEXT,
        )

    assert result["ok"] is True
    assert result["applied"] is False
    assert result["stale"] is False
    assert result["proposal"]["edits"] == [{"element_id": "el_2", "text": "New line."}]
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_attached_selection_outranks_ids_the_model_supplied():
    """A7's panel posts what the writer actually selected. When it is
    present, ids the model typed are ignored rather than merged — a half
    model, half human target is a passage nobody chose."""
    resolve = AsyncMock(return_value=_resolved_scene())
    patches = _tool_patches()
    with patches[0], patches[2], patch.object(selection_mod, "resolve_scene", resolve):
        result = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": "999999",  # the model's recollection — wrong
                "element_ids": ["el_1"],
                "edits": [{"element_id": "el_2", "text": "New line."}],
            },
            {
                **_RUN_CONTEXT,
                "selection": ParsedSelection(
                    scene_id=str(_SCENE_ID),
                    element_ids=("el_2",),
                    summary_text="I told you I would come.",
                ),
            },
        )

    # The scene actually resolved is the attached one, not the model's.
    assert resolve.await_args.args[0] == str(_SCENE_ID)
    assert result["ok"] is True
    assert result["proposal"]["element_ids"] == ["el_2"]


@pytest.mark.asyncio
async def test_propose_edit_never_hands_back_the_scenes_current_version():
    """A5 review, Critical — the disclosure half.

    The first cut set ``proposal.base_content_version`` to the scene's CURRENT
    version, so a model told "your proposal is stale" received, in the same
    payload, the number that used to switch the write precondition off. It now
    echoes only what the model itself quoted. (The precondition no longer reads
    any model-supplied number, so this is defence in depth — but there is no
    reason to hand the number over, so we don't.)"""
    patches = _tool_patches(_resolved_scene(version=9))
    with patches[0], patches[1], patches[2]:
        stale = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_2"],
                "edits": [{"element_id": "el_2", "text": "New line."}],
                "base_content_version": 7,
            },
            _RUN_CONTEXT,
        )

    assert stale["stale"] is True
    assert stale["proposal"]["base_content_version"] == 7  # the model's own
    assert 9 not in stale["proposal"].values()
    assert "9" not in stale["note"]


@pytest.mark.asyncio
async def test_a_partly_malformed_edit_batch_writes_nothing_and_says_so():
    """A5 review, M2. Dropping bad entries silently meant 3 edits with 1
    malformed wrote 2 and reported success — the model would believe a
    revision landed that never did, and so would the writer reading the
    transcript."""
    applied = AsyncMock()
    patches = _tool_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(gateway_mod, "apply_element_edit", applied),
    ):
        result = await SCREENWRITING_HANDLERS["ApplyEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_1", "el_2"],
                "edits": [
                    {"element_id": "el_1", "text": "Fine."},
                    {"element_id": "el_2"},  # no text
                ],
                "base_content_version": 2,
            },
            _RUN_CONTEXT,
        )

    assert result["ok"] is False
    assert result["error_code"] == "invalid_args"
    assert "1 of 2 edits are malformed" in result["error"]
    applied.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_scene_is_what_records_the_observation():
    """The record has to be written where content is handed to the model, or
    it stops describing what the model actually read. Asserted through the
    real gateway function rather than by calling the store directly — the
    wiring is the thing that can rot."""
    scene = _resolved_scene(version=5)
    elements = await gateway_mod.read_scene_elements(_scope(), scene)

    assert [el["element_id"] for el in elements] == ["el_1", "el_2"]
    observation = observations_mod.observed_scene(_RUN_ID, _SCENE_ID)
    assert observation is not None
    assert observation.content_version == 5
    # RAW elements, not the trimmed {element_id,type,text} projection — the
    # write-time diff compares whole elements.
    assert observation.elements[1]["character_id"] == "ch_9"
    assert observation.elements[1]["id"] == "el_2"


@pytest.mark.asyncio
async def test_re_reading_a_scene_replaces_the_record():
    """ "Re-read the scene and rebase" has to actually clear the conflict, or
    the retry loop never terminates."""
    _observe([_EL_1, _EL_2], 2)
    moved_on = [dict(_EL_1), {**_EL_2, "text": "Actually, I lied."}]
    repo = _FakeSceneRepo(_ledger(), new_version=4)

    patches = _patched_gateway(repo, current_version=3, current_elements=moved_on)
    with patches[0], patches[1]:
        refused = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "v1"},
            quoted_base_version=2,
            actor="agent:test",
        )
        # The model does what it was told: reads the scene again.
        await gateway_mod.read_scene_elements(
            _scope(), _resolved_scene(content=moved_on, version=3)
        )
        after = await gateway_mod.apply_element_edit(
            _scope(),
            _resolved_scene(),
            {"el_2": "v2 based on the new text"},
            quoted_base_version=3,
            actor="agent:test",
        )

    assert isinstance(refused, gateway_mod.EditRefused)
    assert isinstance(after, gateway_mod.EditApplied)
    assert repo.calls[0]["ops"][0]["payload"]["text"] == "v2 based on the new text"


def test_the_observation_store_is_bounded_and_per_run():
    """Two runs never see each other's records, and the map cannot grow
    without limit. Eviction degrades to "read the scene again", never to a
    bypass — but only if it is actually bounded."""
    observations_mod.record_scene_read("run-a", "s1", 1, [dict(_EL_1)])
    observations_mod.record_scene_read("run-b", "s1", 2, [dict(_EL_2)])
    assert observations_mod.observed_scene("run-a", "s1").content_version == 1
    assert observations_mod.observed_scene("run-b", "s1").content_version == 2
    assert observations_mod.observed_scene("run-c", "s1") is None

    for i in range(observations_mod.MAX_SCENES_PER_RUN + 10):
        observations_mod.record_scene_read("run-a", f"scene-{i}", 1, [dict(_EL_1)])
    assert (
        len(observations_mod._OBSERVED["run-a"]) == observations_mod.MAX_SCENES_PER_RUN
    )
    # The oldest went first (LRU), and an evicted entry reads as "never read".
    assert observations_mod.observed_scene("run-a", "scene-0") is None
