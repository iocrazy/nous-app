"""A full ``canvases`` row as ``CanvasRepository`` hands it to the router.

Fixtures for the canvas routes used to write only the keys a test cared
about. Once the routes declared ``CanvasRow`` (every column), those partial
rows stopped being valid responses — they were never the real shape. Build
rows from here instead: every ORM column, through the repository's own
``_serialize``, so a column added to ``canvases`` reaches every fixture.
"""

from __future__ import annotations

from typing import Any, Dict

from app.models import Canvases
from app.repositories.canvas_repository import _serialize
from tests.api.wire_parity import sample_row

_LIST_COLUMNS = (
    "nodes_json",
    "connections_json",
    "node_ops_json",
    "connection_ops_json",
)


def repo_canvas_row(**overrides: Any) -> Dict[str, Any]:
    """Every column, repository-shaped (ISO timestamps, native int ids)."""
    row = sample_row(Canvases)
    row["kind"] = "smart"
    row["viewport_json"] = {"x": 1.5, "y": -2, "zoom": 0.75}
    for name in _LIST_COLUMNS:
        row[name] = [{"id": f"{name}-1", "data": {"n": 1}}]
    row.update(overrides)
    return _serialize(row)
