"""Pure projections from ``run_deliverables`` rows to the wire shapes.

Both readers share them — the per-issue list and the per-object lineage must
describe a version the same way, or the frontend ends up with two ideas of what
a version is (三期 3a spec §4).

No IO: the repository has already stringified ids, floated ``cost_cents`` and
ISO-formatted ``created_at``. Nothing here re-derives those.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: The version fields that cross the wire. An explicit projection, not
#: ``dict(row)``: the row carries columns (and a joined ``issue_id``) whose
#: membership in the public shape should be a decision, not a leak.
_VERSION_KEYS = (
    "id",
    "version",
    "parent_version",
    "run_id",
    "issue_id",
    "seq",
    "turn",
    "step",
    "title",
    "model",
    "cost_cents",
    "created_at",
)


def version_of(
    row: Dict[str, Any], *, issue_id: Optional[str] = None
) -> Dict[str, Any]:
    """One row → one ``OutputVersion`` dict.

    ``issue_id`` fills in for the per-issue reader, whose rows were selected BY
    an issue and therefore do not carry the join back (spec §3: the table has
    no ``issue_id`` column — ``agent_runs.issue_id`` is the only truth)."""
    out = {key: row.get(key) for key in _VERSION_KEYS}
    if out.get("issue_id") is None and issue_id is not None:
        out["issue_id"] = str(issue_id)
    return out


def group_by_object(
    rows: List[Dict[str, Any]], *, issue_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Group rows into one entry per ``(kind, ref_id)``, versions newest first.

    Object order follows the order objects first appear in ``rows`` (the
    repository already sorts by kind, ref_id, version DESC), so the caller's
    ordering decision is not silently replaced by this function's.

    The entry's ``title`` is the LATEST version's: an old title on a revised
    object names something that no longer exists.
    """
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = (row.get("kind"), str(row.get("ref_id")))
        entry = grouped.setdefault(
            key,
            {"kind": key[0], "ref_id": key[1], "title": None, "versions": []},
        )
        entry["versions"].append(version_of(row, issue_id=issue_id))
    items: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["versions"].sort(key=lambda v: v.get("version") or 0, reverse=True)
        latest = entry["versions"][0]
        entry["latest_version"] = latest.get("version") or 0
        entry["title"] = latest.get("title")
        items.append(entry)
    return items


__all__ = ["group_by_object", "version_of"]
