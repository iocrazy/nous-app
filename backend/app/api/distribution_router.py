"""Distribution — 平台账号绑定（PR-D1）。发布/记录端点在 PR-D2。"""

from __future__ import annotations

import logging
import secrets
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert

from app.core.config import settings

# get_current_user is otherwise unused directly in this module (CurrentUserDep
# already binds Depends(get_current_user) internally) — imported so tests can
# target the same dependency callable via ``dr.get_current_user`` overrides.
from app.core.deps import CurrentUserDep, get_current_user  # noqa: F401
from app.db.session import write_scope
from app.models import DistributionOauthStates
from app.repositories.publish_tasks_repository import (
    PublishTasksRepository,
    aggregate_task_status,
)
from app.repositories.social_accounts_repository import SocialAccountsRepository
from app.schemas.distribution import (
    AccountListResponse,
    ConnectAccountRequest,
    ConnectAccountResponse,
    SessionLoginCancelResponse,
    SessionLoginRequest,
    SessionLoginResponse,
    SessionOpResponse,
    SessionSmsRequest,
)
from app.schemas.distribution_publish import (
    PublishTaskCreate,
    PublishTaskListResponse,
    PublishTaskOut,
    ShareSchemaResponse,
    TaskAccountOut,
)
from app.services.distribution.credentials import (
    CredentialsNotConfigured,
    get_douyin_credentials,
)
from app.services.distribution.registry import get_adapter
from app.services.infra.dbos_orchestrator import start_workflow_routed
from app.services.infra.unified_task_manager import get_task_manager
from app.workflows.publish_distribution import (
    publish_distribution_workflow,
    visibility_to_private_status,
)
from app.workflows.session_login import TASK_TYPE as SESSION_LOGIN_TASK_TYPE
from app.workflows.session_login import session_login_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/distribution", tags=["Distribution"])
accounts_repo = SocialAccountsRepository()
publish_repo = PublishTasksRepository()


async def require_distribution() -> None:
    """Gate every account/OAuth endpoint on the DB module ACCESS switch
    (``system_settings['distribution.module'].enabled``, admin-controlled,
    fail-closed) — never an env flag. Module off → 404, same as an
    unregistered route. The frontend reads the switches from the batch
    ``GET /modules/status`` endpoint, which is never behind this gate."""
    from app.services.distribution.module_config import is_module_enabled

    if not await is_module_enabled():
        raise HTTPException(status_code=404, detail="Not found")


async def _user_team_ids(user_id: str) -> list[str]:
    """Team ids (as text) the user belongs to — a single seam so scope-
    ownership checks below (and tests) can monkeypatch one place."""
    from app.repositories.team_repository import TeamRepository

    teams = await TeamRepository().get_user_teams(user_id)
    return [str(t["id"]) for t in teams]


