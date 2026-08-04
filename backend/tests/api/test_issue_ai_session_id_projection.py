"""``Issue.ai_session_id`` reaches the client, str-serialized.

The column has always been on ``public.issues`` and the repository's SELECT *
row carries it, but the ``Issue`` response schema never declared it — so
``response_model=Issue`` filtered it out and no frontend could see it. The A2
timeline's "Open conversation" deep link needs it.

Why str and not int like the sibling BIGINTs (id / team_id / project_id): this
one is only ever pasted into a URL. A JSON number past 2^53 rounds in the
browser, and a rounded snowflake deep-links to a session that does not exist.
"""

from __future__ import annotations

from app.schemas.issue import Issue

_BASE_ROW = {
    "id": 4242,
    "issue_number": 7,
    "identifier": "MH-7",
    "title": "Second act",
    "status": "in_progress",
    "priority": "medium",
    "origin_kind": "manual",
    "origin_fingerprint": "default",
    "created_at": "2026-08-01T10:00:00+00:00",
    "updated_at": "2026-08-01T10:00:00+00:00",
}


def test_ai_session_id_is_str_serialized_from_the_native_bigint():
    """The repository hands back a native int (5.3 parity rule); the schema
    must not let it stay a JSON number."""
    # Past 2^53 — the exact value a float round-trip would corrupt.
    row = {**_BASE_ROW, "ai_session_id": 7300000000000000123}

    issue = Issue.model_validate(row)

    assert issue.ai_session_id == "7300000000000000123"
    assert issue.model_dump()["ai_session_id"] == "7300000000000000123"


def test_ai_session_id_defaults_to_none_before_first_dispatch():
    """An issue that has never been dispatched has no session yet — the field
    must be absent-safe, not required."""
    issue = Issue.model_validate(_BASE_ROW)

    assert issue.ai_session_id is None


def test_ai_session_id_passes_through_when_already_a_str():
    """Idempotent: a caller that already str'd it must not get '7300...' wrapped
    twice or rejected."""
    row = {**_BASE_ROW, "ai_session_id": "7300000000000000123"}

    assert Issue.model_validate(row).ai_session_id == "7300000000000000123"
