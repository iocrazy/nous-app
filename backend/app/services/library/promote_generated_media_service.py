"""Promote a Tier-1 generated_media item into a first-class resources asset."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from pathlib import Path

from loguru import logger

from app.core.config import settings
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.repositories.resources_repository import ResourcesRepository


class PromoteGeneratedMediaService:
    def __init__(self) -> None:
        self.gen_repo = GeneratedMediaRepository()
        self.res_repo = ResourcesRepository()

    async def promote(self, *, gen_id: int, user_id: str, scope_id: int) -> dict:
        gen = await self.gen_repo.get(gen_id, scope_id)
        if not gen:
            raise ValueError("generation not found")

        # Idempotency: already promoted → return the existing resource.
        if gen.get("promoted_resource_id"):
            existing = await self.res_repo.get_resource_by_id(
                str(gen["promoted_resource_id"])
            )
            return existing or {"id": gen["promoted_resource_id"]}

        media_kind = gen.get("media_kind") or "image"
        mime = gen.get("mime") or (
            "video/mp4" if media_kind == "video" else "image/png"
        )
        src_abs = os.path.join(settings.DOWNLOAD_PATH, gen["file_path"])
        ext = os.path.splitext(gen["file_path"])[1] or (
            ".mp4" if media_kind == "video" else ".png"
        )
        filename = f"generated-{media_kind}{ext}"
        size = os.path.getsize(src_abs) if os.path.isfile(src_abs) else 0
        file_hash = (
            await asyncio.to_thread(_sha256, src_abs)
            if os.path.isfile(src_abs)
            else None
        )

        # 1) resource row (source_type='generated' + provenance in metadata)
        resource = await self.res_repo.create_resource(
            {
                "creator_id": user_id,
                "source_type": "generated",
                "filename": filename,
                "file_type": media_kind,
                "mime_type": mime,
                "file_size_bytes": size,
                "current_version": 1,
                "file_hash": file_hash,
                "metadata": {
                    "promoted_from_generated_media_id": str(gen["id"]),
                    "prompt": gen.get("prompt"),
                    "model": gen.get("model"),
                    "provider": gen.get("provider"),
                    "origin_kind": gen.get("origin_kind"),
                },
            }
        )
        resource_id = str(resource["id"])

        # 2) copy file into resources layout
        rel = f"teams/{scope_id}/uploads/{resource_id}/v1/{filename}"
        dst_abs = os.path.join(settings.DOWNLOAD_PATH, rel)
        if os.path.isfile(src_abs):
            Path(dst_abs).parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, src_abs, dst_abs)

        # 3) version row
        await self.res_repo.create_version(
            {
                "resource_id": resource_id,
                "version_number": 1,
                "filename": filename,
                "file_path": rel,
                "file_size_bytes": size,
                "mime_type": mime,
                "uploaded_by": user_id,
                "file_hash": file_hash,
            }
        )

        # 4) scope membership (folder_id None = scope root)
        await self.res_repo.create_resource_item(
            {
                "resource_id": resource_id,
                "scope_id": scope_id,
                "folder_id": None,
                "added_by": user_id,
            }
        )

        # canvas-origin → surface under its canvas
        if gen.get("canvas_id"):
            try:
                from app.db import engine as db_engine

                await db_engine.execute_as_service_role(
                    "INSERT INTO canvas_resource_refs "
                    "(canvas_id, resource_id, role, node_id) "
                    "VALUES (:cid, :rid, 'generated', :nid) "
                    "ON CONFLICT (canvas_id, resource_id, node_id) DO NOTHING",
                    {
                        "cid": int(gen["canvas_id"]),
                        "rid": int(resource_id),
                        "nid": gen.get("node_id") or "",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=True).warning(
                    "[genmedia] canvas_ref on promote failed (non-fatal): {}", exc
                )

        await self.gen_repo.mark_promoted(int(gen["id"]), int(resource_id))
        return resource


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["PromoteGeneratedMediaService"]
