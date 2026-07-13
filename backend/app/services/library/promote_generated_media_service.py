"""Promote a Tier-1 generated_media item into a first-class resources asset."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from loguru import logger

from app.core.config import settings
from app.repositories.conversation_repository import get_conversation_repository
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.services.library.media_storage import (
    materialize,
    sha256_file,
    store_local_file,
)
from app.services.library.resources_service import _resolve_personal_team_id


class PromoteGeneratedMediaService:
    def __init__(self) -> None:
        self.gen_repo = GeneratedMediaRepository()
        self.res_repo = ResourcesRepository()

    async def promote(self, *, gen_id: int, user_id: str, target_scope_id: int) -> dict:
        """Promote a Tier-1 generation into a Tier-2 resource."""
        gen = await self.gen_repo.get_by_id(gen_id)
        if gen is None:
            raise ValueError("generation not found")

        conv_repo = get_conversation_repository()
        personal_team_id = int(await _resolve_personal_team_id(user_id))

        if gen.get("origin_kind") == "chat_upload":
            conv_id = gen.get("conversation_id")
            if conv_id is None:
                raise PermissionError("chat_upload generation has no conversation_id")
            if not await conv_repo.is_member(
                conversation_id=int(conv_id), user_id=user_id
            ):
                raise PermissionError("not a member of the source conversation")
        else:
            gen_scope = gen.get("scope_id")
            if gen_scope is None:
                raise PermissionError("generation has no source scope")
            source_scope_id = int(gen_scope)
            can_read_source = (
                source_scope_id == personal_team_id
                or await conv_repo.is_team_member(
                    team_id=source_scope_id, user_id=user_id
                )
            )
            if not can_read_source:
                raise PermissionError("not authorised to access this generation")

        is_target_personal = personal_team_id == target_scope_id
        is_target_team_member = await conv_repo.is_team_member(
            team_id=target_scope_id, user_id=user_id
        )
        if not is_target_personal and not is_target_team_member:
            raise PermissionError("not authorised to write to target scope")

        if gen.get("promoted_resource_id"):
            existing = await self.res_repo.get_resource_by_id(
                str(gen["promoted_resource_id"])
            )
            return existing or {"id": gen["promoted_resource_id"]}

        media_kind = gen.get("media_kind") or "image"
        mime = gen.get("mime") or (
            "video/mp4" if media_kind == "video" else "image/png"
        )

        # Source bytes come from whichever backend holds the generation —
        # materialize() hides the fs-vs-sb:// split behind a real local
        # path, deleted on exit for sb:// rows. Entry (the download/lookup)
        # is isolated in its own try/except so a missing/unfetchable source
        # always raises the same ValueError regardless of backend; the
        # manual __aenter__/__aexit__ protocol (instead of a plain `async
        # with`) is needed because the materialized path has to stay alive
        # across BOTH the resource-row creation below (needs size/hash
        # first) AND the destination write further down.
        materialized = materialize(gen["file_path"])
        try:
            src = await materialized.__aenter__()
        except Exception as exc:
            raise ValueError("generation file missing") from exc

        try:
            if not src.exists():
                raise ValueError("generation file missing")
            size = src.stat().st_size
            file_hash = await asyncio.to_thread(sha256_file, str(src))
            ext = os.path.splitext(gen["file_path"])[1] or (
                ".mp4" if media_kind == "video" else ".png"
            )
            filename = f"generated-{media_kind}{ext}"

            # 1) resource row. `resources` has NO metadata column (prod
            # schema — inserting one 500s with PGRST204). Provenance stays
            # queryable on the generated_media row itself
            # (prompt/model/provider/origin_kind + the promoted_resource_id
            # backlink written by mark_promoted below); only the prompt is
            # denormalised into the existing gen_prompt column.
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
                    "gen_prompt": gen.get("prompt"),
                }
            )
            resource_id = str(resource["id"])

            # 2) write the file: dual-track, mirrors the resources upload
            # dual-track write (Task 2.1) — unified storage first, fall
            # back to the existing filesystem copy2 on flag-off or any
            # storage failure.
            stored = None
            if settings.FEATURE_UNIFIED_STORAGE:
                try:
                    stored = await store_local_file(
                        scope_id=int(target_scope_id),
                        source_path=str(src),
                        mime=mime,
                        filename=filename,
                        sha256=file_hash,
                    )
                except Exception as exc:
                    logger.warning(
                        f"[promote] unified-storage write failed, falling "
                        f"back to filesystem: scope={target_scope_id} "
                        f"error={exc!r}"
                    )
            if stored is not None:
                rel = stored.file_path
            else:
                rel = f"teams/{target_scope_id}/uploads/{resource_id}/v1/{filename}"
                dst_abs = os.path.join(settings.DOWNLOAD_PATH, rel)
                Path(dst_abs).parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, str(src), dst_abs)
            await self.res_repo.update_resource(resource_id, {"file_path": rel})
        finally:
            await materialized.__aexit__(None, None, None)

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
                "scope_id": target_scope_id,
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


__all__ = ["PromoteGeneratedMediaService"]
