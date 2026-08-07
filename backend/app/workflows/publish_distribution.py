"""publish_distribution DBOS workflow — multi-channel publish (PR-D2 + S3).

One workflow run = ONE publish batch (publish_tasks row). It iterates the
batch's publish_task_accounts and publishes each via the account's channel:

  - 'official': DouyinAdapter.publish_video → item_id → status='success'
    (+ published_url / platform_item_id / published_at)
  - 'h5': DouyinAdapter.generate_share_url → schema URL, status='pending_share'
    (the user finishes on their phone; the douyin webhook later flips the row
    to 'success' via share_id — see distribution_webhook.py)
  - 'session' (spec 2026-08-04 §4.2): SessionAdapter.publish → nous-browser
    drives the platform's own web UI with the account's storage_state →
    status='success' immediately (nothing to wait for — unlike h5 there is no
    second device in the loop). Three obligations ride with it, all in
    ``_settle_session_outcome``: write the refreshed storage_state back, mark
    ONLY genuinely-dead sessions needs_relogin, and never touch account status
    on an infra failure.

路线 C discipline (CLAUDE.md 任务系统架构纪律):
  - task_tracking is the UI's ONLY execution source. This module drives it via
    UnifiedTaskManager.start()/complete()/fail() — never PATCHes phase/status.
  - per-account BUSINESS state lives in publish_task_accounts.status (includes
    the platform-specific 'pending_share'); business code writes it directly.
  - a per-account failure is recorded as business state and the loop keeps
    going to give every OTHER account a chance — but the workflow itself
    raises (never returns a failed dict — DBOS would read that as SUCCESS)
    whenever the batch is NOT a clean success: all-failed, or partial (some
    succeeded, some failed). Raising on partial is deliberate (route-C
    raise-on-partial, see ``classify_batch``): it's what makes the batch
    eligible for retry via ``manager.retry_task`` (only failed/cancelled/lost
    tasks are retryable), powering the Retry button on partial batches.
  - a retry re-dispatch of the SAME task_id must not re-publish rows that
    already settled (success / pending_share) — see the idempotency guard in
    ``_run_accounts``.
"""

from __future__ import annotations

import secrets
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

_SETTLED = {"success", "pending_share"}


def classify_batch(statuses: list[str]) -> str:
    """Pure batch outcome for the workflow: 'all_failed' (raise) | 'partial'
    (raise, some failed) | 'ok' (complete). Extracted so the completion
    decision is unit-testable without the DBOS runtime."""
    settled = [s for s in statuses if s in _SETTLED]
    if not settled:
        return "all_failed"
    if any(s == "failed" for s in statuses):
        return "partial"
    return "ok"


def decide_channel(task_channel: str, account: dict) -> str:
    """Resolve which channel this row actually publishes through.

    'session' (spec §4.2) requires the account to be session-bound — the
    browser channel's whole credential IS the account's storage_state, so a
    row asking for 'session' on an OAuth account has nothing to drive a browser
    with. It degrades to 'h5' by the same logic 'official' does: the share
    handoff is the one path that needs no per-account credential at all.

    The official open API needs a live access_token; without one we can only do
    the H5 share handoff. Requested 'official' with no token falls back to 'h5'
    rather than guaranteeing a failure."""
    if task_channel == "session" and account.get("auth_type") == "session":
        return "session"
    if task_channel == "official" and account.get("access_token"):
        return "official"
    return "h5"


def visibility_to_private_status(visibility: Optional[str]) -> int:
    """Map the task's ``visibility`` to Douyin's ``private_status`` enum:
    public→0 (everyone), private→1 (self only), friends→2 (friends). Unknown
    values fall back to public (0). Same enum for both the official create API
    and the H5 share schema, so a single mapping is correct here."""
    return {"public": 0, "private": 1, "friends": 2}.get(visibility or "public", 0)