async def _authorize_account(account_id: int, user: dict) -> dict:
    """IDOR guard: only the account's own scope (self user, or a team the
    caller belongs to) may operate on it. 404 (not 403) on mismatch so
    existence isn't leaked to non-members."""
    acct = await accounts_repo.get_public(account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    uid = str(user["id"])
    if acct["scope_type"] == "user" and acct["scope_id"] == uid:
        return acct
    if acct["scope_type"] == "team" and acct["scope_id"] in await _user_team_ids(uid):
        return acct
    raise HTTPException(status_code=404, detail="Account not found")


async def _save_oauth_state(state, user_id, platform, scope_type, scope_id) -> None:
    async with write_scope() as session:
        await session.execute(
            insert(DistributionOauthStates).values(
                state=state,
                user_id=user_id,
                platform=platform,
                scope_type=scope_type,
                scope_id=scope_id,
            )
        )


_OAUTH_STATE_COLS = tuple(DistributionOauthStates.__table__.columns)


async def _pop_oauth_state(state: str) -> dict | None:
    # COMMITTING path required: this DELETE ... RETURNING consumes a row.
    # write_scope() commits (unlike a bare read_scope/connect()), so the state
    # is actually consumed rather than silently rolled back and replayable
    # (the #498 silent-rollback class).
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    stmt = (
        sa_delete(DistributionOauthStates)
        .where(
            DistributionOauthStates.state == state,
            DistributionOauthStates.created_at > cutoff,
        )
        .returning(*_OAUTH_STATE_COLS)
    )
    async with write_scope() as session:
        row = (await session.execute(stmt)).mappings().first()
    return dict(row) if row is not None else None


@router.get(
    "/accounts",
    response_model=AccountListResponse,
    dependencies=[Depends(require_distribution)],
)
async def list_accounts(user: CurrentUserDep):
    # PR-D1 先取个人账号 + 所在团队账号；团队清单复用现有 teams 查询
    team_ids = await _user_team_ids(user["id"])
    accounts = await accounts_repo.list_for_user(user["id"], team_ids)
    return {"accounts": accounts}


async def _resolve_bind_scope(scope_type: str, scope_id: str, uid: str) -> str:
    """Resolve the scope an account binding may land in — the single guard both
    binding channels (OAuth connect, session QR login) go through.

    IDOR guard: never trust a client-supplied scope_id for user scope — force
    it to the caller's own id unconditionally. Otherwise a client could pass
    another user's uuid (team co-members can read peers' user_id via GET
    /teams/{id}/members) and have the binding land under the victim's personal
    scope. Team scope requires membership.

    Shared by both channels on purpose: a session-bound account is exactly as
    publish-capable as an OAuth one, so a weaker check on the newer endpoint
    would be the whole hole, and a copy of the check is a copy that drifts.
    """
    if scope_type == "user":
        return uid
    if scope_id not in await _user_team_ids(uid):
        raise HTTPException(status_code=403, detail="Not a member of this team")
    return scope_id


@router.post(
    "/accounts/connect",
    response_model=ConnectAccountResponse,
    dependencies=[Depends(require_distribution)],
)
async def connect_account(body: ConnectAccountRequest, user: CurrentUserDep):
    uid = str(user["id"])
    scope_id = await _resolve_bind_scope(body.scope_type, body.scope_id, uid)
    try:
        creds = await get_douyin_credentials()
    except CredentialsNotConfigured:
        raise HTTPException(
            status_code=503,
            detail="Douyin credentials not configured (admin → settings)",
        )
    state = secrets.token_urlsafe(32)
    await _save_oauth_state(state, uid, body.platform, body.scope_type, scope_id)
    adapter = get_adapter(body.platform, creds)
    return {"auth_url": adapter.get_auth_url(state)}


@router.get(
    "/accounts/oauth/{platform}/callback",
    dependencies=[Depends(require_distribution)],
)
async def oauth_callback(platform: str, code: str = "", state: str = ""):
    front = settings.FRONTEND_URL.rstrip("/")
    st = await _pop_oauth_state(state) if state else None
    if not st or not code:
        return RedirectResponse(f"{front}/distribution/accounts?error=oauth_state")
    try:
        creds = await get_douyin_credentials()
        adapter = get_adapter(platform, creds)
        tok = await adapter.exchange_token(code)
        info = await adapter.get_user_info(tok["access_token"], tok["open_id"])
        await accounts_repo.upsert_account(
            scope_type=st["scope_type"],
            scope_id=st["scope_id"],
            platform=platform,
            platform_user_id=tok["open_id"],
            username=info["username"],
            avatar_url=info.get("avatar_url"),
            access_token=tok["access_token"],
            refresh_token=tok.get("refresh_token"),
            token_expires_at=None,
            created_by=st["user_id"],
        )
    except Exception:
        logger.exception("distribution: oauth callback failed (platform=%s)", platform)
        return RedirectResponse(f"{front}/distribution/accounts?error=oauth_exchange")
    return RedirectResponse(f"{front}/distribution/accounts?connected=1")


@router.post(
    "/accounts/{account_id}/refresh",
    dependencies=[Depends(require_distribution)],
)
async def refresh_account(account_id: int, user: CurrentUserDep):
    await _authorize_account(account_id, user)
    row = await accounts_repo.get_with_tokens(account_id)
    if not row or not row.get("refresh_token"):
        raise HTTPException(
            status_code=404, detail="Account not found or no refresh token"
        )
    creds = await get_douyin_credentials()
    adapter = get_adapter(row["platform"], creds)
    try:
        tok = await adapter.refresh_token(row["refresh_token"])
    except RuntimeError:
        await accounts_repo.mark_expired(account_id)
        raise HTTPException(
            status_code=400, detail="Refresh failed — reauthorize required"
        )
    return await accounts_repo.upsert_account(
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        platform=row["platform"],
        platform_user_id=row["platform_user_id"],
        username=row["username"],
        avatar_url=row.get("avatar_url"),
        access_token=tok["access_token"],
        refresh_token=tok.get("refresh_token", row["refresh_token"]),
        token_expires_at=None,
        created_by=row["created_by"],
    )


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    dependencies=[Depends(require_distribution)],
)
async def delete_account(account_id: int, user: CurrentUserDep):
    await _authorize_account(account_id, user)
    await accounts_repo.delete(account_id)


