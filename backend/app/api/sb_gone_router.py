"""Legacy storyboard tombstone router (Phase B P4 cutover).

The old ReactFlow storyboard workbench — once served by five ``sb_*_router``
modules (projects / canvas / characters / ai / export, all under the
``/storyboard`` prefix, since deleted) — is retired: the storyboarding surface
now lives inside the script editor as the per-scene shot board
(``script_shots_router``).

Rather than 404 (which reads as "wrong URL / try again"), every retired
``/storyboard/*`` path answers **410 Gone** with a fixed, non-i18n detail so any
stale client learns the surface is intentionally gone, not broken. This is a
catch-all: it stands in for the five unregistered routers without re-listing
each of their paths, and it does NOT shadow the kept routers (``/scripts/...``
script canvas, ``/canvases/...`` infinite-canvas, ``/scenes/.../shots`` — none
sit under ``/storyboard``).

The underlying ``storyboard_*`` tables were rename-deprecated by migration 348
and finally dropped by migration 366; this router keeps the 410 HTTP surface so
stale clients still learn the surface is intentionally gone.
"""

from fastapi import APIRouter, HTTPException

router = APIRouter()

LEGACY_STORYBOARD_GONE_DETAIL = (
    "Legacy storyboard retired — shots now live in the script editor"
)

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/storyboard", methods=_METHODS)
@router.api_route("/storyboard/{rest:path}", methods=_METHODS)
async def legacy_storyboard_gone(rest: str = "") -> None:
    """Tombstone every retired legacy-workbench ``/storyboard/*`` path (410)."""
    raise HTTPException(status_code=410, detail=LEGACY_STORYBOARD_GONE_DETAIL)
