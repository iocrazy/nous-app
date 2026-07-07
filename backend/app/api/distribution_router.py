"""Distribution — 平台账号绑定（PR-D1）。发布/记录端点在 PR-D2。"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.core.config import settings

# get_current_user is otherwise unused directly in this module (CurrentUserDep
# already binds Depends(get_current_user) internally) — imported so tests can
# target the same dependency callable via ``dr.get_current_user`` overrides.
from app.core.deps import CurrentUserDep, get_current_user  # noqa: F401
from app.db import engine as db_engine
from app.repositories.social_accounts_repository import SocialAccountsRepository
from app.schemas.distribution import (
    AccountListResponse,
    ConnectAccountRequest,
    ConnectAccountResponse,
)
from app.services.distribution.credentials import (
    CredentialsNotConfigured,
    get_douyin_credentials,
)
from app.services.distribution.registry import get_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/distribution", tags=["Distribution"])
accounts_repo = SocialAccountsRepository()


def require_distribution() -> None:
    if not settings.FEATURE_DISTRIBUTION:
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
    await accounts_repo.execute(
        """
        INSERT INTO distribution_oauth_states (state, user_id, platform, scope_type, scope_id)
        VALUES ($1,$2,$3,$4,$5)
        """,
        state,
        user_id,
        platform,
        scope_type,
        scope_id,
    )


async def _pop_oauth_state(state: str) -> dict | None:
    # COMMITTING path required: this DELETE ... RETURNING consumes a row.
    # accounts_repo.fetch_one runs on eng.connect() (no transaction) and
    # would SILENTLY ROLL BACK the delete on connection close (the #498
    # silent-rollback class) — the state would never actually be consumed,
    # making it replayable. Use db_engine.execute_returning_one (eng.begin(),
    # auto-commit) instead.
    return await db_engine.execute_returning_one(
        """
        DELETE FROM distribution_oauth_states
        WHERE state = :s AND created_at > NOW() - INTERVAL '10 minutes'
        RETURNING *
        """,
        {"s": state},
    )


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


@router.post(
    "/accounts/connect",
    response_model=ConnectAccountResponse,
    dependencies=[Depends(require_distribution)],
)
async def connect_account(body: ConnectAccountRequest, user: CurrentUserDep):
    uid = str(user["id"])
    if body.scope_type == "user":
        # IDOR guard: never trust a client-supplied scope_id for user scope —
        # force it to the caller's own id unconditionally. Otherwise a client
        # could pass another user's uuid (team co-members can read peers'
        # user_id via GET /teams/{id}/members) and have the later oauth
        # callback bind the attacker's token under the victim's personal scope.
        scope_id = uid
    else:  # team
        scope_id = body.scope_id
        if scope_id not in await _user_team_ids(uid):
            raise HTTPException(status_code=403, detail="Not a member of this team")
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
