"""sideload_workflow — register files that are ALREADY on the NAS volume
(million-files P2; design: docs/superpowers/specs/
2026-07-06-million-files-single-user-design.md).

The Eagle-migration path: the user copies a directory tree into
``{DOWNLOAD_PATH}/sideload-inbox/<batch>/`` (same volume as the library),
then one API call registers everything — streaming SHA-256, batch dedup
(duplicates zero-copy LINK, mirroring /resources/link-existing), and for
new files a same-volume rename into the standard upload layout
``teams/{scope}/uploads/{rid}/v1/``. No HTTP byte transfer, no per-file
round trips: throughput is bounded by disk read speed (~hours for 1M files
vs. days of HTTP POSTs).

Discipline (任务系统路线 C):
  - The ROUTER pre-creates the task_tracking row (manager.create with the
    workflow id); phase/status mirror from DBOS via the lifecycle trigger.
  - The workflow PATCHES decoration fields only (progress/subtitle/metadata)
    through the manager API and finishes by returning (SUCCESS → trigger
    marks completed). Hard failures raise — never `return {"failed": ...}`.
  - Steps do I/O; the WORKFLOW BODY loops the batches. The scan step writes
    a manifest FILE (deterministic, on disk) instead of returning a
    million-path list through the DBOS state tables.
  - Thumbnails are NOT chained — the lazy cover path (P1) generates them on
    first view.

Idempotency on replay: a re-run batch re-hashes its files; anything already
registered dedups onto the existing row (find_by_hashes → already-linked
short-circuit). In `move` mode a file whose bytes were already relocated by
the pre-crash attempt is counted `skipped` (its hash lives in the library).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from dbos import DBOS
from loguru import logger

from app.core.config import settings
from app.db.scope import system_request_scope

SIDELOAD_INBOX_DIR = "sideload-inbox"
SIDELOAD_BATCH_SIZE = 500
_HASH_CHUNK = 1024 * 1024


def inbox_root() -> Path:
    return Path(settings.DOWNLOAD_PATH) / SIDELOAD_INBOX_DIR


def _manifest_path(task_id: str) -> Path:
    return inbox_root() / ".manifests" / f"{task_id}.jsonl"


async def _sha256_file(path: Path) -> str:
    def _hash() -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()

    return await asyncio.to_thread(_hash)


@DBOS.step(retries_allowed=False)
async def sideload_scan_step(inbox_rel: str, task_id: str) -> int:
    """Walk the inbox batch (symlinks NOT followed), verify every file stays
    under the inbox root after resolution, and write the manifest file.
    Returns the total file count. Deterministic on replay: rewrites the
    manifest from the (already partially-drained, in move mode) directory —
    the batch step tolerates missing files as `skipped`."""
    root = inbox_root().resolve()
    batch_dir = (inbox_root() / inbox_rel).resolve()
    if not str(batch_dir).startswith(str(root) + os.sep) and batch_dir != root:
        raise ValueError(f"inbox path escapes the sideload inbox: {inbox_rel!r}")
    if not batch_dir.is_dir():
        raise ValueError(f"inbox path is not a directory: {inbox_rel!r}")

    manifest = _manifest_path(task_id)
    manifest.parent.mkdir(parents=True, exist_ok=True)

    def _scan() -> int:
        count = 0
        with open(manifest, "w", encoding="utf-8") as out:
            for dirpath, dirnames, filenames in os.walk(batch_dir, followlinks=False):
                # Never descend into the manifests dir itself.
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in sorted(filenames):
                    if name.startswith("."):
                        continue
                    p = Path(dirpath) / name
                    if p.is_symlink():
                        continue  # symlinks could point outside the volume
                    try:
                        rel = str(p.resolve().relative_to(root))
                    except ValueError:
                        continue  # resolved outside the inbox → refuse
                    out.write(json.dumps({"rel": rel, "size": p.stat().st_size}) + "\n")
                    count += 1
        return count

    total = await asyncio.to_thread(_scan)
    logger.info(f"[sideload] scan {inbox_rel!r}: {total} files → {manifest.name}")
    return total


@DBOS.step(retries_allowed=True, max_attempts=2)
async def sideload_batch_step(
    task_id: str,
    offset: int,
    limit: int,
    user_id: str,
    scope_id: str,
    folder_id: str | None,
    library_id: str | None,
    mode: str,
) -> dict[str, int]:
    """Process manifest rows [offset, offset+limit): hash → batch dedup →
    link duplicates / register new files. Returns per-batch counters."""
    from app.repositories.resources_repository import ResourcesRepository

    manifest = _manifest_path(task_id)
    entries: list[dict[str, Any]] = []
    with open(manifest, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < offset:
                continue
            if i >= offset + limit:
                break
            entries.append(json.loads(line))

    counters = {"processed": 0, "created": 0, "linked": 0, "skipped": 0, "errors": 0}
    if not entries:
        return counters

    root = inbox_root()
    repo = ResourcesRepository()

    # Pass 1: hash everything still present on disk.
    hashed: list[dict[str, Any]] = []
    for entry in entries:
        src = root / entry["rel"]
        if not src.is_file():
            counters["skipped"] += 1  # already moved by a pre-crash attempt
            continue
        try:
            entry["hash"] = await _sha256_file(src)
            entry["src"] = src
            hashed.append(entry)
        except OSError as e:
            logger.warning(f"[sideload] hash failed {entry['rel']}: {e}")
            counters["errors"] += 1

    # Pass 2: one dedup query for the whole batch.
    existing = await repo.find_by_hashes([e["hash"] for e in hashed], user_id)

    import mimetypes

    for entry in hashed:
        src: Path = entry["src"]
        try:
            dup = existing.get(entry["hash"])
            if dup:
                # Zero-copy link (mirrors /resources/link-existing).
                dup_id = str(dup["id"])
                already = await repo.find_resource_item(
                    dup_id, None, scope_id, folder_id
                )
                if not already:
                    await repo.create_resource_item(
                        {
                            "resource_id": dup_id,
                            "scope_id": scope_id,
                            "folder_id": folder_id,
                            "library_id": library_id,
                            "added_by": user_id,
                        }
                    )
                counters["linked"] += 1
                if mode == "move":
                    await asyncio.to_thread(src.unlink)
                counters["processed"] += 1
                continue

            filename = src.name
            mime = mimetypes.guess_type(filename)[0] or ""
            resource = await repo.create_resource(
                {
                    "creator_id": user_id,
                    "source_type": "upload",
                    "filename": filename,
                    "file_type": _classify(mime),
                    "mime_type": mime,
                    "file_size_bytes": entry["size"],
                    "current_version": 1,
                    "file_hash": entry["hash"],
                }
            )
            rid = str(resource["id"])

            if mode == "move":
                save_dir = (
                    Path(settings.DOWNLOAD_PATH)
                    / "teams"
                    / scope_id
                    / "uploads"
                    / rid
                    / "v1"
                )
                save_dir.mkdir(parents=True, exist_ok=True)
                # Same volume → rename, O(1) per file.
                await asyncio.to_thread(shutil.move, str(src), str(save_dir / filename))
                rel_path = f"teams/{scope_id}/uploads/{rid}/v1/{filename}"
            else:  # register in place — read side resolves via the DB column
                rel_path = f"{SIDELOAD_INBOX_DIR}/{entry['rel']}"

            await repo.update_resource(rid, {"file_path": rel_path})
            await repo.create_version(
                {
                    "resource_id": rid,
                    "version_number": 1,
                    "filename": filename,
                    "file_path": rel_path,
                    "file_size_bytes": entry["size"],
                    "mime_type": mime,
                    "uploaded_by": user_id,
                    "file_hash": entry["hash"],
                }
            )
            await repo.create_resource_item(
                {
                    "resource_id": rid,
                    "scope_id": scope_id,
                    "folder_id": folder_id,
                    "library_id": library_id,
                    "added_by": user_id,
                }
            )
            counters["created"] += 1
            counters["processed"] += 1
        except Exception as e:
            logger.warning(f"[sideload] register failed {entry['rel']}: {e}")
            counters["errors"] += 1

    return counters


def _classify(mime: str) -> str:
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith(("application/pdf", "text/")):
        return "document"
    return "other"


@DBOS.workflow()
async def sideload_workflow(
    user_id: str,
    scope_id: str,
    inbox_rel: str,
    folder_id: str | None,
    library_id: str | None,
    mode: str,
    task_id: str,
) -> dict[str, Any]:
    from app.services.infra.unified_task_manager import get_task_manager

    async with system_request_scope(reason="system-sideload"):
        total = await sideload_scan_step(inbox_rel, task_id)
        mgr = get_task_manager()

        agg = {"processed": 0, "created": 0, "linked": 0, "skipped": 0, "errors": 0}
        done = 0
        for offset in range(0, total, SIDELOAD_BATCH_SIZE):
            counters = await sideload_batch_step(
                task_id,
                offset,
                SIDELOAD_BATCH_SIZE,
                user_id,
                scope_id,
                folder_id,
                library_id,
                mode,
            )
            for k, v in counters.items():
                agg[k] = agg.get(k, 0) + v
            done = min(offset + SIDELOAD_BATCH_SIZE, total)
            try:
                await mgr.update_progress(
                    task_id,
                    int(done * 100 / total) if total else 100,
                    subtitle=f"{done}/{total} · +{agg['created']} new, {agg['linked']} linked",
                    metadata_patch={"sideload": agg},
                )
            except Exception as e:  # decoration only — never kill the import
                logger.warning(f"[sideload] progress update failed: {e}")

        # Every single file erroring out is a real failure — raise so DBOS
        # marks ERROR and the trigger surfaces `failed` (never a failed dict).
        if total > 0 and agg["errors"] == total:
            raise RuntimeError(f"sideload failed for all {total} files")

        logger.info(f"[sideload] {inbox_rel!r} done: {agg} (total={total})")
        return {"total": total, **agg}
