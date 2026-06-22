"""K — register AI-generated media into the Tier-1 generated_media store."""

from __future__ import annotations

import json
import mimetypes
import os
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import aiofiles
import httpx
from loguru import logger

from app.boundary import cap_aiter
from app.core.config import settings
from app.db import engine as db_engine

_DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB ceiling per generation


def media_kind_from_mime(mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("video/"):
        return "video"
    return "image"  # default for images / unknown


def ext_for(mime: str, kind: str) -> str:
    guessed = mimetypes.guess_extension((mime or "").split(";")[0].strip() or "")
    if guessed:
        return guessed
    return ".mp4" if kind == "video" else ".png"


async def _download_to(
    dest_path: str,
    source_url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> int:
    """Stream source_url → dest_path, byte-capped + atomic (.part → os.replace)."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", source_url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(part, "wb") as fp:
                    async for chunk in cap_aiter(resp.aiter_bytes(), max_bytes):
                        await fp.write(chunk)
                        written += len(chunk)
        os.replace(part, dest)
        return written
    except BaseException:
        Path(part).unlink(missing_ok=True)
        logger.opt(exception=True).warning("[genmedia] download failed: {}", source_url)
        raise


@dataclass
class GenerationOrigin:
    kind: str  # 'agent_run' | 'canvas_run'
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    canvas_id: Optional[int] = None
    node_id: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    cost_cents: Optional[float] = None
    parent_resource_id: Optional[int] = None
    derivation_kind: Optional[str] = None


async def register_generated_media(
    *,
    user_id: str,
    scope_id: int,
    source_url: str,
    mime: str,
    origin: GenerationOrigin,
) -> dict:
    """Download a generated media URL into Tier-1 and insert one row. Returns it."""
    kind = media_kind_from_mime(mime)
    gen_uuid = _uuid.uuid4().hex
    rel = f"teams/{scope_id}/generations/{gen_uuid}/media{ext_for(mime, kind)}"
    dest = f"{settings.DOWNLOAD_PATH}/{rel}"
    size = await _download_to(dest, source_url)
    row = await db_engine.execute_returning_one(
        "INSERT INTO public.generated_media "
        "(scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
        " origin_kind, origin_run_id, agent_id, canvas_id, node_id, prompt, model, "
        " provider, params, cost_cents, parent_resource_id, derivation_kind) "
        "VALUES (:scope_id, :creator_id, :media_kind, :mime, :file_path, :file_size_bytes, "
        " :origin_kind, :origin_run_id, :agent_id, :canvas_id, :node_id, :prompt, :model, "
        " :provider, CAST(:params AS jsonb), :cost_cents, :parent_resource_id, :derivation_kind) "
        "RETURNING *",
        {
            "scope_id": scope_id,
            "creator_id": user_id,
            "media_kind": kind,
            "mime": mime,
            "file_path": rel,
            "file_size_bytes": size,
            "origin_kind": origin.kind,
            "origin_run_id": origin.run_id,
            "agent_id": origin.agent_id,
            "canvas_id": origin.canvas_id,
            "node_id": origin.node_id,
            "prompt": origin.prompt,
            "model": origin.model,
            "provider": origin.provider,
            "params": json.dumps(origin.params or {}),
            "cost_cents": origin.cost_cents,
            "parent_resource_id": origin.parent_resource_id,
            "derivation_kind": origin.derivation_kind,
        },
    )
    return row or {}


__all__ = [
    "GenerationOrigin",
    "media_kind_from_mime",
    "ext_for",
    "_download_to",
    "register_generated_media",
]
