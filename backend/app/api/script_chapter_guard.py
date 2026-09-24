"""A chapter id supplied in a request body must belong to the script in scope.

Route guards authorize the caller on a script (or on a scene, and through it on
its script). A ``chapter_id`` carried in the body is a second, unguarded id: a
chapter of somebody else's script would ride in under a script the caller does
own. The script-AI workflows write through it (expand overwrites the chapter's
content), and a scene created or moved with it is filed under a foreign
chapter. So every body ``chapter_id`` is checked against the script here.

Both sides are str-coerced: the chapter's ``script_id`` reads back as a native
int while ids arrive as strings (#1006 — an int-vs-str ``!=`` is always true).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE


async def require_chapter_of_script(chapter_id: Any, script_id: Any) -> None:
    """404 ``not_found_or_out_of_scope`` unless ``chapter_id`` is a chapter of
    ``script_id``."""
    from app.services.storyboard.script.script_service import ScriptService

    chapter = await ScriptService().chapter_repo.get_by_id(str(chapter_id))
    if not chapter or str(chapter.get("script_id")) != str(script_id):
        raise HTTPException(
            status_code=404,
            detail={
                "code": NOT_FOUND_OR_OUT_OF_SCOPE,
                "message": "Chapter not found in this script.",
            },
        )
