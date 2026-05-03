"""R3 — memory snapshot + rollback primitive."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.services.memory.snapshot import (
    MemorySnapshotEntry,
    RollbackPlan,
    SnapshotManifest,
    build_manifest,
    deserialize_manifest,
    diff_manifests,
    plan_rollback_to,
    serialize_manifest,
)


def _row(id_, summary="x", status="active", when_to_use="y"):
    return {"id": id_, "summary": summary, "status": status, "when_to_use": when_to_use}


# ─── build_manifest ──────────────────────────────────────────────────


@pytest.mark.unit
def test_build_manifest_freezes_rows():
    m = build_manifest(
        agent_id="a", user_id="u", scope="agent_user",
        rows=[_row("m1"), _row("m2", summary="hello")],
    )
    assert m.namespace_key == "a:u:agent_user"
    assert len(m.entries) == 2
    assert m.entries[0].summary == "x"


@pytest.mark.unit
def test_build_manifest_sorts_by_id():
    """Deterministic order — important for diff stability."""
    m = build_manifest(
        agent_id="a", user_id="u", scope="agent_user",
        rows=[_row("z"), _row("a"), _row("m")],
    )
    ids = [e.id for e in m.entries]
    assert ids == sorted(ids)


@pytest.mark.unit
def test_build_manifest_empty():
    m = build_manifest(
        agent_id="a", user_id="u", scope="agent_user", rows=[],
    )
    assert m.entries == ()


@pytest.mark.unit
def test_build_manifest_content_hash_changes_with_summary():
    m1 = build_manifest(
        agent_id="a", user_id="u", scope="agent_user",
        rows=[_row("m1", summary="A")],
    )
    m2 = build_manifest(
        agent_id="a", user_id="u", scope="agent_user",
        rows=[_row("m1", summary="B")],
    )
    assert m1.entries[0].content_hash != m2.entries[0].content_hash


# ─── active_ids ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_active_ids_excludes_archived():
    m = build_manifest(
        agent_id="a", user_id="u", scope="agent_user",
        rows=[
            _row("m1", status="active"),
            _row("m2", status="archived"),
            _row("m3", status="superseded"),
            _row("m4", status="active"),
        ],
    )
    assert m.active_ids == {"m1", "m4"}


# ─── diff_manifests ──────────────────────────────────────────────────


@pytest.mark.unit
def test_diff_added_removed_status_content():
    before = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[
            _row("kept_same", summary="A"),
            _row("status_flipped", status="active"),
            _row("content_drift", summary="orig"),
            _row("removed_one"),
        ],
    )
    after = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[
            _row("kept_same", summary="A"),
            _row("status_flipped", status="archived"),
            _row("content_drift", summary="modified"),
            _row("brand_new"),
        ],
    )
    diff = diff_manifests(before, after)
    assert diff.added_ids == ("brand_new",)
    assert diff.removed_ids == ("removed_one",)
    assert diff.status_changed_ids == ("status_flipped",)
    assert diff.content_changed_ids == ("content_drift",)


@pytest.mark.unit
def test_diff_identical_returns_all_empty():
    rows = [_row("m1"), _row("m2")]
    a = build_manifest(agent_id="a", user_id="u", scope="x", rows=rows)
    b = build_manifest(agent_id="a", user_id="u", scope="x", rows=rows)
    diff = diff_manifests(a, b)
    assert diff.added_ids == ()
    assert diff.removed_ids == ()
    assert diff.status_changed_ids == ()
    assert diff.content_changed_ids == ()


# ─── plan_rollback_to ────────────────────────────────────────────────


@pytest.mark.unit
def test_plan_restores_archived_active_at_snapshot():
    snap = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[_row("m1"), _row("m2")],  # both active
    )
    cur = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[_row("m1", status="archived"), _row("m2")],  # m1 archived since
    )
    plan = plan_rollback_to(snap, cur)
    assert plan.restore_to_active_ids == ("m1",)
    assert plan.new_since_snapshot_ids == ()
    assert plan.content_drift_ids == ()


@pytest.mark.unit
def test_plan_surfaces_new_since_snapshot():
    """Rows added after snapshot — caller decides whether to delete."""
    snap = build_manifest(
        agent_id="a", user_id="u", scope="x", rows=[_row("m1")],
    )
    cur = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[_row("m1"), _row("m2_new")],
    )
    plan = plan_rollback_to(snap, cur)
    assert "m2_new" in plan.new_since_snapshot_ids
    # Conservative: doesn't auto-delete
    assert plan.restore_to_active_ids == ()


@pytest.mark.unit
def test_plan_skips_fully_deleted_rows():
    """Memory existed in snapshot but completely gone from current —
    can't UPDATE it back; just leave it (caller could rebuild from blob)."""
    snap = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[_row("m1"), _row("m2")],
    )
    cur = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[_row("m1")],  # m2 deleted
    )
    plan = plan_rollback_to(snap, cur)
    # m2 not in restore list — can't be UPDATEd
    assert "m2" not in plan.restore_to_active_ids


# ─── serialize / deserialize round-trip ──────────────────────────────


@pytest.mark.unit
def test_serialize_roundtrip():
    original = build_manifest(
        agent_id="a", user_id="u", scope="x",
        rows=[_row("m1", summary="hello", status="active"), _row("m2", status="archived")],
        captured_at=datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc),
    )
    blob = serialize_manifest(original)
    parsed = deserialize_manifest(blob)
    assert parsed.namespace_key == original.namespace_key
    assert parsed.captured_at == original.captured_at
    assert len(parsed.entries) == 2
    assert parsed.entries[0].id == "m1"


@pytest.mark.unit
def test_deserialize_tolerates_missing_fields():
    """Forward-compat: extra fields ignored, missing optionals default."""
    blob = json.dumps({
        "namespace_key": "n",
        "entries": [{"id": "x"}],
    })
    m = deserialize_manifest(blob)
    assert m.entries[0].id == "x"
    assert m.entries[0].summary == ""
    assert m.entries[0].status == "active"
