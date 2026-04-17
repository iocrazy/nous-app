"""Script export router — GET /scripts/{script_id}/export."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from app.core.deps import AuthDep
from app.services.script_export_service import ScriptExportService
from app.services.script_service import ScriptService

router = APIRouter(prefix="/scripts", tags=["Script Export"])

_VALID_FORMATS = {"txt", "md", "json", "docx"}


@router.get("/{script_id}/export", response_model=None)
async def export_script(
    script_id: str,
    auth: AuthDep,
    format: str = Query(..., description="Export format: txt | md | json | docx"),
    branch_id: Optional[str] = Query(None, description="Branch ID (reserved)"),
):
    """Export a script project in the requested format.

    Supported formats: txt, md, json, docx.
    """
    if format not in _VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Choose one of: {', '.join(sorted(_VALID_FORMATS))}",
        )

    script_service = ScriptService()
    result = await script_service.get_project_full(script_id)
    if not result:
        raise HTTPException(status_code=404, detail="Script not found")

    project: dict = result.get("project", {})
    chapters: list = result.get("chapters", [])
    chapters = sorted(chapters, key=lambda c: c.get("chapter_number", 0))

    logger.info(
        "Script export: script_id=%s, format=%s, chapters=%d",
        script_id,
        format,
        len(chapters),
    )

    export_service = ScriptExportService()
    safe_name = _safe_filename(project.get("name", "script"))

    if format == "txt":
        content = export_service.export_txt(project, chapters)
        return StreamingResponse(
            iter([content.encode("utf-8")]),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.txt"'},
        )

    if format == "md":
        content = export_service.export_markdown(project, chapters)
        return StreamingResponse(
            iter([content.encode("utf-8")]),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
        )

    if format == "json":
        data = export_service.export_json(project, chapters)
        return JSONResponse(
            content=data,
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
        )

    # format == "docx"
    buffer = export_service.export_docx(project, chapters)
    return StreamingResponse(
        buffer,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.docx"'},
    )


def _safe_filename(name: str) -> str:
    """Strip characters unsafe for Content-Disposition filenames."""
    import re

    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    return safe[:100] or "script"