def _account_publish_opts(account: dict, task: dict) -> dict:
    """Resolve the per-account publish options for one row: title/description
    and topics (account override → batch default) plus the batch-level
    visibility and download toggles decoded into what the adapter needs.

    Returns keys: title, description, topics (list[str], Douyin hashtags),
    private_status (int), allow_download (bool). Extracted so it is
    unit-testable without the DBOS runtime."""
    title = account.get("title") or task.get("title") or ""
    description = account.get("description")
    if description is None:
        description = task.get("description")
    allow_download = task.get("allow_download")
    if allow_download is None:
        allow_download = True
    topics = account.get("topics")
    if topics is None:
        topics = task.get("topics")
    return {
        "title": title,
        "description": description,
        "topics": list(topics or []),
        "private_status": visibility_to_private_status(task.get("visibility")),
        "allow_download": bool(allow_download),
    }


def unsupported_options(task: dict, channel: str) -> list[str]:
    """本次批次里 ``channel`` 这条通道**接不住**的表单字段。

    定时发布 / 自主声明 / 合集全都是"在创作页上操作"的能力，只有会话通道
    （浏览器真的在页面上点）能做到。official 走开放平台 create API（无这些
    参数），h5 把内容甩给用户手机上的抖音 App（我们连页面都碰不到）。

    返回非空时调用方**让这一行失败**，而不是把字段丢掉照发。理由与浏览器侧
    对 ``scheduled_at`` 的处理一致：一条本该十二小时后发出、却立刻发出去的
    作品，不是一个更小的失败；少了自主声明的作品更是合规问题。silent no-op
    在这条路径上不可接受（CLAUDE.md「触发路径必须类型化失败回显」）。

    纯函数，可单测。
    """
    if channel == "session":
        return []
    unsupported: list[str] = []
    if task.get("scheduled_at"):
        unsupported.append("scheduled publishing")
    if task.get("self_declaration"):
        unsupported.append("self declaration")
    if (task.get("collection_name") or "").strip():
        unsupported.append("collection")
    return unsupported


def _title_with_hashtags(title: str, topics: list[str]) -> str:
    """Append topics to the official-post title as Douyin hashtags. The create
    API has no dedicated hashtag field — topics ride in the post text as
    ``#word `` (hash + word + trailing space, which terminates the tag).
    Description is still newline-joined downstream by _create_video_post."""
    if not topics:
        return title
    tags = "".join(f"#{t} " for t in topics)
    return f"{title} {tags}"


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


async def _resolve_image_urls(task: dict, repo) -> list[str]:
    """Resolve the ordered servable URLs for every image in an images batch.
    The batch's ``resource_ids`` order IS the gallery order (never reordered).

    Unlike video one_to_one (which round-robins a resource per account), an
    images task is always ONE note carrying ALL images — we do not split a
    gallery across accounts, so every account posts the full ordered set from
    the batch (see the ``images`` branch in ``_publish_one_account``). Raises
    if any resource id has no servable URL: a note is an all-or-nothing gallery,
    so publishing a silently-truncated set would be worse than failing loud."""
    ids = task.get("resource_ids") or []
    urls: list[str] = []
    for rid in ids:
        if not rid:
            continue
        url = await repo.get_resource_media_url(int(rid))
        if not url:
            raise RuntimeError(f"no servable media URL for resource {rid}")
        urls.append(url)
    return urls


