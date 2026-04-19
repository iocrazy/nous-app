# backend/app/api/resources_router.py

"""
Resources Router (Main Entry)

Aggregates all resource sub-routers into a single router.
Sub-modules:
- resources_upload_router   — upload, duplicate detection, permissions
- resources_crud_router     — CRUD, file serving, recycle bin, tags, move
- resources_versions_router — version management, HLS, transcode
- resources_folders_router  — folders + smart folders
"""

from fastapi import APIRouter

from app.api.resources_crud_router import router as crud_router
from app.api.resources_folders_router import router as folders_router
from app.api.resources_upload_router import router as upload_router
from app.api.resources_versions_router import router as versions_router

# Main router — prefix is set on each sub-router (/resources)
router = APIRouter()

# IMPORTANT: folders_router must be registered BEFORE crud_router.
# crud_router owns GET /resources/{resource_id} (a catch-all path param),
# which would otherwise match /resources/smart-folders, /resources/folders,
# etc. and return 404 because "smart-folders" is not a real resource id.
# FastAPI matches routes in registration order, so static paths must win.
router.include_router(upload_router)
router.include_router(folders_router)
router.include_router(versions_router)
router.include_router(crud_router)
