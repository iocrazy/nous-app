"""Admin storage observability — read-only endpoints over the S3-migrated
library. DB is the index (7 sb:// columns); this router exposes stats, per-row
storage status for the media table, and per-media asset detail. Deep S3
verification lives in Task 3 (same file). Read logic is module-level
``_fetch_*`` functions so tests hit them without HTTP/auth plumbing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.admin_deps import AdminAuthDep
from app.db import engine as db_engine

router = APIRouter()


def _fs_cond(col: str) -> str:
    return f"({col} IS NOT NULL AND {col} LIKE '%/%' AND {col} NOT LIKE 'sb://%')"


_FS_RESIDUE_WHERE = " OR ".join(
    [
        _fs_cond("pm.download_path"),
        _fs_cond("pm.cover_download_path"),
        _fs_cond("r.thumbnail_path"),
        _fs_cond("r.cover_image_path"),
        _fs_cond("r.file_path"),
        _fs_cond("rv.hls_path"),
        _fs_cond("rv.file_path"),
    ]
)

# resources.media_id is NOT unique in prod (verified: 2 media_id values have
# 2 matching resources rows each) — a plain LEFT JOIN fans a media row out
# into >1 row for those ids, and _fetch_media_status silently drops all but
# the last one when the frontend collapses by media_id, potentially hiding a
# real fs_residue row behind a later ok-looking one. LATERAL + LIMIT 1 keeps
# it to one row per pm, same deterministic-pick convention as the rv/ri
# LATERALs below.
_JOINS = """
    FROM parsed_media pm
    LEFT JOIN LATERAL (
        SELECT id, thumbnail_path, cover_image_path, file_path FROM resources
        WHERE media_id = pm.id ORDER BY id LIMIT 1
    ) r ON true
    LEFT JOIN LATERAL (
        SELECT hls_path, file_path FROM resource_versions
        WHERE resource_id = r.id ORDER BY id DESC LIMIT 1
    ) rv ON true
