from uuid import UUID

from app.schemas.issue_message import IssueMessageKind
from app.services.issues.issue_message_mapper import map_ai_message_to_issue_message


def test_assistant_row_maps_to_agent_run():
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "role": "assistant",
        "content": "hello",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        # agent_runs.id is a BIGINT Snowflake since mig 232 → numeric string.
        "metadata_json": {"run_id": "310819108761487"},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.kind == IssueMessageKind.AGENT_RUN
    assert m.body == "hello"
    assert m.author_agent_id == UUID("22222222-2222-2222-2222-222222222222")
    assert m.agent_run_id == "310819108761487"


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


# ─── attachments projection (3a Task 8a, defect 1) ──────────────────────
#
# The stored shape is what ``output_ref_resolver._stamped`` wrote and
# ``ConversationsAiStore._to_legacy_message_shape`` hands back under
# ``attachments`` — real wire shape, not an idealised one: ``title`` is
# ABSENT (not empty) when the registry row had none, and the reducer drops
# every key whose value is None.


def test_output_ref_attachment_survives_the_read_path():
    """The citation chip has to come back on reload — the whole point of
    copying the title onto the attachment at post time."""
    row = {
        "id": 323848780659604,
        "role": "user",
        "content": "look at this",
        "metadata_json": {},
        "attachments": [
            {
                "kind": "output_ref",
                "ref_kind": "script_shot",
                "ref_id": "337650953731886",
                "version": 1,
                "title": "MEDIUM",
            }
        ],
        "created_at": "2026-09-11T10:58:40.959732+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.attachments is not None
    att = m.attachments[0]
    assert (att.kind, att.ref_kind, att.ref_id, att.version, att.title) == (
        "output_ref",
        "script_shot",
        "337650953731886",
        1,
        "MEDIUM",
    )
    # The wire form the UI actually receives carries all five keys.
    dumped = m.model_dump()["attachments"][0]
    for key in ("kind", "ref_kind", "ref_id", "version", "title"):
        assert key in dumped


def test_output_ref_without_a_title_keeps_the_other_four_keys():
    """``_stamped`` omits ``title`` entirely when the registry row had none."""
    row = {
        "id": 1,
        "role": "user",
        "content": "x",
        "attachments": [
            {
                "kind": "output_ref",
                "ref_kind": "generated_media",
                "ref_id": "9",
                "version": 2,
            }
        ],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.attachments[0].title is None
    assert m.attachments[0].version == 2


def test_resource_and_asset_ref_attachments_keep_their_own_keys():
    """Three stored kinds share one read model; none may lose its identity."""
    row = {
        "id": 2,
        "role": "user",
        "content": "x",
        "attachments": [
            {"kind": "resource_ref", "resource_id": "12345", "name": "clip.mp4"},
            {"kind": "asset_ref", "asset_id": "67890", "loadout_id": "42"},
            {"kind": "image", "mime": "image/png", "alt_text": "a cat"},
        ],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert [a.kind for a in m.attachments] == ["resource_ref", "asset_ref", "image"]
    assert m.attachments[0].resource_id == "12345"
    assert (m.attachments[1].asset_id, m.attachments[1].loadout_id) == ("67890", "42")
    assert m.attachments[2].alt_text == "a cat"


def test_a_row_without_attachments_reads_back_as_null():
    """Chosen convention: absent → ``None``, never ``[]`` — the store itself
    returns ``body.get("attachments") or None``, and a legacy row predating
    the feature must not be dressed up as "had attachments, none left"."""
    row = {
        "id": 3,
        "role": "user",
        "content": "legacy",
        "metadata_json": {},
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.attachments is None
    assert m.model_dump()["attachments"] is None


def test_an_empty_attachment_list_is_also_null():
    row = {
        "id": 4,
        "role": "user",
        "content": "x",
        "attachments": [],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    assert (
        map_ai_message_to_issue_message(
            row, issue_id=5, session_user_id=None
        ).attachments
        is None
    )


def test_a_malformed_attachments_value_does_not_break_the_thread():
    """One bad legacy row must not 500 the whole issue's history."""
    row = {
        "id": 5,
        "role": "user",
        "content": "x",
        "attachments": "not-a-list",
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    assert (
        map_ai_message_to_issue_message(
            row, issue_id=5, session_user_id=None
        ).attachments
        is None
    )


def test_assistant_rows_carry_no_attachments():
    """Only user-role messages store display attachments (the store writes
    them in ``append_user_message`` alone)."""
    row = {
        "id": 6,
        "role": "assistant",
        "content": "done",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "metadata_json": {"run_id": "310819108761487"},
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    assert (
        map_ai_message_to_issue_message(
            row, issue_id=5, session_user_id=None
        ).attachments
        is None
    )