# ── Session channel — QR login (S2, spec §4.1) ────────────────────────


async def _load_login_task(task_id: str, user: dict) -> dict:
    """Load a session-login task row + IDOR guard.

    Returns ``{"phase", "metadata"}``. 404 (not 403) on a foreign task so
    existence isn't leaked — same posture as ``_authorize_task``.

    Reads ``task_tracking`` (route-C rule 1: the UI's only execution source);
    the caller-vs-owner comparison happens in Python because ``user_id`` is a
    UUID column and a malformed id in the WHERE clause would 500 instead of
    404.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TaskTracking

    async with read_scope() as session:
        row = (
            await session.execute(
                select(TaskTracking)
                .where(TaskTracking.dbos_workflow_id == task_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        # Read the attributes INSIDE the session, and off the ORM object rather
        # than a .mappings() row: the metadata column is mapped as ``metadata_``
        # (the ORM reserves ``metadata``), so a mappings() lookup by either name
        # is a coin flip that returns None on the wrong guess — and a None here
        # degrades into "login session not ready yet" forever, which looks like
        # a browser problem rather than a key typo.
        found = (
            None
            if row is None
            else {
                "user_id": row.user_id,
                "phase": row.phase,
                "task_type": row.task_type,
                "metadata": row.metadata_ or {},
            }
        )
    if not found or found["task_type"] != SESSION_LOGIN_TASK_TYPE:
        raise HTTPException(status_code=404, detail="Login task not found")
    if str(found["user_id"]) != str(user["id"]):
        raise HTTPException(status_code=404, detail="Login task not found")
    return {"phase": found["phase"], "metadata": found["metadata"]}


def _login_session_id(task: dict) -> str:
    """Pull the browser-side login session id out of the task's metadata.

    The workflow writes it under ``metadata.session_login`` (NOT inside the
    ``metadata.login`` blob, which is the frontend's rendering contract and
    must stay exactly the shape the spec fixed). 409 while it's absent: the
    workflow has been dispatched but hasn't opened the browser context yet, so
    there is genuinely nothing to talk to — an explicit "not ready" beats a
    500 or, worse, a silent no-op.
    """
    session_login = (task.get("metadata") or {}).get("session_login") or {}
    login_session_id = session_login.get("login_session_id")
    if not login_session_id:
        raise HTTPException(status_code=409, detail="Login session is not ready yet")
    return str(login_session_id)


@router.post(
    "/accounts/session/login",
    response_model=SessionLoginResponse,
    dependencies=[Depends(require_distribution)],
)
async def start_session_login(body: SessionLoginRequest, user: CurrentUserDep):
    """Start a QR-code login for one account. Returns immediately with the
    task id; the scan happens inside ``session_login_workflow`` and surfaces
    through ``task_tracking.metadata.login`` over Realtime (spec §4.1)."""
    from app.services.distribution.browser_client import BrowserClient
    from app.services.distribution.session_adapter import supported_session_platforms

    uid = str(user["id"])
    scope_id = await _resolve_bind_scope(body.scope_type, body.scope_id, uid)

    if body.platform not in supported_session_platforms():
        raise HTTPException(
            status_code=400,
            detail=f"Session channel not supported for platform: {body.platform}",
        )
    # Fail fast on a missing BROWSER_SERVICE_URL / token instead of dispatching
    # a workflow that can only fail: the user would watch a task appear and die
    # with an infra error they can't act on. 503 says "server side, not you".
    if not BrowserClient().is_configured:
        raise HTTPException(
            status_code=503,
            detail="Browser service not configured (BROWSER_SERVICE_URL / token)",
        )

    # 路线 C: the SAME wf_id keys the task_tracking row and the dispatched
    # workflow — create the row first so the QR modal can subscribe to it
    # before the workflow writes the first metadata patch.
    wf_id = str(_uuid.uuid4())
    await get_task_manager().create(
        user_id=uid,
        task_type=SESSION_LOGIN_TASK_TYPE,
        title=f"Connect {body.platform}"[:200],
        subtitle="Opening the QR code",
        dbos_workflow_id=wf_id,
        metadata={
            "login": {
                "platform": body.platform,
                "status": "waiting_scan",
                "qrcode_data_url": None,
                "expires_at": None,
                "message": "Opening the QR code",
            }
        },
    )
    await start_workflow_routed(
        SESSION_LOGIN_TASK_TYPE,
        dbos_workflow_callable=session_login_workflow,
        dbos_workflow_kwargs={
            "platform": body.platform,
            "scope_type": body.scope_type,
            "scope_id": scope_id,
            "user_id": uid,
        },
        workflow_id=wf_id,
    )
    return {"task_id": wf_id}


@router.post(
    "/accounts/session/login/{task_id}/sms",
    response_model=SessionOpResponse,
    dependencies=[Depends(require_distribution)],
)
async def submit_session_login_sms(
    task_id: str, body: SessionSmsRequest, user: CurrentUserDep
):
    """Hand an SMS verification code to the live browser context.

    Goes straight to nous-browser rather than through the workflow: the code is
    only meaningful to that context, and the user gets the platform's verdict
    ("code rejected") in this response instead of having to watch a metadata
    field flip. The workflow's poll loop picks up the resulting status change
    on its next tick either way.
    """
    from app.services.distribution.browser_client import BrowserClient

    task = await _load_login_task(task_id, user)
    if task["phase"] not in ("queued", "in_progress"):
        # A terminal task's browser context is already released — submitting a
        # code would 404 inside the container. Say so instead of pretending.
        raise HTTPException(status_code=409, detail="Login task is no longer active")
    snapshot = await BrowserClient().submit_login_sms(
        _login_session_id(task), body.code
    )
    return snapshot.result.to_dict()


@router.delete(
    "/accounts/session/login/{task_id}",
    response_model=SessionLoginCancelResponse,
    dependencies=[Depends(require_distribution)],
)
async def cancel_session_login(task_id: str, user: CurrentUserDep):
    """User abandoned the scan: cancel the task and release the browser context.

    Both halves matter and neither can be skipped:

    - ``manager.cancel`` flips task_tracking to cancelled, which is what the
      workflow's poll loop watches to stop early (it would otherwise keep
      polling for the full 5-minute TTL).
    - the ``close`` call releases the context now. The workflow's ``finally``
      would also close it, but only after its current poll returns, and the
      browser-side TTL only fires if the worker died — closing here is what
      makes "Cancel" feel like cancel.

    Closing twice is harmless (the second close finds nothing); leaking a
    headed-browser context for minutes is not.
    """
    from app.services.distribution.browser_client import BrowserClient

    task = await _load_login_task(task_id, user)
    session_login = (task.get("metadata") or {}).get("session_login") or {}
    login_session_id = session_login.get("login_session_id")

    await get_task_manager().cancel(task_id, str(user["id"]))
    closed = True
    if login_session_id:
        # Never raises — a cleanup failure must not turn "cancelled" into a 500
        # for the user, and the workflow's finally still gets its own attempt.
        closed = await BrowserClient().close_login(str(login_session_id))
    return {
        "cancelled": True,
        "context_released": closed,
        "message": (
            "Login cancelled"
            if closed
            else "Login cancelled; browser context release unconfirmed"
        ),
    }


# ── Publish tasks (PR-D2) ─────────────────────────────────────────────


def _task_out(task: dict, accounts: list[dict]) -> PublishTaskOut:
    """Assemble the API response: per-account rows + the rolled-up status."""
    rollup = aggregate_task_status([a.get("status", "pending") for a in accounts])
    return PublishTaskOut(
        id=task["id"],
        content_type=task.get("content_type", "video"),
        title=task["title"],
        description=task.get("description"),
        topics=task.get("topics") or [],
        visibility=task.get("visibility", "public"),
        distribution_mode=task.get("distribution_mode", "broadcast"),
        status=rollup,
        created_at=task["created_at"],
        accounts=[
            TaskAccountOut(
                id=a["id"],
                account_id=a["account_id"],
                username=a.get("username", "Unknown"),
                avatar_url=a.get("avatar_url"),
                channel=a.get("channel", "h5"),
                status=a.get("status", "pending"),
                error_message=a.get("error_message"),
                published_url=a.get("published_url"),
                platform_item_id=a.get("platform_item_id"),
                published_at=a.get("published_at"),
            )
            for a in accounts
        ],
    )


async def _authorize_task(task_id: int, user: dict) -> dict:
    """IDOR guard for a publish task: caller must own it (created it). 404 (not
    403) on mismatch so existence isn't leaked."""
    task = await publish_repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if str(task.get("user_id")) != str(user["id"]):
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post(
    "/tasks",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def create_task(body: PublishTaskCreate, user: CurrentUserDep):
    # IDOR: every target account must belong to the caller's scope (reuses the
    # D1 _authorize_account seam — 404s any account the caller can't operate).
    for account_id in body.account_ids:
        await _authorize_account(int(account_id), user)

    task = await publish_repo.create_task(
        user_id=user["id"],
        content_type=body.content_type,
        resource_ids=body.resource_ids,
        title=body.title,
        description=body.description,
        topics=body.topics,
        cover_vertical_resource_id=body.cover_vertical_resource_id,
        cover_horizontal_resource_id=body.cover_horizontal_resource_id,
        visibility=body.visibility,
        ai_content=body.ai_content,
        allow_download=body.allow_download,
        distribution_mode=body.distribution_mode,
    )
    task_id = int(task["id"])

    # one_to_one assigns resources round-robin to accounts; broadcast leaves
    # resource_id NULL (the workflow uses the batch's first resource).
    for idx, account_id in enumerate(body.account_ids):
        cfg = body.account_configs.get(account_id)
        resource_id = None
        if body.distribution_mode == "one_to_one" and body.resource_ids:
            resource_id = body.resource_ids[idx % len(body.resource_ids)]
        await publish_repo.create_task_account(
            task_id=task_id,
            account_id=account_id,
            resource_id=resource_id,
            channel=body.channel,
            title=cfg.title if cfg else None,
            description=cfg.description if cfg else None,
            topics=cfg.topics if cfg else None,
            status="pending",
        )

    # 路线 C: the SAME wf_id keys the task_tracking row and the dispatched
    # workflow (manager.create(dbos_workflow_id=wf_id) ==
    # start_workflow_routed(workflow_id=wf_id)) — never dispatch inside a step.
    wf_id = str(_uuid.uuid4())
    await get_task_manager().create(
        user_id=user["id"],
        task_type="publish",  # ≤20 chars (task_tracking.task_type VARCHAR(20))
        title=f"Publish: {body.title}"[:200],
        subtitle=f"{len(body.account_ids)} account(s)",
        dbos_workflow_id=wf_id,
        metadata={"publish_task_id": task["id"], "channel": body.channel},
    )
    await publish_repo.set_task_workflow_id(task_id, wf_id)
    await start_workflow_routed(
        "publish",
        dbos_workflow_callable=publish_distribution_workflow,
        dbos_workflow_kwargs={"task_id": task_id, "user_id": user["id"]},
        workflow_id=wf_id,
    )

    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(await publish_repo.get_task(task_id), accounts)


@router.get(
    "/tasks",
    response_model=PublishTaskListResponse,
    dependencies=[Depends(require_distribution)],
)
async def list_tasks(user: CurrentUserDep):
    tasks = await publish_repo.list_tasks(user["id"])
    out = []
    for task in tasks:
        accounts = await publish_repo.get_task_accounts(int(task["id"]))
        out.append(_task_out(task, accounts))
    return {"tasks": out}


@router.get(
    "/tasks/{task_id}",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def get_task(task_id: int, user: CurrentUserDep):
    task = await _authorize_task(task_id, user)
    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(task, accounts)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def cancel_task(task_id: int, user: CurrentUserDep):
    task = await _authorize_task(task_id, user)
    await publish_repo.mark_accounts_cancelled(task_id)
    wf_id = task.get("dbos_workflow_id")
    if wf_id:
        await get_task_manager().cancel(wf_id, user["id"])
    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(await publish_repo.get_task(task_id), accounts)


@router.post(
    "/tasks/{task_id}/retry",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def retry_task(task_id: int, user: CurrentUserDep):
    task = await _authorize_task(task_id, user)
    # Re-key task_tracking to a fresh workflow id and re-dispatch (retry_task
    # with new_workflow_id — otherwise the row keeps pointing at the terminal
    # workflow and the sweeper re-marks it lost; see bug_retry_failed_downloads).
    # retry_task() only mutates task_tracking (and returns non-None) when the
    # row is actually in a terminal/retryable state (failed/cancelled/lost).
    # It must gate everything below: dispatching unconditionally would fire a
    # SECOND real DBOS workflow under new_wf while task_tracking still points
    # at old_wf — an orphaned, untracked duplicate that also republishes
    # already-succeeded accounts a second time.
    new_wf = str(_uuid.uuid4())
    old_wf = task.get("dbos_workflow_id")
    if not old_wf:
        raise HTTPException(status_code=409, detail="Task cannot be retried")
    retried = await get_task_manager().retry_task(
        old_wf, user["id"], new_workflow_id=new_wf
    )
    if retried is None:
        raise HTTPException(status_code=409, detail="Task is not in a retryable state")
    await publish_repo.reset_failed_accounts(task_id)
    await publish_repo.set_task_workflow_id(task_id, new_wf)
    await start_workflow_routed(
        "publish",
        dbos_workflow_callable=publish_distribution_workflow,
        dbos_workflow_kwargs={"task_id": task_id, "user_id": user["id"]},
        workflow_id=new_wf,
    )
    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(await publish_repo.get_task(task_id), accounts)


@router.get(
    "/tasks/{task_id}/share-schema",
    response_model=ShareSchemaResponse,
    dependencies=[Depends(require_distribution)],
)
async def get_share_schema(task_id: int, user: CurrentUserDep):
    """Re-sign an H5 share Schema URL for this task's first H5 account still
    awaiting the handoff (signature is fresh each call)."""
    await _authorize_task(task_id, user)
    accounts = await publish_repo.get_task_accounts(task_id)
    h5 = next(
        (a for a in accounts if a.get("channel") == "h5" and a.get("share_id")), None
    )
    if not h5:
        raise HTTPException(status_code=400, detail="No H5 share pending for this task")
    task = await publish_repo.get_task(task_id)
    content_type = task.get("content_type") or "video"
    creds = await get_douyin_credentials()
    adapter = get_adapter(h5.get("platform", "douyin"), creds)
    title = h5.get("title") or task.get("title") or ""
    topics = h5.get("topics") or task.get("topics") or []
    allow_download = task.get("allow_download")
    if allow_download is None:
        allow_download = True
    private_status = visibility_to_private_status(task.get("visibility"))

    if content_type == "images":
        # An images note carries EVERY resource in batch order (never split per
        # account), so resolve straight from the batch's resource_ids.
        image_urls: list[str] = []
        for rid in task.get("resource_ids") or []:
            if not rid:
                continue
            url = await publish_repo.get_resource_media_url(int(rid))
            if url:
                image_urls.append(url)
        if not image_urls:
            raise HTTPException(status_code=400, detail="No media URL for share")
        schema_url = await adapter.generate_image_share_url(
            image_urls=image_urls,
            title=title,
            share_id=h5["share_id"],
            hashtags=topics,
            private_status=private_status,
            allow_download=bool(allow_download),
        )
        return ShareSchemaResponse(schema_url=schema_url or "", share_id=h5["share_id"])

    resource_id = h5.get("resource_id") or (task.get("resource_ids") or [None])[0]
    video_url = (
        await publish_repo.get_resource_media_url(int(resource_id))
        if resource_id
        else None
    )
    if not video_url:
        raise HTTPException(status_code=400, detail="No media URL for share")
    schema_url = await adapter.generate_share_url(
        video_url=video_url,
        title=title,
        share_id=h5["share_id"],
        hashtags=topics,
        private_status=private_status,
        allow_download=bool(allow_download),
    )
    return ShareSchemaResponse(schema_url=schema_url or "", share_id=h5["share_id"])