async def _publish_one_account(account: dict, adapter, task: dict, repo) -> str:
    """Publish one account and write its business status. Returns the final
    status. Never raises — records 'failed' + error_message instead (a single
    account failing must not abort the whole batch). Extracted from the
    @DBOS.step so it is unit-testable with fakes."""
    account_row_id = int(account["id"])
    channel = decide_channel(account.get("channel", "h5"), account)
    content_type = task.get("content_type") or "video"
    opts = _account_publish_opts(account, task)
    title = opts["title"]
    description = opts["description"]
    topics = opts["topics"]
    private_status = opts["private_status"]
    allow_download = opts["allow_download"]
    try:
        blocked = unsupported_options(task, channel)
        if blocked:
            # 这一行本来就没法兑现用户填的东西 —— 说清楚，别偷偷少发一半。
            raise RuntimeError(
                f"{', '.join(blocked)} needs an account connected by QR code "
                f"(this one publishes via {channel})"
            )
        if content_type == "images":
            # Images publish only through the H5 note handoff for now. The
            # official create API has no image-post path yet, so an account
            # that actually resolved to 'official' (has a live token) fails as
            # BUSINESS state — the loop keeps going for the other accounts.
            if channel == "official":
                raise RuntimeError("images not supported on official channel yet")
            image_urls = await _resolve_image_urls(task, repo)
            if not image_urls:
                raise RuntimeError("no servable media URL for resource")
            share_id = secrets.token_urlsafe(16)
            share_title = f"{title} {description}".strip() if description else title
            await adapter.generate_image_share_url(
                image_urls=image_urls,
                title=share_title,
                share_id=share_id,
                hashtags=topics,
                private_status=private_status,
                allow_download=allow_download,
            )
            await repo.set_account_status(
                account_row_id, "pending_share", share_id=share_id
            )
            return "pending_share"
        video_url = await _resolve_video_url(account, task, repo)
        if not video_url:
            raise RuntimeError("no servable media URL for resource")
        if channel == "official":
            # Official create API download_type: 0=allowed, 1=not allowed
            # (distinct from the H5 schema's 1/2 — see douyin_adapter).
            official_download_type = 0 if allow_download else 1
            item_id = await adapter.publish_video(
                access_token=account["access_token"],
                open_id=account["platform_user_id"],
                video_url=video_url,
                # topics ride in the post text (`#tag `) — no hashtag field.
                title=_title_with_hashtags(title, topics),
                description=description,
                private_status=private_status,
                download_type=official_download_type,
            )
            from datetime import datetime, timezone

            await repo.set_account_status(
                account_row_id,
                "success",
                platform_item_id=item_id,
                published_url=f"https://www.douyin.com/video/{item_id}",
                # datetime OBJECT — this reaches a raw asyncpg $N bind
                # (publish_tasks_repository.set_account_status), which
                # refuses ISO strings for timestamptz.
                published_at=datetime.now(timezone.utc),
            )
            return "success"
        # H5 share channel — topics go through the dedicated hashtag_list param
        # (JsonArray); generate_share_url maps allow_download → the H5
        # download_type enum (1/2) itself.
        share_id = secrets.token_urlsafe(16)
        share_title = f"{title} {description}".strip() if description else title
        await adapter.generate_share_url(
            video_url=video_url,
            title=share_title,
            share_id=share_id,
            hashtags=topics,
            private_status=private_status,
            allow_download=allow_download,
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


# ── session channel (spec §4.2) ───────────────────────────────────────────


def _media_filename(url: str) -> str:
    """Filename for a servable media URL — what fail-fast checks the extension
    of (§7.7). Derived from the URL path because that is what
    ``get_resource_media_url`` hands back for BOTH shapes it produces (the
    signed ``/media/<rel_path>`` URL and the object-store signed URL keep the
    real path, extension included); asking the DB for ``resources.filename``
    separately would be a second round-trip that can disagree with the URL
    actually being published."""
    from urllib.parse import unquote, urlparse

    return unquote(urlparse(url).path).rsplit("/", 1)[-1] or "media"


async def _build_publish_intent(account: dict, task: dict, repo):
    """Batch row + account row → a platform-agnostic ``PublishIntent``.

    Media URLs come from ``repo.get_resource_media_url`` — the SAME entry point
    official/h5 use (spec §8 item 5, measured reachable from the browser
    container). No new URL-signing logic: a second signer would drift from this
    one's TTL and auth semantics, and the drift would only show up as an
    expired link mid-upload.

    ``visibility`` passes through as the SEMANTIC word, not Douyin's
    ``private_status`` int — translating to platform-native values is the
    browser-side uploader's job (§6.1 a/b).

    ``scheduled_at`` IS forwarded now (it used to be deliberately dropped while
    the column had no writer): the schedule is set in the platform's own creator
    page by the browser, so there is no dispatcher on our side to build — the
    2h..14d window is the platform's, and it is checked in three places before
    a browser ever opens (request schema → this intent's fail-fast → the form).

    ``self_declaration`` / ``collection`` ride in ``platform_options`` because
    they are platform-native controls with no channel-level meaning. The
    declaration is RESOLVED here rather than read straight off the column —
    ``ai_content`` alone must still produce a real click on the page (see
    ``resolve_self_declaration``); leaving that mapping to the browser would
    put a product decision in the DOM layer where nobody would find it.
    """
    from app.services.distribution.publish_options import resolve_self_declaration
    from app.services.distribution.session_adapter import PublishIntent, PublishMedia

    opts = _account_publish_opts(account, task)
    content_type = task.get("content_type") or "video"
    if content_type == "images":
        urls = await _resolve_image_urls(task, repo)
        if not urls:
            raise RuntimeError("no servable media URL for resource")
        media = tuple(
            PublishMedia(kind="image", url=u, filename=_media_filename(u)) for u in urls
        )
    else:
        url = await _resolve_video_url(account, task, repo)
        if not url:
            raise RuntimeError("no servable media URL for resource")
        media = (PublishMedia(kind="video", url=url, filename=_media_filename(url)),)

    cover = None
    cover_id = task.get("cover_vertical_resource_id") or task.get(
        "cover_horizontal_resource_id"
    )
    if cover_id:
        cover_url = await repo.get_resource_media_url(int(cover_id))
        if cover_url:
            cover = PublishMedia(
                kind="image", url=cover_url, filename=_media_filename(cover_url)
            )

    # platform_options 只放**真的有值**的键：None 与"键不存在"在浏览器侧语义
    # 不同（不碰控件 vs 显式设置），送一个 None 进去等于让对面去猜。
    platform_options: dict[str, Any] = {}
    declaration = resolve_self_declaration(
        ai_content=bool(task.get("ai_content")),
        self_declaration=task.get("self_declaration"),
    )
    if declaration:
        platform_options["self_declaration"] = declaration
    collection = (task.get("collection_name") or "").strip()
    if collection:
        platform_options["collection"] = collection

    return PublishIntent(
        content_type=content_type,
        media=media,
        title=opts["title"],
        description=opts["description"],
        topics=tuple(opts["topics"]),
        visibility=task.get("visibility") or "public",
        allow_download=opts["allow_download"],
        cover=cover,
        scheduled_at=task.get("scheduled_at"),
        platform_options=platform_options,
    )


#: browser 侧 ``detail["collection"]`` 里表示"发布成功了，但合集没挂上"的三个值
#: （另外两个是 ``applied`` / ``not_requested``，都不需要回显）。
#: 合集在浏览器侧是**类型化降级**而不是失败（跨服务约定 2026-08-06）：视频这时
#: 已经传完，为了一个可以事后在平台后台补挂的归档字段而把作品废掉，代价不对称。
#: 但降级必须**看得见** —— 不读这三个值，它就退化成 CLAUDE.md 明令禁止的
#: silent no-op。与自主声明相反：那个是合规、上线后撤不回，浏览器侧任何一步
#: 失败都直接 raise。
_COLLECTION_DEGRADED = {"not_found", "control_missing", "error"}


def collection_note(detail: dict) -> Optional[str]:
    """browser 的 ``detail`` → 一句给用户看的"发了，但合集没挂上"。纯函数。

    ``None`` = 合集挂上了、或者本来就没要求合集。
    """
    state = (detail or {}).get("collection")
    if state not in _COLLECTION_DEGRADED:
        return None
    requested = (detail or {}).get("collection_requested")
    named = f" '{requested}'" if requested else ""
    return f"[collection_{state}] published, but the collection{named} was not applied"


async def _settle_session_outcome(
    outcome, *, account_row_id: int, account_id: int, repo, accounts_repo
) -> str:
    """Persist everything one session publish produced, return the row status.

    Three writes, in this order, each with its own reason for existing:

    1. **storage_state write-back** (spec §4.2 step 6) whenever the browser
       returned a fresh one — including on a FAILED publish, because the
       platform rotates cookies on use regardless of whether the post landed.
       Skipping it spends the original session's remaining life instead of
       extending it: the difference between rescanning a QR code fortnightly
       and quarterly.
    2. **account status** — ``session_invalid`` means the account genuinely
       needs a rescan. But an INFRA failure (container down, token not
       configured, decrypt failed) means we never got an answer at all, and
       must leave account status alone: one container outage would otherwise
       mark every account in the batch as logged-out and make users rescan a
       hundred healthy sessions (§7.8).
    3. **publish row business status** — route C: business state lives in
       ``publish_task_accounts.status``, never in the DBOS phase columns.
    """
    import json

    from app.services.distribution.browser_client import SessionStatus, is_infra_failure

    result = outcome.result.to_dict()
    status = result["status"]
    infra = is_infra_failure(result)

    if outcome.updated_storage_state:
        await accounts_repo.update_session_state(
            account_id,
            json.dumps(outcome.updated_storage_state, ensure_ascii=False),
        )

    if status == SessionStatus.SESSION_INVALID.value and not infra:
        logger.warning(
            f"[publish.session] account {account_id} session died — needs_relogin"
        )
        await accounts_repo.mark_needs_relogin(account_id)

    detail = result.get("detail") or {}

    if status == SessionStatus.PUBLISHED.value:
        from datetime import datetime, timezone

        await repo.set_account_status(
            account_row_id,
            "success",
            platform_item_id=outcome.platform_item_id,
            published_url=outcome.published_url,
            # datetime OBJECT — same binding constraint as the official branch.
            published_at=datetime.now(timezone.utc),
            # 成功行上写 error_message 看着别扭，但这一列**是** UI 唯一能显示
            # 的自由文本。合集没挂上不该把整行标 failed（作品真的发出去了），
            # 也不该无声无息 —— 记录页把 success + 非空 error_message 渲染成
            # 一条提示而不是错误。
            error_message=collection_note(detail),
        )
        return "success"

    # Why it failed must survive into the row: the UI's only handle on a failed
    # account is this string, and 'proxy_failed' vs 'session_invalid' vs
    # 'unreachable' lead the user to three different actions.
    cause = detail.get("error_kind") or detail.get("reason") or status
    message = result.get("message") or status
    await repo.set_account_status(
        account_row_id,
        "failed",
        error_message=f"[{cause}] {message}"[:500],
    )
    return "failed"


async def _publish_one_account_session(
    account: dict, task: dict, repo, accounts_repo, *, adapter=None, lock=None
) -> str:
    """Publish one account through the browser session channel.

    Never raises (same contract as ``_publish_one_account``): one account's
    failure is business state, and the loop must give every OTHER account its
    chance. The workflow body still raises on a non-clean batch (§7.3).

    Ordering is deliberate — everything that can fail cheaply happens BEFORE
    the lock and the browser:

        decrypt check → build intent (resolve media URLs) → fail-fast validate
        → **acquire account lock** → publish → settle → release

    ``validate_publish_intent`` before the lock is §7.7: opening a headed
    browser and pushing a few hundred MB takes minutes, and a too-long title
    must not cost that. Holding the lock for only the browser call keeps the
    idle-in-transaction window (see ``session_lock``) as short as the work
    allows.
    """
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
    )
    from app.services.distribution.registry import get_session_adapter
    from app.services.distribution.session_adapter import (
        PublishOutcome,
        SessionOpResult,
        decrypt_failure_result,
    )
    from app.services.distribution.session_lock import account_session_lock

    account_row_id = int(account["id"])
    account_id = int(account["account_id"])
    lock = lock or account_session_lock
    try:
        if account.get(SESSION_STATE_DECRYPT_FAILED):
            # Ciphertext was there and would not open. INFRA failure, so
            # _settle leaves account status alone — the platform session is
            # probably fine, our key is not.
            outcome = PublishOutcome(
                result=SessionOpResult(
                    **decrypt_failure_result(
                        "session_state could not be decrypted",
                        account_id=account_id,
                    )
                )
            )
            return await _settle_session_outcome(
                outcome,
                account_row_id=account_row_id,
                account_id=account_id,
                repo=repo,
                accounts_repo=accounts_repo,
            )

        adapter = adapter or get_session_adapter(account.get("platform", "douyin"))
        intent = await _build_publish_intent(account, task, repo)
        problems = adapter.validate_publish_intent(intent)
        if problems:
            raise RuntimeError("publish intent rejected: " + "; ".join(problems))

        async with lock(account_id) as acquired:
            if not acquired:
                # Another browser session holds this account. Two contexts on
                # one account get each other kicked out (§7.5) — failing this
                # row is strictly better than burning the session.
                raise RuntimeError(
                    "another browser session is already running for this account"
                )
            outcome = await adapter.publish(account, intent)
            return await _settle_session_outcome(
                outcome,
                account_row_id=account_row_id,
                account_id=account_id,
                repo=repo,
                accounts_repo=accounts_repo,
            )
    except Exception as e:  # noqa: BLE001 — per-account failure, keep looping
        logger.warning(f"[publish.session] account row {account_row_id} failed: {e}")
        await repo.set_account_status(
            account_row_id, "failed", error_message=str(e)[:500]
        )
        return "failed"
    finally:
        # Belt and braces against a credential ever outliving this frame: the
        # plaintext storage_state lives in this dict for the length of one
        # publish and nowhere else (§7.6 — never on disk, never in a log, never
        # in a DBOS step's input/output).
        account.pop("session_state", None)


