"""Sprint 4 — Commitment value-object + state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agent_framework.commitments import (
    TERMINAL_STATUSES,
    Commitment,
    CommitmentStatus,
    InvalidCommitmentError,
    TriggerType,
)

_AGENT = "00000000-0000-0000-0000-000000000001"
_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_time_trigger_requires_trigger_at():
    with pytest.raises(InvalidCommitmentError, match="trigger_at"):
        Commitment(
            agent_id=_AGENT,
            description="check in",
            trigger_type=TriggerType.TIME,
        )


@pytest.mark.unit
def test_event_trigger_requires_trigger_event():
    with pytest.raises(InvalidCommitmentError, match="trigger_event"):
        Commitment(
            agent_id=_AGENT,
            description="ping when ready",
            trigger_type=TriggerType.EVENT,
        )


@pytest.mark.unit
def test_next_session_needs_no_trigger_data():
    c = Commitment(
        agent_id=_AGENT,
        description="follow up next time you log in",
        trigger_type=TriggerType.NEXT_SESSION,
    )
    assert c.status == CommitmentStatus.PENDING


@pytest.mark.unit
def test_empty_description_rejected():
    with pytest.raises(InvalidCommitmentError, match="non-empty"):
        Commitment(
            agent_id=_AGENT,
            description="   ",
            trigger_type=TriggerType.NEXT_SESSION,
        )


@pytest.mark.unit
def test_string_inputs_coerce_to_enums():
    """DB rows arrive as strings — value object should accept them."""
    c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type="next_session",  # type: ignore[arg-type]
        status="pending",  # type: ignore[arg-type]
    )
    assert c.trigger_type == TriggerType.NEXT_SESSION
    assert c.status == CommitmentStatus.PENDING


@pytest.mark.unit
def test_is_due_time_trigger():
    """TIME commitment due iff now >= trigger_at."""
    c = Commitment(
        agent_id=_AGENT,
        description="reminder",
        trigger_type=TriggerType.TIME,
        trigger_at=_NOW,
    )
    assert c.is_due(now=_NOW) is True
    assert c.is_due(now=_NOW - timedelta(seconds=1)) is False
    assert c.is_due(now=_NOW + timedelta(hours=1)) is True


@pytest.mark.unit
def test_event_and_next_session_are_never_due():
    """is_due is the TIME-trigger sweeper signal. EVENT/NEXT_SESSION fire
    via different mechanisms, so is_due always returns False for them."""
    event_c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.EVENT,
        trigger_event="pr.merged:1",
    )
    session_c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
    )
    assert event_c.is_due(now=_NOW) is False
    assert session_c.is_due(now=_NOW) is False


@pytest.mark.unit
def test_terminal_status_not_due():
    c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.TIME,
        trigger_at=_NOW - timedelta(hours=1),
        status=CommitmentStatus.FULFILLED,
    )
    assert c.is_due(now=_NOW) is False


@pytest.mark.unit
def test_is_expired():
    c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
        expires_at=_NOW,
    )
    assert c.is_expired(now=_NOW) is True
    assert c.is_expired(now=_NOW - timedelta(seconds=1)) is False


@pytest.mark.unit
def test_no_expires_never_expired():
    c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
    )
    assert c.is_expired(now=_NOW) is False


@pytest.mark.unit
def test_terminal_status_not_expired():
    """Already-terminal rows shouldn't be considered expired (sweeper
    skips them entirely — TERMINAL is sticky)."""
    c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
        expires_at=_NOW - timedelta(hours=1),
        status=CommitmentStatus.CANCELLED,
    )
    assert c.is_expired(now=_NOW) is False


@pytest.mark.unit
def test_can_transition_from_pending_to_terminal():
    c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
    )
    for term in TERMINAL_STATUSES:
        assert c.can_transition_to(term) is True


@pytest.mark.unit
def test_terminal_is_sticky():
    """Once fulfilled/cancelled/expired/failed, no further transitions."""
    for status in TERMINAL_STATUSES:
        c = Commitment(
            agent_id=_AGENT,
            description="x",
            trigger_type=TriggerType.NEXT_SESSION,
            status=status,
        )
        for target in CommitmentStatus:
            assert (
                c.can_transition_to(target) is False
            ), f"{status} should not transition to {target}"


@pytest.mark.unit
def test_cannot_re_pend():
    """Cannot transition pending → pending (no-op transitions disallowed)."""
    c = Commitment(
        agent_id=_AGENT,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
    )
    assert c.can_transition_to(CommitmentStatus.PENDING) is False
