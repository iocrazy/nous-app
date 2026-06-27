"""Agent Memory Phase C1 — promotion gate tests.

Covers:
- PromotionVerdict dataclass
- build_promotion_prompt
- parse_promotion_output (fence stripping, prose tolerance, fail-closed)
- evaluate_promotion (shareable gate, confidence gate, kind gate, exception gate)
- resolve_promotion_target (team-scope EXISTS, project-scope backfill, unauthorized → None)

All DB calls are mocked via write_scope; no real LLM or DB is required.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — reusable fake session / scope
# ---------------------------------------------------------------------------


class _Session:
    """Async session stub that returns pre-programmed results per execute call."""

    def __init__(self, *execute_returns):
        self._returns = list(execute_returns)
        self.calls: list = []

    async def execute(self, stmt, params=None):
        self.calls.append({"sql": str(stmt), "params": params})
        if self._returns:
            return self._returns.pop(0)
        return _EmptyResult()


class _Scope:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


class _EmptyResult:
    def mappings(self):
        return self

    def all(self):
        return []

    def scalars(self):
        return self

    def fetchone(self):
        return None

    def scalar(self):
        return None


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def mappings(self):
        return self

    def fetchone(self):
        return self._value


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return [dict(r) for r in self._rows]

    def fetchone(self):
        if self._rows:
            return dict(self._rows[0])
        return None

    def scalar(self):
        if self._rows:
            return self._rows[0]
        return None


# ---------------------------------------------------------------------------
# PromotionVerdict dataclass
# ---------------------------------------------------------------------------


def test_promotion_verdict_is_frozen():
    from app.services.ai.memory.promotion_gate import PromotionVerdict

    v = PromotionVerdict(
        shareable=True,
        confidence=0.9,
        justification="clear project decision",
        scrubbed_body_md="Deploy via `make release`.",
    )
    assert v.shareable is True
    assert v.confidence == 0.9
    assert v.justification == "clear project decision"
    assert v.scrubbed_body_md == "Deploy via `make release`."

    with pytest.raises((AttributeError, TypeError)):
        v.shareable = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_promotion_prompt
# ---------------------------------------------------------------------------


def test_build_promotion_prompt_contains_key_fields():
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import build_promotion_prompt

    draft = MemoryDraft(
        title="Deploy process",
        body_md="Run `make release` then tag.",
        when_to_use="When deploying.",
        kind="procedure",
    )
    prompt = build_promotion_prompt(draft=draft, scope="team")

    assert "Deploy process" in prompt
    assert "procedure" in prompt
    assert "team" in prompt
    # Must instruct JSON with expected keys
    assert "shareable" in prompt
    assert "confidence" in prompt
    assert "scrubbed_body_md" in prompt
    assert "justification" in prompt


# ---------------------------------------------------------------------------
# parse_promotion_output
# ---------------------------------------------------------------------------


def test_parse_returns_verdict_on_valid_json():
    from app.services.ai.memory.promotion_gate import parse_promotion_output

    text = (
        '{"shareable": true, "confidence": 0.9, '
        '"justification": "good", "scrubbed_body_md": "safe body"}'
    )
    v = parse_promotion_output(text)
    assert v is not None
    assert v.shareable is True
    assert v.confidence == 0.9
    assert v.scrubbed_body_md == "safe body"


def test_parse_handles_code_fences():
    from app.services.ai.memory.promotion_gate import parse_promotion_output

    text = (
        "```json\n"
        '{"shareable": true, "confidence": 0.85, '
        '"justification": "ok", "scrubbed_body_md": "body here"}\n'
        "```"
    )
    v = parse_promotion_output(text)
    assert v is not None
    assert v.shareable is True
    assert v.confidence == 0.85


def test_parse_handles_prose_surrounding_json():
    from app.services.ai.memory.promotion_gate import parse_promotion_output

    text = (
        "Here is my assessment:\n"
        '{"shareable": false, "confidence": 0.3, '
        '"justification": "too personal", "scrubbed_body_md": ""}\n'
        "Hope that helps."
    )
    v = parse_promotion_output(text)
    assert v is not None
    assert v.shareable is False


def test_parse_returns_none_on_malformed_json():
    from app.services.ai.memory.promotion_gate import parse_promotion_output

    assert parse_promotion_output("not json at all") is None
    assert parse_promotion_output("") is None
    assert parse_promotion_output("{}") is None  # missing required fields
    assert parse_promotion_output("[1,2,3]") is None  # array not object


def test_parse_returns_none_on_missing_required_fields():
    from app.services.ai.memory.promotion_gate import parse_promotion_output

    # Missing scrubbed_body_md
    text = '{"shareable": true, "confidence": 0.9, "justification": "ok"}'
    assert parse_promotion_output(text) is None


# ---------------------------------------------------------------------------
# evaluate_promotion — the main gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_promotion_returns_verdict_when_shareable_and_fact():
    """Happy path: shareable=true, confidence=0.9, kind='fact' → verdict returned."""
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import (
        PromotionVerdict,
        evaluate_promotion,
    )

    draft = MemoryDraft(
        title="DB migration policy",
        body_md="Always run migrations in a transaction.",
        when_to_use="Before DB changes.",
        kind="fact",
    )

    async def fake_evaluator(prompt: str) -> str:
        return (
            '{"shareable": true, "confidence": 0.9, '
            '"justification": "Durable team knowledge", '
            '"scrubbed_body_md": "Always run migrations in a transaction."}'
        )

    result = await evaluate_promotion(
        draft=draft, scope="team", evaluator=fake_evaluator
    )
    assert isinstance(result, PromotionVerdict)
    assert result.shareable is True
    assert result.confidence == 0.9


@pytest.mark.asyncio
async def test_evaluate_promotion_returns_none_when_shareable_false():
    """shareable=false → None even with high confidence."""
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import evaluate_promotion

    draft = MemoryDraft(
        title="Personal note",
        body_md="I prefer dark mode.",
        when_to_use="Always.",
        kind="preference",
    )

    async def fake_evaluator(prompt: str) -> str:
        return (
            '{"shareable": false, "confidence": 0.95, '
            '"justification": "Too personal", "scrubbed_body_md": ""}'
        )

    result = await evaluate_promotion(
        draft=draft, scope="team", evaluator=fake_evaluator
    )
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_promotion_returns_none_when_confidence_below_threshold():
    """confidence < 0.7 → None even if shareable=true."""
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import evaluate_promotion

    draft = MemoryDraft(
        title="Team decision",
        body_md="Use PostgreSQL.",
        when_to_use="DB selection.",
        kind="decision",
    )

    async def fake_evaluator(prompt: str) -> str:
        return (
            '{"shareable": true, "confidence": 0.65, '
            '"justification": "Maybe team relevant", '
            '"scrubbed_body_md": "Use PostgreSQL."}'
        )

    result = await evaluate_promotion(
        draft=draft, scope="team", evaluator=fake_evaluator
    )
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_promotion_returns_none_when_kind_is_preference():
    """kind='preference' → None even if shareable=true and confidence=0.9 (kind gate)."""
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import evaluate_promotion

    draft = MemoryDraft(
        title="Team prefers tabs",
        body_md="Use tabs for indentation.",
        when_to_use="Code style.",
        kind="preference",
    )

    async def fake_evaluator(prompt: str) -> str:
        # LLM wrongly says shareable=true; kind gate must reject
        return (
            '{"shareable": true, "confidence": 0.92, '
            '"justification": "Style preference is shared", '
            '"scrubbed_body_md": "Use tabs for indentation."}'
        )

    result = await evaluate_promotion(
        draft=draft, scope="team", evaluator=fake_evaluator
    )
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_promotion_returns_none_when_evaluator_returns_malformed():
    """Malformed LLM output → fail-closed → None, never raises."""
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import evaluate_promotion

    draft = MemoryDraft(
        title="Something",
        body_md="Body.",
        when_to_use="When.",
        kind="fact",
    )

    async def bad_evaluator(prompt: str) -> str:
        return "I cannot classify this right now."

    result = await evaluate_promotion(
        draft=draft, scope="team", evaluator=bad_evaluator
    )
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_promotion_returns_none_when_evaluator_raises():
    """Evaluator raising an exception → fail-closed → None, never raises."""
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import evaluate_promotion

    draft = MemoryDraft(
        title="Something",
        body_md="Body.",
        when_to_use="When.",
        kind="fact",
    )

    async def exploding_evaluator(prompt: str) -> str:
        raise RuntimeError("LLM is down")

    result = await evaluate_promotion(
        draft=draft, scope="team", evaluator=exploding_evaluator
    )
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_promotion_accepts_decision_and_procedure_kinds():
    """kind='decision' and kind='procedure' are also shareable."""
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft
    from app.services.ai.memory.promotion_gate import (
        PromotionVerdict,
        evaluate_promotion,
    )

    for kind in ("decision", "procedure"):
        draft = MemoryDraft(
            title=f"Team {kind}",
            body_md=f"Body for {kind}.",
            when_to_use="When relevant.",
            kind=kind,
        )

        async def fake_evaluator(prompt: str, _k=kind) -> str:
            return (
                f'{{"shareable": true, "confidence": 0.8, '
                f'"justification": "Good {_k}", '
                f'"scrubbed_body_md": "Body for {_k}."}}'
            )

        result = await evaluate_promotion(
            draft=draft, scope="team", evaluator=fake_evaluator
        )
        assert isinstance(result, PromotionVerdict), f"Expected verdict for kind={kind}"


# ---------------------------------------------------------------------------
# resolve_promotion_target — authorization gate (DB-bound, session-mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_team_scope_issues_exists_query_and_returns_team_id():
    """Team scope: runs EXISTS(team_members) query; authorized → (team_id, None)."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    # The EXISTS query returns truthy (1 row found)
    exists_result = _ScalarResult(True)
    session = _Session(exists_result)

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="team",
            team_id=10,
            project_id=None,
        )

    assert result == (10, None)
    assert len(session.calls) == 1
    sql_upper = session.calls[0]["sql"].upper()
    # Must issue some form of membership check
    assert "TEAM_MEMBERS" in sql_upper or "team_members" in session.calls[0]["sql"]
    params = session.calls[0]["params"]
    assert params["team_id"] == 10
    assert params["owner"] == "user-uuid-1"


