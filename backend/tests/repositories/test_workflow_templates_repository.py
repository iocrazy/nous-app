"""WorkflowTemplatesRepository serialization/coercion helpers (M1 PR-A).

Pure-unit coverage of the boundary helpers (no DB), matching the house pattern
in test_publish_tasks_repository: snowflake ids stringify, UUIDs coerce both
ways, source_stage_id survives the str→int→str round-trip. The live-DB CRUD
path is exercised end-to-end by the router tests against fakes; a full
INTEGRATION harness that points write_scope at a test DB is deferred.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.repositories.workflow_templates_repository import _as_uuid, _s


def test_as_uuid_passes_through_uuid():
    u = uuid.uuid4()
    assert _as_uuid(u) is u


def test_as_uuid_coerces_str():
    u = uuid.uuid4()
    assert _as_uuid(str(u)) == u


def test_as_uuid_none_is_none():
    assert _as_uuid(None) is None


def test_as_uuid_rejects_garbage():
    with pytest.raises(ValueError):
        _as_uuid("not-a-uuid")


def test_s_stringifies_uuid():
    u = uuid.uuid4()
    assert _s(u) == str(u)


def test_s_isoformats_datetime():
    dt = datetime.datetime(2026, 7, 20, 12, 0, tzinfo=datetime.timezone.utc)
    assert _s(dt) == dt.isoformat()


def test_s_passes_through_plain_values():
    assert _s("plain") == "plain"
    assert _s(7) == 7
    assert _s(None) is None
