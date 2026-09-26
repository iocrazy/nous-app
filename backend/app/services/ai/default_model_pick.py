"""Which enabled ``llm`` catalog rows may serve as an IMPLICIT default.

Several paths pick a model nobody named — the canvas "Catalog default", the
scorer health resolver. Taking the first row blindly could land on a local
nous-engine row that is ``idle`` (authorized, not loaded: on 2026-09-24 a real
chat to one got 503 "not loaded") while a loaded one sits further down.

Input rows carry ``status`` as the platform provider view computes it
(``services/ai/platform_provider.platform_rows``: the user's view when there
is a user — their switch and blacklist applied — else the system view; live
engine state for nous-engine rows, the stored probe for the rest; ``fail``
rows and services the engine no longer lists are already gone). Callers:
the canvas Catalog default, the scorer health resolver and the scorer's own
failover pool. One rule, shared:
``ok`` first, then ``not_probed``, then ``idle`` (catalog order within a
rank); anything else (``fail``) is skipped. If nothing qualifies, the rows
come back unchanged — callers keep their old first-row behaviour, a guess
beating no model — and a WARN says so.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from loguru import logger

# Status an implicit default may land on, best first. ``None`` (a row with no
# status at all) ranks with ``not_probed``.
_DEFAULT_STATUS_RANK: Mapping[Optional[str], int] = {
    "ok": 0,
    "not_probed": 1,
    None: 1,
    "idle": 2,
}


def rank_default_candidates(
    rows: List[Dict[str, Any]], *, context: str
) -> List[Dict[str, Any]]:
    """Rows usable as an implicit default, best first (pure, no I/O).

    ``context`` names the caller in the WARN emitted when no row has a usable
    status and the unchanged list is returned as a last resort.
    """
    ranked = sorted(
        (
            (_DEFAULT_STATUS_RANK[row.get("status")], i, row)
            for i, row in enumerate(rows)
            if row.get("status") in _DEFAULT_STATUS_RANK
        ),
        key=lambda t: (t[0], t[1]),
    )
    if ranked:
        return [row for _, _, row in ranked]
    if rows:
        statuses = sorted({str(r.get("status")) for r in rows})
        logger.warning(
            f"{context}: no enabled llm row has a usable status "
            f"(statuses={statuses}); falling back to catalog order"
        )
    return list(rows)
