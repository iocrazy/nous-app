"""Agent memory promotion repository (Phase C1) — unit tests.

All DB calls are intercepted by patching ``write_scope`` to yield a
mock session; no real DB or SQLAlchemy engine is required.
"""

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — reusable fake session / scope builders
# ---------------------------------------------------------------------------


class _Session:
    """Minimal async session stub that records every execute() call."""

    def __init__(self, *execute_returns):
        # execute_returns: ordered sequence of values to return per execute call.
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


class _ScalarResult:
    """Fake result whose .scalar() returns a fixed value (e.g. True/False)."""

    def __init__(self, value):
        self._value = value

    def scalar(self):
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


# ---------------------------------------------------------------------------
# insert_proposal tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_proposal_returns_true_on_success():
    """insert_proposal executes INSERT and returns True."""
    from app.repositories.agent_memory_promotion_repository import insert_proposal

    session = _Session(_EmptyResult())

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        ok = await insert_proposal(
            memory_id=111,
            proposed_scope="team",
            target_team_id=10,
            target_project_id=None,
            classification_kind="fact",
            confidence=0.9,
            justification="looks shareable",
            scrubbed_body_md="sanitised body",
        )

    assert ok is True
    assert len(session.calls) == 1
    params = session.calls[0]["params"]
    assert params["memory_id"] == 111
    assert params["target_team_id"] == 10
    assert params["proposed_scope"] == "team"
    assert params["scrubbed_body_md"] == "sanitised body"
    # ON CONFLICT DO NOTHING — SQL must contain the conflict clause
    assert "ON CONFLICT" in session.calls[0]["sql"].upper()


@pytest.mark.asyncio
async def test_insert_proposal_returns_false_on_error():
    """insert_proposal swallows exceptions and returns False."""
    from app.repositories.agent_memory_promotion_repository import insert_proposal

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        side_effect=RuntimeError("db exploded"),
    ):
        ok = await insert_proposal(
            memory_id=1,
            proposed_scope="team",
            target_team_id=5,
            target_project_id=None,
            classification_kind="fact",
            confidence=0.5,
            justification="j",
            scrubbed_body_md="b",
        )
    assert ok is False


# ---------------------------------------------------------------------------
# approve_proposal tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_proposal_success_issues_both_updates():
    """approve_proposal: pending select returns a row + membership confirmed → both UPDATEs executed."""
    from app.repositories.agent_memory_promotion_repository import approve_proposal

    # Call 0: SELECT FOR UPDATE returns the pending proposal row.
    # Call 1: membership EXISTS check returns True (owner is still a member).
    # Call 2: UPDATE agent_memory.
    # Call 3: UPDATE agent_memory_promotions.
    pending_row = {
        "id": 42,
        "memory_id": 111,
        "target_team_id": 10,
        "target_project_id": None,
        "scrubbed_body_md": "clean body",
        "status": "pending",
    }
    select_result = _MappingResult([pending_row])
    membership_result = _ScalarResult(True)  # owner IS still a member
    update_memory_result = _EmptyResult()
    update_promotion_result = _EmptyResult()

    session = _Session(
        select_result,
        membership_result,
        update_memory_result,
        update_promotion_result,
    )

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        ok = await approve_proposal(proposal_id=42, reviewer_id="admin-uuid")

    assert ok is True
    assert len(session.calls) == 4

    # Call 0: SELECT FOR UPDATE
    assert "FOR UPDATE" in session.calls[0]["sql"].upper()
    assert session.calls[0]["params"]["id"] == 42

    # Call 1: membership check — must reference team_members and memory_id
    membership_sql = session.calls[1]["sql"].upper()
    assert "TEAM_MEMBERS" in membership_sql
    assert "EXISTS" in membership_sql
    assert session.calls[1]["params"]["memory_id"] == 111
    assert session.calls[1]["params"]["target_team_id"] == 10

    # Call 2: UPDATE agent_memory — must set visibility='shared' and bind team_id
    memory_sql = session.calls[2]["sql"].upper()
    assert "UPDATE" in memory_sql
    assert "AGENT_MEMORY" in memory_sql
    memory_params = session.calls[2]["params"]
    assert memory_params.get("visibility") == "shared"
    assert memory_params.get("memory_id") == 111
    # Critical: team_id must be bound to proposal's target_team_id (10).
    # This satisfies the DB CHECK agent_memory_shared_requires_team_id which
    # requires team_id IS NOT NULL when visibility = 'shared'.
    assert memory_params.get("team_id") == 10

    # Call 3: UPDATE agent_memory_promotions — must bind reviewer_id + status=approved
    promo_sql = session.calls[3]["sql"].upper()
    assert "UPDATE" in promo_sql
    assert "AGENT_MEMORY_PROMOTIONS" in promo_sql
    # Verify the promotion row is stamped with status = 'approved' (matches
    # _UPDATE_PROMOTION_APPROVED_SQL which hard-codes the literal 'approved').
    assert "'APPROVED'" in promo_sql or "= 'approved'" in session.calls[3]["sql"]
    promo_params = session.calls[3]["params"]
    assert promo_params.get("reviewer_id") == "admin-uuid"
    assert promo_params.get("proposal_id") == 42