@DBOS.step()
async def mark_publish_processing_step(
    workflow_id: str, user_id: str | None = None
) -> None:
    """Push task_tracking.phase queued → processing (mirror trigger only writes
    status, leaving phase stuck at 'queued' otherwise). Best-effort.

    ``user_id`` is threaded through so ``start()``'s self-heal can rebuild a
    missing row — ``task_tracking.user_id`` is UUID NOT NULL, and start()'s
    fallback of ``""`` can never satisfy it."""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(
            workflow_id, user_id=user_id, task_type="publish_distribution"
        )
    except Exception as e:
        logger.warning(f"[publish.mark_processing] {workflow_id}: {e}")


@DBOS.step()
async def run_publish_accounts_step(task_id: int, user_id: str) -> dict[str, Any]:
    """Load the batch + its accounts (with decrypted tokens) and publish each.
    Returns the per-account final statuses for the workflow to aggregate.

    ``user_id`` exists only to establish the ambient scope (see below); the
    batch's own rows are found by ``task_id``.
    """
    from app.db.scope import Scope, request_scope
    from app.repositories.publish_tasks_repository import PublishTasksRepository
    from app.repositories.social_accounts_repository import SocialAccountsRepository
    from app.services.distribution.credentials import get_douyin_credentials
    from app.services.workflow_heartbeat import async_heartbeat_loop

    repo = PublishTasksRepository()
    accounts_repo = SocialAccountsRepository()
    task = await repo.get_task(task_id)
    if not task:
        raise RuntimeError(f"publish task {task_id} not found")
    rows = await repo.get_task_accounts(task_id)
    if not rows:
        raise RuntimeError(f"publish task {task_id} has no accounts")

    # Lazily fetched, NOT eagerly. These are the OAuth app credentials
    # (client_id/secret) and only the official/h5 channels use them — the
    # session channel publishes through browser cookies and never touches them.
    #
    # Fetching eagerly meant a deployment with no `system_settings
    # ['distribution.douyin']` row could not publish AT ALL, even a batch that
    # was 100% session accounts. That is not a hypothetical: an operator whose
    # Douyin open-platform capabilities are still under review has no
    # credentials to configure, and the session channel exists precisely so
    # they can publish anyway. The first real end-to-end publish run of this
    # module died here — `publish task ... errored: system_settings
    # ['distribution.douyin'] missing` — with a single session account.
    #
    # Cached after the first await so a mixed batch pays for it once.
    creds_box: dict[str, Any] = {}

    async def creds_lazy():
        if "v" not in creds_box:
            creds_box["v"] = await get_douyin_credentials()
        return creds_box["v"]

    # Heartbeat (§7.2): the session channel turns this step from "a few HTTP
    # calls" into "one headed browser + one full video upload PER ACCOUNT",
    # i.e. tens of minutes for a batch. Without a heartbeat the health
    # classifier has only wall-clock to go on and must choose between killing
    # legitimate long batches and staying silent long after a worker dies.
    # Tolerates an empty workflow_id (unit tests, no DBOS runtime).
    #
    # request_scope establishes the ambient tenant scope this step's repo calls
    # need — `resources` is a scoped model, and resolving the video URL is a
    # SELECT against it. Without this the fail-closed guard raises
    #   "SELECT references scoped table(s) ['Resources(resources)'] but no
    #    scope is set"
    # and EVERY publish dies before it can reach a browser. Found by the second
    # real end-to-end run; every other workflow in this package already opens
    # one (download / thumbnail / upload_postprocess / …) — publish was the
    # only one missing it.
    #
    # USER scope, not system: `resource_ids` comes straight from the client and
    # `create_task` never verifies they belong to the caller. The tenant filter
    # is what makes another user's resource resolve to "no servable URL" instead
    # of being happily published to the attacker's account.
    async with request_scope(Scope(user_id=user_id)):
        async with async_heartbeat_loop(workflow_id=DBOS.workflow_id or ""):
            statuses = await _run_accounts(rows, accounts_repo, creds_lazy, task, repo)
    return {"statuses": statuses}