@pytest.mark.asyncio
async def test_resolve_team_scope_returns_none_when_not_member():
    """Team scope: EXISTS returns False → None (unauthorized)."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    exists_result = _ScalarResult(False)
    session = _Session(exists_result)

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="team",
            team_id=10,
            project_id=None,
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_team_scope_returns_none_when_team_id_null():
    """Team scope with team_id=None → None (fast-path, no DB query)."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    session = _Session()  # no results needed

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="team",
            team_id=None,
            project_id=None,
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_project_scope_reads_team_id_then_checks_membership():
    """Project scope: reads projects.team_id, checks membership → (project_team_id, project_id)."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    # First execute: SELECT team_id FROM projects WHERE id=:project_id → 20
    projects_result = _ScalarResult(20)
    # Second execute: EXISTS(team_members WHERE team_id=20 AND user_id=owner) → True
    exists_result = _ScalarResult(True)
    session = _Session(projects_result, exists_result)

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="project",
            team_id=None,
            project_id=55,
        )

    assert result == (20, 55)
    assert len(session.calls) == 2

    # First call: look up projects.team_id
    first_sql = session.calls[0]["sql"]
    assert "projects" in first_sql.lower()
    assert session.calls[0]["params"]["project_id"] == 55

    # Second call: membership check against the project's team
    second_sql = session.calls[1]["sql"]
    assert "team_members" in second_sql.lower()
    second_params = session.calls[1]["params"]
    assert second_params["team_id"] == 20
    assert second_params["owner"] == "user-uuid-1"


@pytest.mark.asyncio
async def test_resolve_project_scope_returns_none_when_project_team_id_null():
    """Project scope: projects.team_id IS NULL → None (project has no team)."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    # SELECT team_id FROM projects returns None
    projects_result = _ScalarResult(None)
    session = _Session(projects_result)

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="project",
            team_id=None,
            project_id=55,
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_project_scope_returns_none_when_not_team_member():
    """Project scope: owner not in project's team → None."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    projects_result = _ScalarResult(20)
    exists_result = _ScalarResult(False)
    session = _Session(projects_result, exists_result)

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="project",
            team_id=None,
            project_id=55,
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_for_unknown_scope():
    """Unknown scope → None without any DB call."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    session = _Session()

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="agent_user",
            team_id=None,
            project_id=None,
        )

    assert result is None
    assert len(session.calls) == 0  # no DB queries issued


@pytest.mark.asyncio
async def test_resolve_returns_none_on_db_error():
    """DB error → fail-closed → None, never raises."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        side_effect=RuntimeError("db is down"),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="team",
            team_id=10,
            project_id=None,
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_project_scope_returns_none_when_project_id_null():
    """Project scope with project_id=None → None (fast-path)."""
    from app.repositories.agent_memory_promotion_repository import (
        resolve_promotion_target,
    )

    session = _Session()

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        result = await resolve_promotion_target(
            owner_user_id="user-uuid-1",
            scope="project",
            team_id=None,
            project_id=None,
        )

    assert result is None