@pytest.mark.asyncio
async def test_approve_proposal_returns_false_when_not_pending():
    """approve_proposal: pending select returns no row → False, no mutation."""
    from app.repositories.agent_memory_promotion_repository import approve_proposal

    # SELECT FOR UPDATE returns empty
    select_result = _MappingResult([])
    session = _Session(select_result)

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        ok = await approve_proposal(proposal_id=99, reviewer_id="admin-uuid")

    assert ok is False
    # Only the SELECT was issued — no UPDATEs
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_approve_proposal_returns_false_on_error():
    """approve_proposal swallows exceptions and returns False."""
    from app.repositories.agent_memory_promotion_repository import approve_proposal

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        side_effect=RuntimeError("txn failed"),
    ):
        ok = await approve_proposal(proposal_id=1, reviewer_id="r")
    assert ok is False


# ---------------------------------------------------------------------------
# reject_proposal test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_proposal_returns_true_and_binds_reviewer():
    """reject_proposal issues an UPDATE and returns True."""
    from app.repositories.agent_memory_promotion_repository import reject_proposal

    session = _Session(_EmptyResult())

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        ok = await reject_proposal(proposal_id=7, reviewer_id="admin-uuid")

    assert ok is True
    assert len(session.calls) == 1
    sql = session.calls[0]["sql"].upper()
    assert "UPDATE" in sql
    assert "REJECTED" in sql or "rejected" in session.calls[0]["sql"]
    params = session.calls[0]["params"]
    assert params["proposal_id"] == 7
    assert params["reviewer_id"] == "admin-uuid"


# ---------------------------------------------------------------------------
# demote_memory test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demote_memory_binds_visibility_private():
    """demote_memory sets visibility='private' on the agent_memory row."""
    from app.repositories.agent_memory_promotion_repository import demote_memory

    session = _Session(_EmptyResult())

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        ok = await demote_memory(memory_id=55)

    assert ok is True
    assert len(session.calls) == 1
    params = session.calls[0]["params"]
    assert params["memory_id"] == 55
    assert params.get("visibility") == "private"


# ---------------------------------------------------------------------------
# list_proposals test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_proposals_returns_joined_rows():
    """list_proposals joins agent_memory and returns list of dicts."""
    from app.repositories.agent_memory_promotion_repository import list_proposals

    rows = [
        {
            "id": 1,
            "memory_id": 10,
            "status": "pending",
            "title": "Deploy secret",
            "owner_user_id": "u-uuid",
            "body_md": "original",
            "scope": "agent_user",
        }
    ]

    class _ListSession:
        async def execute(self, stmt, params=None):
            self._sql = str(stmt)
            self._params = params
            return _MappingResult(rows)

    session_obj = _ListSession()

    class _ListScope:
        async def __aenter__(self):
            return session_obj

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_ListScope(),
    ):
        result = await list_proposals(status="pending", limit=50)

    assert len(result) == 1
    assert result[0]["title"] == "Deploy secret"
    assert result[0]["owner_user_id"] == "u-uuid"
    assert "JOIN" in session_obj._sql.upper() or "join" in session_obj._sql
    assert session_obj._params["status"] == "pending"
    assert session_obj._params["limit"] == 50


# ---------------------------------------------------------------------------
# TOCTOU membership re-check test (Phase C1 security fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_proposal_returns_false_when_owner_no_longer_member():
    """TOCTOU guard: if the memory owner was removed from the target team between
    proposal-creation and admin approval, approve_proposal must return False
    WITHOUT issuing either mutating UPDATE.

    Only the SELECT FOR UPDATE (call 0) and the membership EXISTS check (call 1)
    should be recorded — no UPDATE to agent_memory, no UPDATE to
    agent_memory_promotions.
    """
    from app.repositories.agent_memory_promotion_repository import approve_proposal

    # Call 0: SELECT FOR UPDATE returns a pending proposal row.
    # Call 1: membership EXISTS check returns False (owner was removed from team).
    # No further calls should occur.
    pending_row = {
        "id": 77,
        "memory_id": 222,
        "target_team_id": 55,
        "target_project_id": None,
        "scrubbed_body_md": "sensitive memory body",
        "status": "pending",
    }
    select_result = _MappingResult([pending_row])
    membership_result = _ScalarResult(False)  # owner is NO LONGER a member

    session = _Session(select_result, membership_result)

    with patch(
        "app.repositories.agent_memory_promotion_repository.write_scope",
        return_value=_Scope(session),
    ):
        ok = await approve_proposal(proposal_id=77, reviewer_id="admin-uuid")

    # Must fail-closed: return False without sharing the memory.
    assert ok is False

    # Exactly 2 calls: the guard SELECT and the membership check — NO UPDATEs.
    assert len(session.calls) == 2

    # Call 0: the pending-guard SELECT FOR UPDATE
    assert "FOR UPDATE" in session.calls[0]["sql"].upper()
    assert session.calls[0]["params"]["id"] == 77

    # Call 1: the membership EXISTS check (atom within the same transaction)
    membership_sql = session.calls[1]["sql"].upper()
    assert "EXISTS" in membership_sql
    assert "TEAM_MEMBERS" in membership_sql
    assert session.calls[1]["params"]["memory_id"] == 222
    assert session.calls[1]["params"]["target_team_id"] == 55

    # Confirm neither mutating UPDATE was reached.
    # Note: the SELECT FOR UPDATE SQL itself contains the word UPDATE, so we check
    # for UPDATE statements (lines starting with UPDATE after stripping whitespace).
    for call in session.calls:
        sql_stripped = call["sql"].strip().upper()
        assert not sql_stripped.startswith(
            "UPDATE"
        ), f"Unexpected mutating UPDATE issued after membership check returned False:\n{call['sql']}"