async def _run_accounts(rows, accounts_repo, creds_lazy, task: dict, repo) -> list[str]:
    """Dispatch each account row, publishing only the ones still 'pending'.

    Idempotency guard: a workflow re-dispatch (retry) must NOT re-publish rows
    that already settled. Re-publishing a 'success' row would create a
    duplicate irreversible Douyin post; re-publishing a 'pending_share' row
    would mint a brand-new share_id and invalidate the link the user is
    about to tap. Only 'pending' rows are actually published here — the
    retry endpoint's reset_failed_accounts is what flips failed/cancelled
    rows back to 'pending' before a retry dispatch, so this loop naturally
    only republishes what was reset. Any other status (success,
    pending_share, failed, cancelled) is carried straight through unchanged.
    Extracted from the @DBOS.step so it's unit-testable with fakes.

    Channel dispatch reads the account through DIFFERENT secret boundaries:
    'session' rows need the decrypted storage_state (``get_with_session``),
    OAuth rows need the decrypted access_token (``get_with_tokens``). The two
    reads deliberately don't overlap — each drops the other's secret columns,
    so a row can never carry a credential its channel has no business with.
    Only the few fields the channel needs are merged onto the publish row; a
    blanket ``{**row, **account}`` would let ``social_accounts.id`` overwrite
    the publish_task_accounts row id and settle the WRONG row.
    """
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
    )
    from app.services.distribution.registry import get_adapter

    statuses: list[str] = []
    for row in rows:
        status = row.get("status")
        if status != "pending":
            statuses.append(status)
            continue
        account_id = int(row["account_id"])
        task_channel = row.get("channel", "h5")
        if task_channel == "session":
            acct = await accounts_repo.get_with_session(account_id) or {}
            merged = {
                **row,
                "auth_type": acct.get("auth_type"),
                "session_state": acct.get("session_state"),
                SESSION_STATE_DECRYPT_FAILED: acct.get(SESSION_STATE_DECRYPT_FAILED),
                "environment": acct.get("environment"),
            }
        else:
            # get_with_tokens returns decrypted access_token for the publish call.
            tokens = await accounts_repo.get_with_tokens(account_id) or {}
            merged = {
                **row,
                "access_token": tokens.get("access_token"),
                "auth_type": tokens.get("auth_type"),
            }
        if decide_channel(task_channel, merged) == "session":
            statuses.append(
                await _publish_one_account_session(merged, task, repo, accounts_repo)
            )
            continue
        # Everything else (including a 'session' row that degraded — see
        # decide_channel) goes down the OAuth/H5 path, which re-runs
        # decide_channel on the same dict and therefore agrees with us.
        # Only here — after decide_channel picked a non-session route — do we
        # actually need the OAuth app credentials. A session-only batch never
        # reaches this line and therefore needs no configuration at all.
        adapter = get_adapter(row.get("platform", "douyin"), await creds_lazy())
        statuses.append(await _publish_one_account(merged, adapter, task, repo))
    return statuses


