"""Source descriptor for Generated-inbox cards (spec §6.1 screen 2, note 3).

Pure. The label is what the card prints under the title; deep_link is where
"open source" goes. Only canvas kinds have a route today — shot/chat kinds get
a label and None, never an invented URL.
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
    row: dict[str, Any], *, canvas_names: dict[str, str], team_id: str
) -> dict[str, Any]:
    kind = str(row.get("origin_kind") or "")
    canvas_id = str(row["canvas_id"]) if row.get("canvas_id") is not None else None
    node_id = str(row["node_id"]) if row.get("node_id") is not None else None
    conv_id = (
        str(row["conversation_id"]) if row.get("conversation_id") is not None else None
    )
    label: str
    deep_link: Optional[str] = None
    shot_id: Optional[str] = None

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
    }
