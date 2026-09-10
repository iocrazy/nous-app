# backend/app/services/canvas/asset_refs.py
"""Extract RESOURCE references from a canvas's nodes_json.

NAME TRAP: despite the module/function name, this owns ``canvas_resource_refs``
(``resources`` rows). Asset-library refs (``canvas_asset_refs``) live in the
sibling ``asset_node_refs.py``. The name predates the asset library and is
left alone on purpose — renaming it would churn every import for no behaviour
change; this line is the disambiguation instead.

Pure, DB-free. The source of truth for what canvas_resource_refs should
contain for a given canvas. Mirrors the smart-node shapes defined in
frontend/features/canvas-core/smart/types.ts:
  - shot   node → data.reference_resource_ids[]  (role 'reference')
  - output node → data.resource_id               (role 'output')
  - output node → generated-media images it shows, once archived (role 'output')
prompt/loop nodes hold no resource references.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Tuple

# Same pattern as generated_media_service.GENERATED_MEDIA_URL_RE — copied so
# this module stays import-free on the save hot path; a contract test pins
# the two together.
_GENERATED_MEDIA_URL_RE = re.compile(
    r"/generated-media/(\d+)/(?:cover|stream|file)(?=$|[?#])"
)


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


def extract_output_generation_ids(nodes_json: Any) -> List[Tuple[str, int]]:
    """``(node_id, generation id)`` for every generated-media image an OUTPUT
    node shows (``preview_url`` and ``images[].url``), deduped, in order.

    The caller asks which of these have been archived into ``resources``; an
    archived one is as much this canvas's output as a legacy ``resource_id``.
    """
    if not isinstance(nodes_json, list):
        return []
    seen: set[Tuple[str, int]] = set()
    out: List[Tuple[str, int]] = []
    for idx, node in enumerate(nodes_json):
        if not isinstance(node, dict) or str(node.get("type") or "") != "output":
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        node_id = str(node.get("id") or f"node_{idx}")
        urls: List[Any] = [data.get("preview_url")]
        images = data.get("images")
        if isinstance(images, list):
            urls.extend(img.get("url") for img in images if isinstance(img, dict))
        for url in urls:
            if not isinstance(url, str):
                continue
            match = _GENERATED_MEDIA_URL_RE.search(url)
            if not match:
                continue
            key = (node_id, int(match.group(1)))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def merge_promoted_output_refs(
    refs: List[Dict[str, str]],
    pairs: Iterable[Tuple[str, int]],
    promoted: Mapping[int, int],
) -> List[Dict[str, str]]:
    """``refs`` plus one ``output`` ref per shown generation that has a
    ``resources`` copy. Returns a new list; deduped on (node_id, resource_id),
    which is the table's key within one canvas."""
    out = list(refs)
    seen = {(r["node_id"], r["resource_id"]) for r in refs}
    for node_id, gen_id in pairs:
        resource_id = promoted.get(gen_id)
        if resource_id is None:
            continue
        key = (node_id, str(resource_id))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {"resource_id": str(resource_id), "role": "output", "node_id": node_id}
        )
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
