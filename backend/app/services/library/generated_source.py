"""Source descriptor for Generated-inbox cards (spec §6.1 screen 2, note 3).

Pure. The label is what the card prints under the title; deep_link is where
"open source" goes. Canvas kinds route to their canvas; an ``agent_run`` row
routes to the issue that produced it, but ONLY when the caller hands over the
``provenance`` looked up from ``run_deliverables`` (3a). Everything else gets a
label and None, never an invented URL.
"""

from __future__ import annotations

from typing import Any, Optional

_UNTITLED = "Untitled canvas"

# The ``origin_kind`` written when a My Uploads resource is registered so it can
# be saved as an asset (``GeneratedInboxService.save_resource_as_asset``).
# Defined HERE, next to the label that renders it, because the writer and the
# card are the two halves that must not drift: an origin_kind with no arm below
# falls through to ``label = kind`` and prints its own raw column value on the
# card, which reads as a broken card rather than as a missing case.
LIBRARY_UPLOAD_ORIGIN = "library_upload"


def describe_source(
    row: dict[str, Any],
    *,
    canvas_names: dict[str, str],
    team_id: str,
    provenance: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """``provenance`` is the ``run_deliverables`` reverse lookup for THIS row
    (3a): ``{run_id, issue_id, issue_key, agent_name, step}``. ``None`` — the
    normal state for every row generated before the registry existed — keeps
    today's flat label. Only the ``agent_run`` arm reads it: a canvas card's
    source is its canvas, and handing it the run would replace a working deep
    link with a worse one."""
    kind = str(row.get("origin_kind") or "")
    canvas_id = str(row["canvas_id"]) if row.get("canvas_id") is not None else None
    node_id = str(row["node_id"]) if row.get("node_id") is not None else None
    conv_id = (
        str(row["conversation_id"]) if row.get("conversation_id") is not None else None
    )
    label: str
    deep_link: Optional[str] = None
    shot_id: Optional[str] = None
    # Run provenance, only ever filled for the agent_run arm.
    prov = provenance if kind == "agent_run" else None
    issue_id = str(prov["issue_id"]) if prov and prov.get("issue_id") else None
    run_id = str(prov["run_id"]) if prov and prov.get("run_id") else None
    issue_key = (prov or {}).get("issue_key")
    step = (prov or {}).get("step")

    if kind in ("canvas_run", "canvas_upload"):
        name = canvas_names.get(canvas_id or "", _UNTITLED) if canvas_id else _UNTITLED
        label = f"{name} · {'Canvas' if kind == 'canvas_run' else 'Upload'}"
        if canvas_id:
            deep_link = f"/team/{team_id}/canvas/{canvas_id}"
            if node_id:
                deep_link += f"?node={node_id}"
    elif kind in ("shot_generate", "shot_video"):
        shot_id = node_id
        label = f"Storyboard · Shot {node_id or '?'}"
        if kind == "shot_video":
            label += " · Video"
    elif kind == "agent_run":
        # 3a: this arm used to print four words — "Chat generation" — and no
        # link, so "which run made this picture?" was unanswerable in the
        # inbox even though run_deliverables had held the answer all along.
        if prov:
            label = f"{prov.get('agent_name') or 'Agent'} · {issue_key or 'Run'}"
            if issue_key:
                deep_link = f"/team/{team_id}/todolist/{issue_key}"
                if step is not None:
                    deep_link += f"?step={step}"
        else:
            label = "Chat generation"
    elif kind == "chat_upload":
        label = "Chat upload"
    elif kind == LIBRARY_UPLOAD_ORIGIN:
        label = "Library upload"
    else:
        label = kind

    return {
        "kind": kind,
        "label": label,
        "canvas_id": canvas_id,
        "node_id": node_id,
        "shot_id": shot_id,
        "conversation_id": conv_id,
        "deep_link": deep_link,
        "issue_id": issue_id,
        "run_id": run_id,
        "step": step,
    }
