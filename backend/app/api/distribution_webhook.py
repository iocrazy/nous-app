"""Distribution — Douyin webhook + H5 share-schema (PR-D2).

The webhook is NOT behind auth or require_distribution — the Douyin platform
calls it directly. It authenticates the caller by verifying the SHA1 signature
(SHA1(client_secret + raw_body)) against the x-douyin-signature header, so an
unauthenticated flip is impossible without the client_secret.

On a create_video event it finds the publish_task_accounts row by share_id and
flips 'pending_share' → 'success', then re-derives the parent task_tracking
subtitle so the Records page reflects the completion.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.repositories.publish_tasks_repository import (
    PublishTasksRepository,
    aggregate_task_status,
)
from app.services.distribution.credentials import get_douyin_credentials

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/distribution", tags=["Distribution"])
publish_repo = PublishTasksRepository()


def verify_douyin_signature(client_secret: str, body: str, provided: str) -> bool:
    """SHA1(client_secret + raw_body) — the media-router prototype's scheme.
    Constant-time compare to avoid a timing oracle."""
    expected = hashlib.sha1((client_secret + body).encode()).hexdigest()
    return hmac.compare_digest(expected, provided or "")


async def _reaggregate_task_tracking(task_id: str) -> None:
    """Re-derive the parent task's display subtitle after a per-account flip.
    Best-effort — the per-account status (the Records source) is already
    written; this only refreshes the rollup line."""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        accounts = await publish_repo.get_task_accounts(int(task_id))
        rollup = aggregate_task_status([a["status"] for a in accounts])
        task = await publish_repo.get_task(int(task_id))
        wf_id = (task or {}).get("dbos_workflow_id")
        if wf_id:
            await get_task_manager().patch_metadata(wf_id, {"rollup_status": rollup})
    except Exception as e:
        logger.warning(f"[webhook] reaggregate task {task_id} failed: {e}")


@router.post("/webhook/douyin")
async def douyin_webhook(request: Request):
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    creds = await get_douyin_credentials()
    provided = request.headers.get("x-douyin-signature", "")
    if not verify_douyin_signature(creds.client_secret, body_str, provided):
        logger.warning("distribution webhook: signature mismatch")
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = json.loads(body_str) if body_str else {}
    event = data.get("event", "")

    if event == "verify_webhook":
        return {"challenge": data.get("challenge", 0)}

    if event == "create_video":
        content = data.get("content", "{}")
        if isinstance(content, str):
            content = json.loads(content)
        share_id = content.get("share_id", "")
        item_id = content.get("item_id", "")
        if not share_id:
            return {"msg": "ok"}
        acct = await publish_repo.find_task_account_by_share_id(share_id)
        if not acct:
            logger.warning(f"distribution webhook: no account for share_id={share_id}")
            return {"msg": "ok"}
        await publish_repo.set_account_status(
            int(acct["id"]),
            "success",
            platform_item_id=item_id,
            published_url=(
                f"https://www.douyin.com/video/{item_id}" if item_id else None
            ),
            published_at=datetime.now(timezone.utc).isoformat(),
        )
        await _reaggregate_task_tracking(acct["task_id"])
        logger.info(
            f"distribution webhook: share {share_id} → success (item_id={item_id})"
        )

    return {"msg": "ok"}
