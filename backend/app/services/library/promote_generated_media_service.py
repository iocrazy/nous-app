"""Promote a Tier-1 generated_media item into a first-class resources asset."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db.session import write_scope
from app.models import CanvasResourceRefs
from app.repositories.conversation_repository import get_conversation_repository
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.services.library.generation_access import can_read_generation_scope
from app.services.library.media_storage import (
    materialize,
    sha256_file,
    store_local_file,
)
from app.services.library.resources_service import _resolve_personal_team_id
from app.services.library.storage_errors import (
    discard_orphan_row,
    object_store_write_failed,
)
from app.services.library.storage_flag import unified_storage_enabled

# generated_media.params keys that ARE generation parameters (the column
# also carries unrelated provenance like cover-frame timestamps / filenames).
_GEN_PARAM_PASSTHROUGH = (
    "seed",
    "steps",
    "cfg",
    "sampler",
    "scheduler",
    "width",
    "height",
    "size",
    "aspect_ratio",
    "denoise",
    "loras",
)


def generation_params_from_generated_media(gen: dict) -> Optional[dict]:
    """Normalise a generated_media row's provenance into ``resources.gen_params``.

    Same dict shape the PNG extractor produces (migration 440) so the detail
    panel renders in-app generations and uploads alike. None when the row
    says nothing about how the image was made — an absent column reads as
    "unknown", an empty dict would read as "known: nothing".
    """
    params: dict = {}
    model = (
        (gen.get("model") or "").strip() if isinstance(gen.get("model"), str) else None
    )
    provider = (
        (gen.get("provider") or "").strip()
        if isinstance(gen.get("provider"), str)
        else None
    )
    if model:
        params["model"] = model[:200]
    if provider:
        params["provider"] = provider[:200]
    raw = gen.get("params")
    if isinstance(raw, dict):
        for key in _GEN_PARAM_PASSTHROUGH:
            value = raw.get(key)
            if value is None or value == "" or value == []:
                continue
            params[key] = value[:200] if isinstance(value, str) else value
        # codex writes ``ratio``; the detail panel renders ``aspect_ratio``.
        # Explicit aspect_ratio (set above) wins over the alias.
        ratio = raw.get("ratio")
        if "aspect_ratio" not in params and isinstance(ratio, str) and ratio:
            params["aspect_ratio"] = ratio[:200]
    if not params:
        return None
    return {"tool": "nous", **params}


class PromoteGeneratedMediaService:
    def __init__(self) -> None:
        self.gen_repo = GeneratedMediaRepository()
        self.res_repo = ResourcesRepository()

    async def _can_read_source_scope(
        self, gen: dict, *, user_id: str, personal_team_id: int, conv_repo
    ) -> bool:
        """Delegates to the shared rule — see ``generation_access``."""
        return await can_read_generation_scope(
            gen,
            user_id=user_id,
            personal_team_id=personal_team_id,
            membership=conv_repo,
        )

    async def promote(
        self, *, gen_id: int, user_id: str, target_scope_id: Optional[int] = None
    ) -> dict:
        """Promote a Tier-1 generation into a Tier-2 resource.

        ``target_scope_id`` defaults to the scope the generation ALREADY lives
        in. `_registration_scope_id` (canvas_generation.py) settled this
        argument on the registration side and its docstring says why: "a team
        board checking its inputs against the team while filing its output in
        one person's private inbox ... Two scopes for one run is not a
        defensible split." Hardcoding the caller's personal team here put that
        split back one layer later — a team board's generation, correctly
        filed in the team's inbox, had its Tier-2 copy yanked into whichever
        member happened to open an editor on it.

        Callers that genuinely choose a destination still pass one: the chat
        attachment endpoint takes ``body.scope_id``, and the inbox service is
        handed the membership-gated ``?scope_id=``. The write-authorisation
        gate below runs against whatever was chosen either way.
        """
        gen = await self.gen_repo.get_by_id(gen_id)
        if gen is None:
            raise ValueError("generation not found")

        conv_repo = get_conversation_repository()
        personal_team_id = int(await _resolve_personal_team_id(user_id))

        # A generation that already IS a resource: hand back the resource,
        # before any source check. Nothing is copied here, so no
        # source-CONVERSATION grant is needed — and demanding one broke every
        # row the chat-upload write path and backfill_generated_inbox create
        # (they set promoted_resource_id and leave conversation_id NULL: a
        # session-less upload has no conversation, and the backfill can never
        # recover one). The scope check below still applies, so a guessed
        # gen_id does not become a resource lookup for an unrelated caller.
        # The target-scope check is deliberately NOT run on this path: it
        # gates writes INTO a scope, and this path writes nothing.
        # AUTHORIZATION NOTE: for an already-promoted row the gate is
        # source-SCOPE membership, not conversation membership — so a team
        # member who never joined the conversation CAN resolve an already-
        # promoted chat attachment. That is intended: the resource lives in
        # that team's library and is already readable by any member through
        # /resources, and this path hands back that same row without copying.
        if gen.get("promoted_resource_id"):
            if not await self._can_read_source_scope(
                gen,
                user_id=user_id,
                personal_team_id=personal_team_id,
                conv_repo=conv_repo,
            ):
                raise PermissionError("not authorised to access this generation")
            existing = await self.res_repo.get_resource_by_id(
                str(gen["promoted_resource_id"])
            )
            return existing or {"id": gen["promoted_resource_id"]}

        if gen.get("origin_kind") == "chat_upload":
            conv_id = gen.get("conversation_id")
            if conv_id is None:
                raise PermissionError("chat_upload generation has no conversation_id")
            if not await conv_repo.is_member(
                conversation_id=int(conv_id), user_id=user_id
            ):
                raise PermissionError("not a member of the source conversation")
        else:
            if gen.get("scope_id") is None:
                raise PermissionError("generation has no source scope")
            if not await self._can_read_source_scope(
                gen,
                user_id=user_id,
                personal_team_id=personal_team_id,
                conv_repo=conv_repo,
            ):
                raise PermissionError("not authorised to access this generation")

        # Resolved AFTER the read gate, so an unreadable generation is refused
        # before its scope can steer anything. Raising on a scope-less row
        # rather than falling back to personal: "we cannot name a destination"
        # and "file it privately" are different answers, and only one of them
        # is honest — same reason `_registration_scope_id` raises.
        if target_scope_id is None:
            if gen.get("scope_id") is None:
                raise ValueError("generation has no scope to promote into")
            target_scope_id = int(gen["scope_id"])

        is_target_personal = personal_team_id == target_scope_id
        is_target_team_member = await conv_repo.is_team_member(
            team_id=target_scope_id, user_id=user_id
        )
        if not is_target_personal and not is_target_team_member:
            raise PermissionError("not authorised to write to target scope")

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
                    # mig 455: a generation's prompt is machine-recorded text.
                    # Hand-rolled rather than `stamp_origin`: that helper
                    # stamps whenever a prompt-text KEY is present in the
                    # patch, which is the right rule for a PATCH (the key is
                    # there because the text changed) and the wrong one for
                    # this INSERT dict, where `gen_prompt` is always present
                    # and may be null. Stamping "extracted" on a promoted
                    # generation that carried no prompt would label an
                    # unprompted row. `test_origin_wiring.py` pins this call
                    # site by its literal for the same reason.
                    "prompt_origin": "extracted" if gen.get("prompt") else None,
                    "gen_params": generation_params_from_generated_media(gen),
                }
            )
            resource_id = str(resource["id"])

            # 2) write the file: dual-track, mirrors the resources upload
            # dual-track write (Task 2.1) — unified storage first; the
            # filesystem copy2 only on flag-off. A storage failure is hard
            # and typed (2026-09-07), and the row from step 1 is discarded.
            stored = None
            if await unified_storage_enabled():
                try:
                    stored = await store_local_file(
                        scope_id=int(target_scope_id),
                        source_path=str(src),
                        mime=mime,
                        filename=filename,
                        sha256=file_hash,
                    )
                except Exception as exc:
                    await discard_orphan_row(
                        self.res_repo.delete_resource,
                        resource_id,
                        where="promote_generated_media",
                    )
                    raise object_store_write_failed(
                        exc,
                        where="promote_generated_media",
                        scope_id=str(target_scope_id),
                        resource_id=resource_id,
                        filename=filename,
                        mime=mime,
                        size_bytes=size,
                    ) from exc
            if stored is not None:
                rel = stored.file_path
            else:
                rel = f"teams/{target_scope_id}/uploads/{resource_id}/v1/{filename}"
                dst_abs = os.path.join(settings.DOWNLOAD_PATH, rel)
                Path(dst_abs).parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, str(src), dst_abs)
            await self.res_repo.update_resource(resource_id, {"file_path": rel})
        finally:
            # Passing (None, None, None) even when we're unwinding an
            # exception is safe: materialize()'s cleanup is an unconditional
            # finally-unlink — it never inspects the exc info, so the temp
            # file is removed either way.
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
                # canvas_resource_refs RLS is locked to service_role (mig 290)
                # — SET LOCAL ROLE first, same pattern as issue_lifecycle.py's
                # service_role-only issues.execution_state writes.
                stmt = pg_insert(CanvasResourceRefs).values(
                    canvas_id=int(gen["canvas_id"]),
                    resource_id=int(resource_id),
                    role="generated",
                    node_id=gen.get("node_id") or "",
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["canvas_id", "resource_id", "node_id"]
                )
                async with write_scope() as session:
                    await session.execute(text("SET LOCAL ROLE service_role"))
                    await session.execute(stmt)
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=True).warning(
                    "[genmedia] canvas_ref on promote failed (non-fatal): {}", exc
                )

        await self.gen_repo.mark_promoted(int(gen["id"]), int(resource_id))
        return resource


__all__ = ["PromoteGeneratedMediaService"]