@DBOS.step()
async def emit_publish_notification_step(
    user_id: str, task_id: int, severity: str, summary: str
) -> None:
    """W3d narrow inbox (producer 2/3): one 'publish_result' notification to the
    batch initiator when a publish batch reaches a terminal outcome, deep-linked
    to the distribution records. Wrapped as a DBOS step (durable + memoized on
    replay) so a retry doesn't re-notify. notify() is itself best-effort — it
    never raises, so this step can't fail the publish workflow."""
    from app.services.notifications import notify

    title = "Publish complete" if severity == "success" else "Publish failed"
    await notify(
        user_id=str(user_id),
        kind="publish_result",
        title=title,
        body=summary,
        severity=severity,  # type: ignore[arg-type]
        link_kind="publish_batch",
        link_id=str(task_id),
    )


@DBOS.workflow()
async def publish_distribution_workflow(task_id: int, user_id: str) -> dict[str, Any]:
    """Publish every account in the batch, then classify the outcome via
    ``classify_batch``:

      - 'all_failed' (no account reached success/pending_share): fail + raise
        (route C — never a failed dict).
      - 'partial' (some settled, but at least one hard-failed): ALSO fail +
        raise. This is intentional (route-C raise-on-partial): per-account
        success/pending_share rows are already durably persisted by the step
        (via ``set_account_status``) before this decision, so raising loses
        nothing — and the idempotent ``_run_accounts`` loop guarantees a
        retry dispatch won't re-publish those settled rows. Failing the task
        is what makes it eligible for ``manager.retry_task`` (only
        failed/cancelled/lost tasks are retryable), which is what powers the
        Retry button on a partial batch.
      - 'ok' (all settled, none failed): complete normally.

    ``publish_tasks`` has NO status/phase column (migration 356: "Never
    duplicate phase/status here" — task_tracking is the sole execution
    source), so the outcome only ever drives task_tracking via the manager.
    """
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    await mark_publish_processing_step(DBOS.workflow_id, user_id)

    try:
        result = await run_publish_accounts_step(task_id, user_id)
    except Exception as e:
        await manager.fail(DBOS.workflow_id, f"publish batch errored: {e}")
        await emit_publish_notification_step(
            user_id, task_id, "error", f"Publish batch errored: {e}"
        )
        raise RuntimeError(f"publish task {task_id} errored: {e}") from e

    statuses = result["statuses"]
    outcome = classify_batch(statuses)

    if outcome == "all_failed":
        # Every account failed / cancelled — surface as a failed task.
        await manager.fail(DBOS.workflow_id, "all accounts failed to publish")
        await emit_publish_notification_step(
            user_id, task_id, "error", "All accounts failed to publish"
        )
        raise RuntimeError(f"publish task {task_id}: all accounts failed")

    if outcome == "partial":
        ok = sum(1 for s in statuses if s == "success")
        nfailed = sum(1 for s in statuses if s == "failed")
        await manager.fail(
            DBOS.workflow_id, f"{nfailed} account(s) failed ({ok} published)"
        )
        await emit_publish_notification_step(
            user_id,
            task_id,
            "error",
            f"{nfailed} account(s) failed ({ok} published)",
        )
        raise RuntimeError(f"publish task {task_id}: {nfailed} account(s) failed")

    pending = sum(1 for s in statuses if s == "pending_share")
    ok = sum(1 for s in statuses if s == "success")
    subtitle = f"{ok} published"
    if pending:
        subtitle += f", {pending} awaiting Douyin"
    await manager.complete(DBOS.workflow_id, subtitle=subtitle)
    await emit_publish_notification_step(user_id, task_id, "success", subtitle)
    return {"status": "completed", "task_id": task_id, "statuses": statuses}
