# backend/app/services/canvas/asset_refs.py
"""Extract resource references from a canvas's nodes_json.

Pure, DB-free. The source of truth for what canvas_resource_refs should
contain for a given canvas. Mirrors the smart-node shapes defined in
frontend/features/canvas-core/smart/types.ts:
  - shot   node → data.reference_resource_ids[]  (role 'reference')
  - output node → data.resource_id               (role 'output')
prompt/loop nodes hold no resource references.
"""

from __future__ import annotations

from typing import Any, Dict, List


def extract_asset_refs(nodes_json: Any) -> List[Dict[str, str]]:
    """Return ``[{"resource_id", "role", "node_id"}, ...]``.

    Deduped on (node_id, resource_id). Tolerant of malformed entries —
    anything that isn't a well-formed node is skipped rather than raised,
    because this runs on the canvas-save hot path and must never block a
    save (the refs table is rebuildable from a backfill).
    """
    if not isinstance(nodes_json, list):
        return []

    seen: set[tuple[str, str]] = set()
    out: List[Dict[str, str]] = []

    for idx, node in enumerate(nodes_json):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"node_{idx}")
        node_type = str(node.get("type") or "")
        data = node.get("data")
        if not isinstance(data, dict):
            continue

        if node_type == "shot":
            raw = data.get("reference_resource_ids")
            if isinstance(raw, list):
                for rid in raw:
                    _add(out, seen, rid, "reference", node_id)
        elif node_type == "output":
            _add(out, seen, data.get("resource_id"), "output", node_id)

    return out


def _add(out, seen, rid, role, node_id) -> None:
    if rid is None:
        return
    rid_s = str(rid).strip()
    if not rid_s:
        return
    key = (node_id, rid_s)
    if key in seen:
        return
    seen.add(key)
    out.append({"resource_id": rid_s, "role": role, "node_id": node_id})
