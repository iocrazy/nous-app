# backend/app/api/resources_router.py

"""
Resources Router (Main Entry)

Aggregates all resource sub-routers into a single router.
Sub-modules:
- resources_upload_router   — upload, duplicate detection, permissions
- resources_crud_router     — CRUD, file serving, recycle bin, tags, move
- resources_versions_router — version management, HLS, transcode
- resources_folders_router  — folders + smart folders
- resources_assets_router   — save a resource as an asset (P6 ruling E)
"""

from fastapi import APIRouter

from app.api.resources_ai_router import router as ai_router
from app.api.resources_assets_router import router as assets_router
from app.api.resources_crud_router import router as crud_router
from app.api.resources_folders_router import router as folders_router
from app.api.resources_gallery_router import router as gallery_router
from app.api.resources_search_router import router as search_router
from app.api.resources_upload_router import router as upload_router
from app.api.resources_versions_router import router as versions_router

# Main router — prefix is set on each sub-router (/resources)
router = APIRouter()

# IMPORTANT: static-path routers must be registered BEFORE crud_router.
# crud_router owns GET /resources/{resource_id} (a catch-all path param),
# which would otherwise match /resources/search, /resources/smart-folders,
# /resources/folders, etc. and return 404 because "search" is not a real
# resource id. FastAPI matches routes in registration order, so static
# paths must win.
router.include_router(search_router)
router.include_router(upload_router)
router.include_router(folders_router)
router.include_router(versions_router)
router.include_router(ai_router)
# Gallery routes must precede crud_router: crud owns the catch-all
# GET /resources/{resource_id}. POST /resources/galleries and the
# /resources/{id}/gallery-items sub-paths register first as the defensive default.
router.include_router(gallery_router)
# POST /resources/{id}/save-as-asset — a static SUB-path under the same catch-all
# id segment crud_router owns, so it registers first for the same reason the
# routers above do.
router.include_router(assets_router)
router.include_router(crud_router)
