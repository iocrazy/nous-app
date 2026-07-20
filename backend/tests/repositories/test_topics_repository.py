"""TopicsRepository serialization/coercion helpers (M1.5).

Pure-unit coverage of the boundary helpers (no DB), matching the house pattern
in test_workflow_templates_repository: snowflake ids stringify, source ids
survive the str→int→str round-trip, UUIDs coerce. The live-DB CRUD path is
exercised by the router tests against a fake repo.
"""

from __future__ import annotations

import datetime
import types
import uuid

import pytest

from app.repositories.topics_repository import _as_int, _as_uuid, _s, _topic_row


def test_as_int_coerces_snowflake_str():
    assert _as_int("1234567890123456789") == 1234567890123456789


def test_as_int_passes_through_int():
    assert _as_int(42) == 42


def test_as_int_none_is_none():
    assert _as_int(None) is None


def test_as_uuid_coerces_str_and_passes_uuid():
    u = uuid.uuid4()
    assert _as_uuid(str(u)) == u
    assert _as_uuid(u) is u
    assert _as_uuid(None) is None


def test_s_isoformats_datetime_and_stringifies_uuid():
    dt = datetime.datetime(2026, 7, 20, 12, 0, tzinfo=datetime.timezone.utc)
    assert _s(dt) == dt.isoformat()
    u = uuid.uuid4()
    assert _s(u) == str(u)
    assert _s(None) is None
    assert _s("plain") == "plain"


def _fake_topic(**over):
    base = dict(
        id=500,
        team_id=777,
        title="Spring Launch",
        cover_url=None,
        excerpt="An idea",
        status="candidate",
        note_id=None,
        resource_id=None,
        media_id=None,
        inspiration_topic_id=None,
        created_by=None,
        created_at=datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc),
        updated_at=datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc),
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def test_topic_row_stringifies_ids():
    row = _topic_row(_fake_topic())
    assert row["id"] == "500"
    assert row["team_id"] == "777"
    assert row["status"] == "candidate"
    # unset source ids stay None (not the string "None")
    assert row["note_id"] is None
    assert row["resource_id"] is None
    assert row["media_id"] is None
    assert row["inspiration_topic_id"] is None


def test_topic_row_stringifies_source_ids_when_set():
    row = _topic_row(_fake_topic(note_id=111, inspiration_topic_id=222))
    assert row["note_id"] == "111"
    assert row["inspiration_topic_id"] == "222"


def test_topic_row_isoformats_timestamps():
    row = _topic_row(_fake_topic())
    assert row["created_at"].startswith("2026-07-20")
    assert row["updated_at"].startswith("2026-07-20")
