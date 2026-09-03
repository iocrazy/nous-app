# backend/app/services/canvas/asset_node_refs.py
"""Extract ASSET references from a canvas's nodes_json (asset library P4).

Pure, DB-free. The source of truth for what ``canvas_asset_refs`` should
contain for a given canvas — the asset-library twin of
``asset_refs.py``, which despite its name extracts *resource* refs.

TWO node shapes carry an asset binding, and both must be mirrored.

1. The canvas ``asset`` CARD::

    {"type": "asset", "id": "<node_id>",
     "data": {"asset_id": "<snowflake string>",
              "loadout_id": "<snowflake string>" | null, ...}}

2. A ``prompt`` node's inline ``@`` MENTIONS. A mention creates no node by
   design — the chip in the prompt text IS the reference, and at run time it
   is bundled exactly like a wired card::

    {"type": "prompt", "id": "<node_id>",
     "data": {"body": "... @[asset:<id>] ...",
              "mentioned_assets": [{"asset_id": "<snowflake string>",
                                    "name": ..., "asset_type": ...}, ...]}}

   The ref is attributed to the PROMPT node (there is no other node to
   attribute it to) and carries ``loadout_id: None`` — a mention binds no
   outfit, because there is no card to choose one on.

   Reading only shape 1 was a real gap: an asset used on a canvas *only*
   through ``@`` reported as used by zero canvases in ``used_in.canvases``
   and in both reverse-lookup endpoints, with nothing anywhere saying so.

Every other node type is skipped without inspection. In particular a
``prompt`` node's own ``data.asset_id`` (if some future writer adds one) is
NOT read: ``mentioned_assets`` is the only asset binding that node shape
declares, and guessing from a differently-named field would invent refs.

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

import re
from typing import Any, Dict, List, Optional, Tuple

# BIGINT's exclusive upper bound. A value past this parses fine in Python
# and then fails at asyncpg BIND — the same boundary ``within_int64``
# enforces on the request side of /assets.
_INT64_EXCLUSIVE_MAX = 2**63

# ASCII digits ONLY, and never ``str.isdigit()``. ``isdigit()`` is true for
# characters ``int()`` then handles inconsistently: "²" makes ``int()`` RAISE
# (breaking this module's never-raises contract and costing the whole canvas
# its mirror for that save), while Arabic-Indic "١٢٣" parses to 123 — a ref
# for an asset nobody named, reported as ``skipped == 0``. Same shape as
# ``SNOWFLAKE_PATTERN`` on the request side of /assets, so the two boundaries
# agree about what an id looks like.
_ASCII_DIGITS = re.compile(r"^[0-9]{1,20}$")


def extract_asset_node_refs(
    nodes_json: Any,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return ``([{"asset_id": int, "node_id": str, "loadout_id": int|None}], skipped)``.

    ``skipped`` counts bindings that named an asset and could not be read: an
    ``asset`` card whose ``data.asset_id`` was absent or not a usable snowflake,
    and each ``mentioned_assets`` entry of a ``prompt`` node that was not a dict
    or whose ``asset_id`` was not a usable snowflake. A prompt with NO mentions
    is not a malformed anything and is not counted — same rule as a shot or an
    output node.

    Duplicate ``(node_id, asset_id)`` pairs are collapsed to the FIRST
    occurrence and are NOT counted as skipped: they carry no lost information,
    and Postgres would reject a multi-row ``ON CONFLICT DO UPDATE`` that touched
    the same key twice ("cannot affect row a second time"), so the dedup is
    load-bearing rather than cosmetic. The dedup set is SHARED across the two
    node shapes for that reason — a prompt that mentions the same asset twice,
    and a card and a mention that somehow shared a node id, must both collapse.
    """
    if not isinstance(nodes_json, list):
        return [], 0

    seen: set[Tuple[str, int]] = set()
    out: List[Dict[str, Any]] = []
    skipped = 0

    def _emit(node_id: str, asset_id: int, loadout_id: Optional[int]) -> None:
        key = (node_id, asset_id)
        if key in seen:
            return
        seen.add(key)
        out.append({"asset_id": asset_id, "node_id": node_id, "loadout_id": loadout_id})

    for idx, node in enumerate(nodes_json):
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "")
        if node_type not in ("asset", "prompt"):
            continue
        data = node.get("data")
        node_id = str(node.get("id") or f"node_{idx}")

        if node_type == "prompt":
            # A prompt with no mentions is the ordinary case and costs nothing.
            # Only a node that DECLARED a binding it cannot express is counted.
            mentions = data.get("mentioned_assets") if isinstance(data, dict) else None
            if not isinstance(mentions, list):
                continue
            for entry in mentions:
                if not isinstance(entry, dict):
                    skipped += 1
                    continue
                asset_id = _as_snowflake(entry.get("asset_id"))
                if asset_id is None:
                    skipped += 1
                    continue
                # No loadout: a mention has no card to choose one on.
                _emit(node_id, asset_id, None)
            continue

        if not isinstance(data, dict):
            skipped += 1
            continue

        asset_id = _as_snowflake(data.get("asset_id"))
        if asset_id is None:
            skipped += 1
            continue

        _emit(
            node_id,
            asset_id,
            # An unreadable loadout_id degrades to None (the node still
            # references the asset) rather than dropping the whole ref.
            _as_snowflake(data.get("loadout_id")),
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
    * non-ASCII digits — see ``_ASCII_DIGITS``. They are the one input class
      that could make this function RAISE or, worse, quietly resolve to a
      DIFFERENT id than the characters name.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        num = value
    elif isinstance(value, str):
        text = value.strip()
        if not _ASCII_DIGITS.match(text):
            return None
        num = int(text)
    else:
        return None
    if num <= 0 or num >= _INT64_EXCLUSIVE_MAX:
        return None
    return num
