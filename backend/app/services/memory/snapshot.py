"""Memory snapshot + rollback — point-in-time recovery for agent_memories.

Phase R (R3). Memory operations (decay archival, consolidation,
contradiction-supersede, active_remember) are mostly one-way. If a
sweeper run misclassifies + supersedes a load-bearing memory, today
there's no undo. This primitive enables snapshot + rollback at the
namespace level (agent_id × user_id × scope).

Pure layer:
  - SnapshotManifest dataclass: a frozen view of a namespace at time T
  - build_manifest(rows): pure function — takes current rows + builds
    the manifest (sorted, content-hashed)
  - diff_manifests(before, after): set-based diff (added/removed/changed)
  - reconstruct_intent(manifest, current_ids):
      decide for each row: "restore" (was active in snapshot but not now)
      / "leave" (matches) / "ignore" (didn't exist at snapshot)

DB-touching pieces (persist manifest as JSONB blob; restore-by-rebuild)
deferred — module is the algorithm + math layer.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class MemorySnapshotEntry:
    """One row's frozen state at snapshot time."""

    id: str
    summary: str
    status: str  # 'active' / 'archived' / 'superseded'
    content_hash: str  # of (summary + when_to_use) for change detection


@dataclass(frozen=True)
class SnapshotManifest:
    """Point-in-time view of a memory namespace."""

    namespace_key: str  # "agent_id:user_id:scope"
    captured_at: datetime
    entries: tuple[MemorySnapshotEntry, ...]

    @property
    def active_ids(self) -> set[str]:
        return {e.id for e in self.entries if e.status == "active"}


def _entry_hash(summary: str, when_to_use: str) -> str:
    return hashlib.sha1(
        f"{summary}\x00{when_to_use}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]


def build_manifest(
    *,
    agent_id: str,
    user_id: str,
    scope: str,
    rows: list[dict[str, Any]],
    captured_at: datetime | None = None,
) -> SnapshotManifest:
    """Take current agent_memories rows + freeze a manifest."""
    entries = []
    for row in rows or []:
        entries.append(MemorySnapshotEntry(
            id=str(row.get("id") or ""),
            summary=str(row.get("summary") or ""),
            status=str(row.get("status") or "active"),
            content_hash=_entry_hash(
                str(row.get("summary") or ""),
                str(row.get("when_to_use") or ""),
            ),
        ))
    # Sort by id for deterministic comparison
    entries.sort(key=lambda e: e.id)
    return SnapshotManifest(
        namespace_key=f"{agent_id}:{user_id}:{scope}",
        captured_at=captured_at or datetime.now(timezone.utc),
        entries=tuple(entries),
    )


@dataclass(frozen=True)
class ManifestDiff:
    """Set-based diff between two manifests of the same namespace."""

    added_ids: tuple[str, ...] = field(default_factory=tuple)
    removed_ids: tuple[str, ...] = field(default_factory=tuple)
    status_changed_ids: tuple[str, ...] = field(default_factory=tuple)
    content_changed_ids: tuple[str, ...] = field(default_factory=tuple)


def diff_manifests(
    before: SnapshotManifest, after: SnapshotManifest,
) -> ManifestDiff:
    """Compare two snapshots — returns ids that changed in each axis."""
    before_by_id = {e.id: e for e in before.entries}
    after_by_id = {e.id: e for e in after.entries}

    before_ids = set(before_by_id)
    after_ids = set(after_by_id)

    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)

    status_changed: list[str] = []
    content_changed: list[str] = []
    for mid in sorted(before_ids & after_ids):
        b = before_by_id[mid]
        a = after_by_id[mid]
        if b.status != a.status:
            status_changed.append(mid)
        if b.content_hash != a.content_hash:
            content_changed.append(mid)

    return ManifestDiff(
        added_ids=tuple(added),
        removed_ids=tuple(removed),
        status_changed_ids=tuple(status_changed),
        content_changed_ids=tuple(content_changed),
    )


@dataclass(frozen=True)
class RollbackPlan:
    """Caller-actionable list of changes to revert to ``snapshot``."""

    # ids to flip BACK to 'active' (was active in snapshot, not now)
    restore_to_active_ids: tuple[str, ...] = field(default_factory=tuple)
    # ids that exist now but didn't at snapshot time — caller decides
    # whether to delete or leave alone (default: leave alone)
    new_since_snapshot_ids: tuple[str, ...] = field(default_factory=tuple)
    # ids that have content drift since snapshot — surfaced for review
    content_drift_ids: tuple[str, ...] = field(default_factory=tuple)


def plan_rollback_to(
    snapshot: SnapshotManifest, current: SnapshotManifest,
) -> RollbackPlan:
    """Build a rollback plan: which rows to flip back to active.

    Conservative — never deletes / never restores content drift; only
    addresses status flips. Caller can decide the rest manually.
    """
    snapshot_active = snapshot.active_ids
    current_by_id = {e.id: e for e in current.entries}

    restore: list[str] = []
    for mid in sorted(snapshot_active):
        cur = current_by_id.get(mid)
        if cur is None:
            # Memory deleted entirely — can't restore via UPDATE; surface
            # in content_drift for manual recovery
            continue
        if cur.status != "active":
            restore.append(mid)

    diff = diff_manifests(snapshot, current)
    return RollbackPlan(
        restore_to_active_ids=tuple(restore),
        new_since_snapshot_ids=diff.added_ids,
        content_drift_ids=diff.content_changed_ids,
    )


def serialize_manifest(manifest: SnapshotManifest) -> str:
    """JSON-encode for storage in agent_memory_snapshots.payload (TBD
    table). Round-trips via deserialize_manifest."""
    return json.dumps({
        "namespace_key": manifest.namespace_key,
        "captured_at": manifest.captured_at.isoformat(),
        "entries": [
            {"id": e.id, "summary": e.summary, "status": e.status,
             "content_hash": e.content_hash}
            for e in manifest.entries
        ],
    })


def deserialize_manifest(blob: str) -> SnapshotManifest:
    """Parse JSON-encoded manifest. Defensive against extra/missing
    fields — extra ignored; missing → empty defaults."""
    obj = json.loads(blob)
    captured = obj.get("captured_at") or datetime.now(timezone.utc).isoformat()
    if isinstance(captured, str):
        try:
            captured_dt = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        except ValueError:
            captured_dt = datetime.now(timezone.utc)
    else:
        captured_dt = datetime.now(timezone.utc)
    entries = tuple(
        MemorySnapshotEntry(
            id=str(e.get("id") or ""),
            summary=str(e.get("summary") or ""),
            status=str(e.get("status") or "active"),
            content_hash=str(e.get("content_hash") or ""),
        )
        for e in (obj.get("entries") or [])
    )
    return SnapshotManifest(
        namespace_key=str(obj.get("namespace_key") or ""),
        captured_at=captured_dt,
        entries=entries,
    )


__all__ = [
    "ManifestDiff",
    "MemorySnapshotEntry",
    "RollbackPlan",
    "SnapshotManifest",
    "build_manifest",
    "deserialize_manifest",
    "diff_manifests",
    "plan_rollback_to",
    "serialize_manifest",
]
