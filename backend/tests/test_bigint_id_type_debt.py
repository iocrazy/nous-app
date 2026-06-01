"""Regression tests for the UUID→BIGINT type-debt bug class (mig 231/232).

``ai_sessions.id`` (mig 231) and ``agent_runs.id`` (mig 232) became BIGINT
Snowflake ids — numeric strings like "310819108761487", NOT UUIDs. Code that
still wrapped these in ``UUID(...)`` raised ``ValueError: badly formed
hexadecimal UUID string``, and Pydantic/FastAPI models typed ``UUID`` raised
ValidationError / HTTP 422 on a bigint.

These tests pin the boundary contracts so the regression can't return:
  - Pydantic response models validate a bigint id (as int AND str).
  - ``ApprovalRequest.from_row`` accepts a bigint session_id / run_id.
  - ``map_chat_row_to_issue_message`` accepts a bigint run_id in metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.repositories.approval_requests_repository import ApprovalRequest
from app.schemas.agent_runs import RunDetail, RunListItem
from app.schemas.ai_library_chat import ChatResponse, MessageOut, SessionOut
from app.schemas.issue_message import IssueMessage, IssueMessageKind

# A representative BIGINT Snowflake id as both int (DB shape) and str (wire).
_BIGINT_INT = 310819108761487
_BIGINT_STR = "310819108761487"


@pytest.mark.unit
def test_session_out_accepts_bigint_id_as_str_and_int():
    user_id = uuid4()
    assert SessionOut(id=_BIGINT_STR, user_id=user_id).id == _BIGINT_STR
    # DB / PostgREST hand back a JSON number → Python int. Must coerce.
    assert SessionOut(id=_BIGINT_INT, user_id=user_id).id == _BIGINT_STR


@pytest.mark.unit
def test_message_out_accepts_bigint_session_id():
    msg_uuid = uuid4()
    # ai_messages.id is still a real UUID; session_id is the bigint FK.
    out = MessageOut(id=msg_uuid, session_id=_BIGINT_INT, role="user", content="hi")
    assert out.session_id == _BIGINT_STR
    assert str(out.id) == str(msg_uuid)


@pytest.mark.unit
def test_chat_response_accepts_bigint_run_id():
    msg = MessageOut(id=uuid4(), session_id=_BIGINT_STR, role="assistant", content="ok")
    resp = ChatResponse(message=msg, run_id=_BIGINT_INT)
    assert resp.run_id == _BIGINT_STR


@pytest.mark.unit
def test_run_list_item_and_detail_accept_bigint_ids():
    item = RunListItem(
        id=_BIGINT_INT,
        agent_id=uuid4(),
        status="completed",
        trigger="chat",
        started_at=datetime.now(timezone.utc),
        parent_run_id=_BIGINT_INT,
    )
    assert item.id == _BIGINT_STR
    assert item.parent_run_id == _BIGINT_STR

    detail = RunDetail(
        id=_BIGINT_INT,
        agent_id=uuid4(),
        status="running",
        trigger="chat",
        started_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        session_id=_BIGINT_INT,
    )
    assert detail.session_id == _BIGINT_STR


@pytest.mark.unit
def test_approval_request_from_row_accepts_bigint_session_and_run():
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": str(uuid4()),  # the row's own PK is still a UUID (mig 198)
        "user_id": str(uuid4()),
        "agent_id": str(uuid4()),
        # These are bigint FKs — wrapping them in UUID() used to crash.
        "session_id": _BIGINT_INT,
        "run_id": _BIGINT_STR,
        "hook_name": "cost_auditor",
        "reason": "spend",
        "payload": {},
        "status": "pending",
        "created_at": now,
        "expires_at": now,
    }
    req = ApprovalRequest.from_row(row)
    assert req.session_id == _BIGINT_STR
    assert req.run_id == _BIGINT_STR


@pytest.mark.unit
def test_approval_request_from_row_handles_null_session_and_run():
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "agent_id": str(uuid4()),
        "session_id": None,
        "run_id": None,
        "hook_name": "h",
        "reason": "r",
        "payload": {},
        "status": "pending",
        "created_at": now,
        "expires_at": now,
    }
    req = ApprovalRequest.from_row(row)
    assert req.session_id is None
    assert req.run_id is None


@pytest.mark.unit
def test_issue_message_accepts_bigint_agent_run_id():
    # model_validate over a raw DB row where agent_run_id is a bigint int.
    msg = IssueMessage.model_validate(
        {
            "id": uuid4(),
            "issue_id": 42,
            "kind": IssueMessageKind.AGENT_RUN.value,
            "agent_run_id": _BIGINT_INT,
            "created_at": datetime.now(timezone.utc),
        }
    )
    assert msg.agent_run_id == _BIGINT_STR


@pytest.mark.unit
def test_issue_message_mapper_keeps_bigint_run_id():
    from app.services.issues.issue_message_mapper import (
        map_ai_message_to_issue_message,
    )

    row = {
        "id": str(uuid4()),
        "role": "assistant",
        "content": "done",
        "agent_id": str(uuid4()),
        # agent_runs.id is a bigint; UUID(str(run_id)) used to crash here.
        "metadata_json": {"run_id": _BIGINT_INT},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    msg = map_ai_message_to_issue_message(row, issue_id=7, session_user_id=None)
    assert msg.agent_run_id == _BIGINT_STR