"""


async def _fetch_latest_audit() -> dict:
    """Most recent storage_audit run, read from task_tracking (route C: never
    query dbos.workflow_status). Returns the audit dict the /audit endpoint
    serves; status 'none' when no scan has ever run."""
    row = await db_engine.fetch_one(
        """
        SELECT phase, metadata, completed_at FROM task_tracking
        WHERE task_type = 'storage_audit'
        ORDER BY created_at DESC LIMIT 1
        """
    )
    if not row:
        return {
            "status": "none",
            "scanned": 0,
            "errors": 0,
            "scanned_at": None,
            "missing": [],
        }
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        import json

        meta = json.loads(meta or "{}")
    return {
        "status": row.get("phase") or "queued",
        "scanned": int(meta.get("scanned") or 0),
        "errors": int(meta.get("errors") or 0),
        "scanned_at": meta.get("scanned_at"),
        "missing": meta.get("missing") or [],
        "missing_truncated": bool(meta.get("missing_truncated")),
    }


async def _fetch_stats() -> dict:
    videos = await db_engine.fetch_one(
        """
        SELECT count(*) AS count, COALESCE(sum(storage_size),0)::bigint AS size_bytes
        FROM parsed_media WHERE download_path LIKE 'sb://%'
        """
    )
    fs = await db_engine.fetch_one(
        f"SELECT count(*) AS n {_JOINS} WHERE {_FS_RESIDUE_WHERE}"
    )
    orphans = await db_engine.fetch_one(
        """
        SELECT count(*) AS n FROM parsed_media pm
        LEFT JOIN resources r ON r.media_id = pm.id WHERE r.id IS NULL
        """
    )
    hls = await db_engine.fetch_one(
        "SELECT count(*) AS n FROM resource_versions WHERE hls_path LIKE 'sb://%'"
    )
    audit = await _fetch_latest_audit()
    return {
        "videos": {
            "count": int(videos["count"]),
            "size_bytes": int(videos["size_bytes"]),
        },
        "fs_residue": int(fs["n"]),
        "orphans": int(orphans["n"]),
        "hls_ready": int(hls["n"]),
        "broken": len(audit["missing"]) if audit["status"] == "completed" else None,
        "last_scan": (
            {
                "at": audit["scanned_at"],
                "scanned": audit["scanned"],
                "missing": len(audit["missing"]),
                "errors": audit["errors"],
            }
            if audit["status"] == "completed"
            else None
        ),
    }


def _is_sb(v) -> bool:
    return bool(v) and str(v).startswith("sb://")


def _is_fs(v) -> bool:
    return bool(v) and "/" in str(v) and not str(v).startswith("sb://")


async def _fetch_media_status(media_ids: list[int]) -> list[dict]:
    rows = await db_engine.fetch_all(
        f"""
        SELECT pm.id AS media_id, pm.download_path AS video_key,
               pm.storage_size AS video_size, pm.video_download_status,
               pm.cover_download_path AS cover_path,
               r.thumbnail_path, r.cover_image_path AS res_cover_image,
               r.file_path AS res_file_path,
               rv.hls_path, rv.file_path AS rv_file_path, ri.scope_id
        {_JOINS}
        LEFT JOIN LATERAL (
            SELECT scope_id FROM resource_items
            WHERE resource_id = r.id ORDER BY id LIMIT 1
        ) ri ON true
        WHERE pm.id = ANY(:ids)
        """,
        {"ids": media_ids},
    )
    out = []
    for row in rows:
        fs = any(
            _is_fs(row.get(c))
            for c in (
                "video_key",
                "cover_path",
                "thumbnail_path",
                "res_cover_image",
                "res_file_path",
                "hls_path",
                "rv_file_path",
            )
        )
        if fs:
            st = "fs_residue"
        elif not row.get("video_key") or row.get("video_download_status") == "failed":
            st = "no_video"
        else:
            st = "ok"
        out.append(
            {
                "media_id": str(row["media_id"]),
                "video_key": row.get("video_key"),
                "video_size": row.get("video_size"),
                "cover_ok": _is_sb(row.get("cover_path")),
                "thumbnail_ok": _is_sb(row.get("thumbnail_path")),
                "hls_ok": _is_sb(row.get("hls_path")),
                "storage_status": st,
                "scope_id": (
                    str(row["scope_id"]) if row.get("scope_id") is not None else None
                ),
            }
        )
    return out


async def _fetch_media_detail(media_id: int) -> dict:
    row = await db_engine.fetch_one(
        f"""
        SELECT pm.id AS media_id, pm.download_path, pm.storage_size,
               pm.cover_download_path, r.id AS resource_id, r.thumbnail_path,
               rv.hls_path,
               (SELECT scope_id FROM resource_items
                WHERE resource_id = r.id ORDER BY id LIMIT 1) AS scope_id
        {_JOINS}
        WHERE pm.id = :mid
        """,
        {"mid": media_id},
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Media not found"
        )
    rid = row.get("resource_id")
    sprite_key = f"sb://library/derived/{rid}/preview_sprite.jpg" if rid else None
    assets = [
        {
            "kind": "video",
            "key": row.get("download_path"),
            "size_bytes": row.get("storage_size"),
            "present_in_db": _is_sb(row.get("download_path")),
        },
        {
            "kind": "cover",
            "key": row.get("cover_download_path"),
            "size_bytes": None,
            "present_in_db": _is_sb(row.get("cover_download_path")),
        },
        {
            "kind": "thumbnail",
            "key": row.get("thumbnail_path"),
            "size_bytes": None,
            "present_in_db": _is_sb(row.get("thumbnail_path")),
        },
        # sprite has no DB column anywhere — key is the serve-side convention
        {
            "kind": "sprite",
            "key": sprite_key,
            "size_bytes": None,
            "present_in_db": False,
        },
        {
            "kind": "hls",
            "key": row.get("hls_path"),
            "size_bytes": None,
            "present_in_db": _is_sb(row.get("hls_path")),
        },
    ]
    return {
        "assets": assets,
        "scope_id": str(row["scope_id"]) if row.get("scope_id") is not None else None,
    }


@router.get("/stats")
async def get_storage_stats(auth: AdminAuthDep):
    return await _fetch_stats()


@router.get("/media-status")
async def get_media_status(auth: AdminAuthDep, media_ids: str):
    try:
        ids = [int(x) for x in media_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(
            status_code=422, detail="media_ids must be comma-separated ints"
        )
    if not ids or len(ids) > 200:
        raise HTTPException(status_code=422, detail="media_ids must contain 1-200 ids")
    return {"rows": await _fetch_media_status(ids)}


@router.get("/media/{media_id}/detail")
async def get_media_storage_detail(auth: AdminAuthDep, media_id: int):
    return await _fetch_media_detail(media_id)
