"""Style Templates Router — DEPRECATED, redirects to Skills."""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/style-templates")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def redirect_to_skills(path: str = ""):
    """All style-templates endpoints redirect to /skills."""
    return RedirectResponse(url=f"/api/v1/skills/{path}", status_code=301)


@router.api_route("", methods=["GET", "POST"])
async def redirect_to_skills_root():
    return RedirectResponse(url="/api/v1/skills", status_code=301)
