"""Which enabled ``llm`` catalog rows may serve as an IMPLICIT default.

Several paths pick a model nobody named — the canvas "Catalog default", the
scorer health resolver — by walking ``list_enabled("llm")``. Taking the first
row blindly could land on a local nous-engine row that is ``idle`` (authorized,
not loaded: on 2026-09-24 a real chat to one got 503 "not loaded" instead of
loading on demand) or on a ``fail`` row, while the user pickers grey / hide
exactly those rows.

One rule, shared: ``ok`` first, then never-probed / ``not_probed`` (catalog
order within a rank); ``idle`` and ``fail`` are skipped. If nothing qualifies,
the rows come back unchanged — callers keep their old first-row behaviour, a
guess beating no model — and a WARN says so.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from loguru import logger

# Probe statuses an implicit default may land on, best first. Values mirror
# nous_model_health.PROBE_STATUSES; anything else (idle, fail) is skipped.
_DEFAULT_STATUS_RANK: Mapping[Optional[str], int] = {"ok": 0, None: 1, "not_probed": 1}


def rank_default_candidates(
    rows: List[Dict[str, Any]], *, context: str
) -> List[Dict[str, Any]]:
    """Rows usable as an implicit default, best first (pure, no I/O).

    ``context`` names the caller in the WARN emitted when every row is
    ``idle``/``fail`` and the unchanged list is returned as a last resort.
    """
    ranked = sorted(
        (
            (_DEFAULT_STATUS_RANK[row.get("last_test_status")], i, row)
            for i, row in enumerate(rows)
            if row.get("last_test_status") in _DEFAULT_STATUS_RANK
        ),
        key=lambda t: (t[0], t[1]),
    )
    if ranked:
        return [row for _, _, row in ranked]
    if rows:
        statuses = sorted({str(r.get("last_test_status")) for r in rows})
        logger.warning(
            f"{context}: no enabled llm row is ok or unprobed "
            f"(statuses={statuses}); falling back to catalog order"
        )
    return list(rows)
