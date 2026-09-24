"""Response shapes of the script editor's async dispatch routes.

``/scripts/expand-chapter``, ``/scripts/create-branches``,
``/scripts/{id}/chapters/{cid}/convert-to-scenes`` and
``/scripts/import-screenplay`` all answer a FLAT envelope (no ``data`` key,
#1019): the task id is a sibling of ``success``. These declare what the
routers already sent; nothing here changes the wire.
"""

from __future__ import annotations

from pydantic import BaseModel


class ScriptAiTaskDispatch(BaseModel):
    """``{"success": true, "task_id": ...}``: the workflow is queued, poll the
    ``task_tracking`` row."""

    success: bool = True
    task_id: str


class ScriptImportScreenplayDispatch(BaseModel):
    """``POST /scripts/import-screenplay``: the new script's id (a string on
    this route — the handler ``str()``-s it) plus the import task to poll."""

    success: bool = True
    script_id: str
    task_id: str
