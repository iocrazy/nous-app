"""publish_distribution DBOS workflow — dual-channel publish (PR-D2).

One workflow run = ONE publish batch (publish_tasks row). It iterates the
batch's publish_task_accounts and publishes each via the account's channel:

  - 'official': DouyinAdapter.publish_video → item_id → status='success'
    (+ published_url / platform_item_id / published_at)
  - 'h5': DouyinAdapter.generate_share_url → schema URL, status='pending_share'
    (the user finishes on their phone; the douyin webhook later flips the row
    to 'success' via share_id — see distribution_webhook.py)

路线 C discipline (CLAUDE.md 任务系统架构纪律):
  - task_tracking is the UI's ONLY execution source. This module drives it via
    UnifiedTaskManager.start()/complete()/fail() — never PATCHes phase/status.
  - per-account BUSINESS state lives in publish_task_accounts.status (includes
    the platform-specific 'pending_share'); business code writes it directly.
  - a per-account failure is business state, NOT a workflow failure — the loop
    continues. Only if EVERY account hard-failed (no success/pending_share) does
    the workflow raise → the mirror trigger stamps task_tracking failed (never
    return a failed dict — DBOS would read that as SUCCESS).
"""

from __future__ import annotations

import secrets
from typing import Any, Optional

from dbos import DBOS
from loguru import logger


def decide_channel(task_channel: str, account: dict) -> str:
    """The official open API needs a live access_token; without one we can only
    do the H5 share handoff. Requested 'official' with no token falls back to
    'h5' rather than guaranteeing a failure."""
    if task_channel == "official" and account.get("access_token"):
        return "official"
    return "h5"


def _account_title(account: dict, task: dict) -> tuple[str, Optional[str]]:
    title = account.get("title") or task.get("title") or ""
    description = account.get("description")
    if description is None:
        description = task.get("description")
    return title, description


async def _resolve_video_url(account: dict, task: dict, repo) -> Optional[str]:
    """Resolve the servable video URL for this account. one_to_one carries a
    per-account resource_id; broadcast uses the batch's first resource."""
    rid = account.get("resource_id")
    if not rid:
        ids = task.get("resource_ids") or []
        rid = ids[0] if ids else None
    if not rid:
        return None
    return await repo.get_resource_media_url(int(rid))


async def _publish_one_account(account: dict, adapter, task: dict, repo) -> str:
    """Publish one account and write its business status. Returns the final
    status. Never raises — records 'failed' + error_message instead (a single
    account failing must not abort the whole batch). Extracted from the
    @DBOS.step so it is unit-testable with fakes."""
    account_row_id = int(account["id"])
    channel = decide_channel(account.get("channel", "h5"), account)
    title, description = _account_title(account, task)
    try:
        video_url = await _resolve_video_url(account, task, repo)
        if not video_url:
            raise RuntimeError("no servable media URL for resource")
        if channel == "official":
            item_id = await adapter.publish_video(
                access_token=account["access_token"],
                open_id=account["platform_user_id"],
                video_url=video_url,
                title=title,
                description=description,
            )
            from datetime import datetime, timezone

            await repo.set_account_status(
                account_row_id,
                "success",
                platform_item_id=item_id,
                published_url=f"https://www.douyin.com/video/{item_id}",
                published_at=datetime.now(timezone.utc).isoformat(),
            )
            return "success"
        # H5 share channel
        share_id = secrets.token_urlsafe(16)
        share_title = f"{title} {description}".strip() if description else title
        await adapter.generate_share_url(
            video_url=video_url, title=share_title, share_id=share_id
        )
        await repo.set_account_status(
            account_row_id, "pending_share", share_id=share_id
        )
        return "pending_share"
    except Exception as e:  # noqa: BLE001 — record per-account failure, keep looping
        logger.warning(
            f"[publish] account row {account_row_id} channel={channel} failed: {e}"
        )
        await repo.set_account_status(
            account_row_id, "failed", error_message=str(e)[:500]
        )
        return "failed"


@DBOS.step()
async def mark_publish_processing_step(workflow_id: str) -> None:
    """Push task_tracking.phase queued → processing (mirror trigger only writes
    status, leaving phase stuck at 'queued' otherwise). Best-effort."""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(workflow_id)
    except Exception as e:
        logger.warning(f"[publish.mark_processing] {workflow_id}: {e}")


@DBOS.step()
async def run_publish_accounts_step(task_id: int) -> dict[str, Any]:
    """Load the batch + its accounts (with decrypted tokens) and publish each.
    Returns the per-account final statuses for the workflow to aggregate."""
    from app.repositories.publish_tasks_repository import PublishTasksRepository
    from app.repositories.social_accounts_repository import SocialAccountsRepository
    from app.services.distribution.credentials import get_douyin_credentials
    from app.services.distribution.registry import get_adapter

    repo = PublishTasksRepository()
    accounts_repo = SocialAccountsRepository()
    task = await repo.get_task(task_id)
    if not task:
        raise RuntimeError(f"publish task {task_id} not found")
    rows = await repo.get_task_accounts(task_id)
    if not rows:
        raise RuntimeError(f"publish task {task_id} has no accounts")

    creds = await get_douyin_credentials()
    statuses: list[str] = []
    for row in rows:
        if row.get("status") == "cancelled":
            statuses.append("cancelled")
            continue
        # get_with_tokens returns decrypted access_token for the publish call.
        tokens = await accounts_repo.get_with_tokens(int(row["account_id"])) or {}
        merged = {**row, "access_token": tokens.get("access_token")}
        adapter = get_adapter(row.get("platform", "douyin"), creds)
        statuses.append(await _publish_one_account(merged, adapter, task, repo))
    return {"statuses": statuses}


@DBOS.workflow()
async def publish_distribution_workflow(task_id: int, user_id: str) -> dict[str, Any]:
    """Publish every account in the batch. Completes when at least one account
    reached a settled state (success / pending_share); raises only when every
    account hard-failed (route C — never a failed dict).

    ``publish_tasks`` has NO status/phase column (migration 356: "Never
    duplicate phase/status here" — task_tracking is the sole execution
    source). The aggregated business label from Task 3's
    ``aggregate_task_status`` is therefore recomputed here and stashed on
    ``task_tracking.metadata`` (a legit business decoration field, route C
    rule 3) rather than written back onto publish_tasks; the list/detail
    endpoint recomputes the same label from the live account rows.
    """
    from app.repositories.publish_tasks_repository import aggregate_task_status
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    await mark_publish_processing_step(DBOS.workflow_id)

    try:
        result = await run_publish_accounts_step(task_id)
    except Exception as e:
        await manager.fail(DBOS.workflow_id, f"publish batch errored: {e}")
        raise RuntimeError(f"publish task {task_id} errored: {e}") from e

    statuses = result["statuses"]
    aggregated = aggregate_task_status(statuses)

    if aggregated == "failed":
        # Every account failed / cancelled — surface as a failed task.
        await manager.fail(
            DBOS.workflow_id,
            "all accounts failed to publish",
            metadata_patch={"aggregated_status": aggregated},
        )
        raise RuntimeError(f"publish task {task_id}: all accounts failed")

    pending = sum(1 for s in statuses if s == "pending_share")
    ok = sum(1 for s in statuses if s == "success")
    subtitle = f"{ok} published"
    if pending:
        subtitle += f", {pending} awaiting Douyin"
    await manager.complete(
        DBOS.workflow_id,
        subtitle=subtitle,
        metadata_patch={"aggregated_status": aggregated},
    )
    return {"status": "completed", "task_id": task_id, "statuses": statuses}
