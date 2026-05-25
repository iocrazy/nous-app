from uuid import UUID

from app.schemas.issue_message import IssueMessageKind
from app.services.issues.issue_message_mapper import map_ai_message_to_issue_message


def test_assistant_row_maps_to_agent_run():
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "role": "assistant",
        "content": "hello",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "metadata_json": {"run_id": "33333333-3333-3333-3333-333333333333"},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.kind == IssueMessageKind.AGENT_RUN
    assert m.body == "hello"
    assert m.author_agent_id == UUID("22222222-2222-2222-2222-222222222222")
    assert m.agent_run_id == UUID("33333333-3333-3333-3333-333333333333")


def test_user_row_maps_to_comment_with_session_user():
    row = {
        "id": "44444444-4444-4444-4444-444444444444",
        "role": "user",
        "content": "hi",
        "metadata_json": {},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    su = UUID("55555555-5555-5555-5555-555555555555")
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=su)
    assert m.kind == IssueMessageKind.COMMENT
    assert m.author_user_id == su
