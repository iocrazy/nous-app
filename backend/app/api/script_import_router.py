"""Script import router — POST /scripts/import."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger

from app.core.deps import AuthDep
from app.services.script_import_service import ScriptImportService

router = APIRouter(prefix="/scripts", tags=["Script Import"])


@router.post("/import")
async def import_script(
    auth: AuthDep,
    file: UploadFile = File(...),
    script_id: str = Form(...),
) -> dict:
    """Parse an uploaded file and return structured chapter content.

    Accepts TXT, PDF, and DOCX files (max 10 MB).
    Returns a list of chapters extracted from the document.
    """
    file_bytes = await file.read()
    filename = file.filename or ""
    content_type = file.content_type or "application/octet-stream"

    logger.info(
        "Script import request: script_id=%s, filename=%s, content_type=%s, size=%d",
        script_id,
        filename,
        content_type,
        len(file_bytes),
    )

    service = ScriptImportService()

    try:
        service.validate_file(file_bytes, filename, content_type)
        text = service.parse_file(file_bytes, filename, content_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    chapters = service.build_single_chapter(text, filename)

    logger.info(
        "Script import complete: script_id=%s, chapters=%d", script_id, len(chapters)
    )

    return {
        "script_id": script_id,
        "filename": filename,
        "char_count": len(text),
        "chapters": chapters,
    }
