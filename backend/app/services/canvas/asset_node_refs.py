# backend/app/services/canvas/asset_node_refs.py
"""Extract ASSET references from a canvas's nodes_json (asset library P4).

Pure, DB-free. The source of truth for what ``canvas_asset_refs`` should
contain for a given canvas — the asset-library twin of
``asset_refs.py``, which despite its name extracts *resource* refs.

The node shape this reads is the one the canvas ``asset`` node writes::

    {"type": "asset", "id": "<node_id>",
     "data": {"asset_id": "<snowflake string>",
              "loadout_id": "<snowflake string>" | null, ...}}

Nothing else in ``nodes_json`` carries an asset binding, so every other
node type is skipped without inspection.

WHY IT NEVER RAISES
───────────────────
``nodes_json`` is client-authored JSON stored verbatim; a malformed entry
is a data fact, not a programming error. This runs on the canvas-save hot
path, and ``canvas_asset_refs`` is derived + rebuildable (see
``scripts/backfill_canvas_asset_refs.py``), so a bad node must cost that
one ref, not the user's save. Refs that could not be read are COUNTED and
returned alongside the good ones — a silent drop is exactly the
"silent no-op" CLAUDE.md forbids, and the count is what the caller logs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# BIGINT's exclusive upper bound. A value past this parses fine in Python
# and then fails at asyncpg BIND — the same boundary ``within_int64``
# enforces on the request side of /assets.
_INT64_EXCLUSIVE_MAX = 2**63


def extract_asset_node_refs(
    nodes_json: Any,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return ``([{"asset_id": int, "node_id": str, "loadout_id": int|None}], skipped)``.

    ``skipped`` counts asset nodes whose ``data.asset_id`` was absent or not a
    usable snowflake. Duplicate ``(node_id, asset_id)`` pairs are collapsed to
    the FIRST occurrence and are NOT counted as skipped: they carry no lost
    information, and Postgres would reject a multi-row ``ON CONFLICT DO UPDATE``
    that touched the same key twice ("cannot affect row a second time"), so the
    dedup is load-bearing rather than cosmetic.
    """
    if not isinstance(nodes_json, list):
        return [], 0

    seen: set[Tuple[str, int]] = set()
    out: List[Dict[str, Any]] = []
    skipped = 0

    for idx, node in enumerate(nodes_json):
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "") != "asset":
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            skipped += 1
            continue

        asset_id = _as_snowflake(data.get("asset_id"))
        if asset_id is None:
            skipped += 1
            continue

        node_id = str(node.get("id") or f"node_{idx}")
        key = (node_id, asset_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "asset_id": asset_id,
                "node_id": node_id,
                # An unreadable loadout_id degrades to None (the node still
                # references the asset) rather than dropping the whole ref.
                "loadout_id": _as_snowflake(data.get("loadout_id")),
            }
        )

    return out, skipped


def _as_snowflake(value: Any) -> Optional[int]:
    """A snowflake, or None when the value cannot be one.

    Accepts the wire shape the canvas writes (a digit STRING) and a raw JSON
    number, because ``nodes_json`` is stored verbatim and the two shapes do
    coexist across this repo's routers (CLAUDE.md "边界 mock 必须用真实 JSON 形状").
    Rejects, deliberately:

    * ``bool`` — ``True`` is an ``int`` in Python; treating it as asset 1 would
      invent a reference.
    * ``float`` — a snowflake that arrived as a JSON float has ALREADY lost
      precision, so the id it names is not the id that was meant.
    * anything outside ``[1, 2**63)`` — 0/negatives are not snowflakes, and a
      larger value fails at driver BIND rather than at this boundary.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        num = value
    elif isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            return None
        num = int(text)
    else:
        return None
    if num <= 0 or num >= _INT64_EXCLUSIVE_MAX:
        return None
    return num
