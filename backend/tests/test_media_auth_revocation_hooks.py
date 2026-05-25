"""#275 Task 3: revocation hooks on logout / password change."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import media_auth as m

# ---------------------------------------------------------------------------
# delete_media_session — revokes for the cookie's user before clearing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_media_session_revokes_cookie_user(monkeypatch):
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", "media-secret", raising=False)
    monkeypatch.setattr(m.settings, "SUPABASE_SERVICE_ROLE_KEY", "svc", raising=False)
    revoked = {}
    monkeypatch.setattr(
        m,
        "revoke_media_tokens",
        AsyncMock(side_effect=lambda uid: revoked.update(uid=uid)),
    )
    now = int(time.time())
    cookie = m._sign_token("u9", now, now + m.COOKIE_MAX_AGE)
    req = SimpleNamespace(cookies={m.COOKIE_NAME: cookie})
    await m.delete_media_session(req)
    assert revoked.get("uid") == "u9"


@pytest.mark.asyncio
async def test_delete_media_session_no_cookie_does_not_revoke(monkeypatch):
    """If there's no cookie (e.g. already cleared), revoke must not be called."""
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", "media-secret", raising=False)
    monkeypatch.setattr(m.settings, "SUPABASE_SERVICE_ROLE_KEY", "svc", raising=False)
    revoke_mock = AsyncMock()
    monkeypatch.setattr(m, "revoke_media_tokens", revoke_mock)
    req = SimpleNamespace(cookies={})
    await m.delete_media_session(req)
    revoke_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_media_session_invalid_cookie_does_not_revoke(monkeypatch):
    """An invalid/tampered cookie must not trigger revocation."""
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", "media-secret", raising=False)
    monkeypatch.setattr(m.settings, "SUPABASE_SERVICE_ROLE_KEY", "svc", raising=False)
    revoke_mock = AsyncMock()
    monkeypatch.setattr(m, "revoke_media_tokens", revoke_mock)
    req = SimpleNamespace(cookies={m.COOKIE_NAME: "bad.token.value.here"})
    await m.delete_media_session(req)
    revoke_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# supabase_auth_router /signout — revoke_media_tokens in background task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signout_triggers_revoke_for_known_user(monkeypatch):
    """POST /signout: when user_id is resolved, revoke_media_tokens is added
    as a background task."""
    import app.api.supabase_auth_router as auth_router
    from app.api.media_auth import revoke_media_tokens

    fake_user = {"id": "u9"}
    auth_svc_mock = AsyncMock()
    auth_svc_mock.get_user = AsyncMock(return_value=fake_user)
    auth_svc_mock.sign_out = AsyncMock(return_value={"success": True})

    revoke_mock = AsyncMock()

    background_tasks = MagicMock()
    added_tasks = []
    background_tasks.add_task = MagicMock(
        side_effect=lambda fn, *args, **kwargs: added_tasks.append((fn, args, kwargs))
    )

    with (
        patch.object(
            auth_router,
            "SupabaseAuthService",
            return_value=auth_svc_mock,
        ),
        patch(
            "app.api.supabase_auth_router.revoke_media_tokens",
            revoke_mock,
        ),
    ):
        await auth_router.sign_out(
            background_tasks=background_tasks,
            authorization="Bearer fake-token",
        )

    fns = [t[0] for t in added_tasks]
    assert (
        revoke_mock in fns
    ), "revoke_media_tokens should be added as a background task"


@pytest.mark.asyncio
async def test_signout_no_revoke_when_user_unknown(monkeypatch):
    """POST /signout: when auth header is absent / invalid, no revoke task added."""
    import app.api.supabase_auth_router as auth_router

    auth_svc_mock = AsyncMock()
    auth_svc_mock.get_user = AsyncMock(return_value=None)
    auth_svc_mock.sign_out = AsyncMock(return_value={"success": True})

    revoke_mock = AsyncMock()
    background_tasks = MagicMock()
    added_tasks = []
    background_tasks.add_task = MagicMock(
        side_effect=lambda fn, *args, **kwargs: added_tasks.append((fn, args, kwargs))
    )

    with (
        patch.object(auth_router, "SupabaseAuthService", return_value=auth_svc_mock),
        patch("app.api.supabase_auth_router.revoke_media_tokens", revoke_mock),
    ):
        await auth_router.sign_out(
            background_tasks=background_tasks,
            authorization=None,
        )

    fns = [t[0] for t in added_tasks]
    assert revoke_mock not in fns, "revoke_media_tokens must NOT be queued when no user"


# ---------------------------------------------------------------------------
# supabase_auth_router PUT /me — revoke only when password is updated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_user_with_password_revokes(monkeypatch):
    """PUT /me: when request.password is set AND update succeeds, revoke is awaited."""
    import app.api.supabase_auth_router as auth_router
    from app.api.supabase_auth_router import UpdateUserRequest

    fake_user = {"id": "u9"}
    auth_svc_mock = AsyncMock()
    auth_svc_mock.get_user = AsyncMock(return_value=fake_user)
    auth_svc_mock.update_user = AsyncMock(return_value={"success": True})

    revoke_mock = AsyncMock()

    with (
        patch.object(auth_router, "SupabaseAuthService", return_value=auth_svc_mock),
        patch("app.api.supabase_auth_router.revoke_media_tokens", revoke_mock),
    ):
        req = UpdateUserRequest(password="new-secure-password")
        await auth_router.update_user(req, authorization="Bearer fake-token")

    revoke_mock.assert_awaited_once_with("u9")


@pytest.mark.asyncio
async def test_update_user_without_password_does_not_revoke(monkeypatch):
    """PUT /me: when no password in the request, revoke must NOT be called."""
    import app.api.supabase_auth_router as auth_router
    from app.api.supabase_auth_router import UpdateUserRequest

    auth_svc_mock = AsyncMock()
    auth_svc_mock.update_user = AsyncMock(return_value={"success": True})

    revoke_mock = AsyncMock()

    with (
        patch.object(auth_router, "SupabaseAuthService", return_value=auth_svc_mock),
        patch("app.api.supabase_auth_router.revoke_media_tokens", revoke_mock),
    ):
        req = UpdateUserRequest(username="new-name")
        await auth_router.update_user(req, authorization="Bearer fake-token")

    revoke_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_user_password_fails_no_revoke(monkeypatch):
    """PUT /me: when update_user returns success=False, revoke must NOT be called."""
    import app.api.supabase_auth_router as auth_router
    from app.api.supabase_auth_router import UpdateUserRequest

    auth_svc_mock = AsyncMock()
    auth_svc_mock.update_user = AsyncMock(
        return_value={"success": False, "message": "weak password"}
    )

    revoke_mock = AsyncMock()

    with (
        patch.object(auth_router, "SupabaseAuthService", return_value=auth_svc_mock),
        patch("app.api.supabase_auth_router.revoke_media_tokens", revoke_mock),
    ):
        req = UpdateUserRequest(password="weak")
        try:
            await auth_router.update_user(req, authorization="Bearer fake-token")
        except Exception:
            pass  # HTTPException expected here

    revoke_mock.assert_not_awaited()
