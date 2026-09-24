"""Who a share link lets in, and the visitor credential a password unlocks.

A share (``shares`` row) is reached by its public ``share_code``. The code
alone used to be the credential everywhere a visitor went next — the media
file routes (``?share_token=<code>``), the review comments — and none of those
places knew about the share's password. So a password-protected share was
protected only on ``POST /shares/code/{code}``; every file and comment behind
it was one query parameter away from anybody who had the link.

Two kinds of ``share_token`` are accepted (:func:`resolve_share_token`):

- **a share grant** (``sg1.<share id>.<expires>.<sig>``), handed out by
  ``POST /shares/code/{code}`` once the visitor has passed the password check.
  The signature covers a fingerprint of the share's current password, so
  changing or removing the password revokes every grant issued before. A grant
  does not re-check ``max_views``: it was issued on a counted view, and the
  visitor who used up the last view must still be able to load the file.
- **the bare share code**, only for a share WITHOUT a password (old links,
  cached pages). It keeps the view limit, as it always did.

Either way the share has to be live: status ``active`` and not past
``expires_at``. Which resource it opens is the caller's business
(``media_permissions._validate_share_token`` matches ``resource_id``).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from loguru import logger

GRANT_PREFIX = "sg1"
# Long enough for a visitor to keep a video page open; short enough that a
# grant copied out of a URL does not outlive the evening.
GRANT_TTL_SECONDS = 12 * 3600
# Domain separator: a grant's MAC can never be mistaken for a media token's
# (``app/api/media_auth.py`` signs ``user.issued.expires`` with the same key).
_GRANT_DOMAIN = "share-grant"


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_time_expired(share: Mapping[str, Any]) -> bool:
    """Past ``expires_at``. An unparseable value counts as expired (fail
    closed — the old media check did the same)."""
    raw = share.get("expires_at")
    if not raw:
        return False
    expires_at = _as_utc(raw)
    if expires_at is None:
        return True
    return datetime.now(timezone.utc) > expires_at


def is_view_exhausted(share: Mapping[str, Any]) -> bool:
    max_views = share.get("max_views")
    return max_views is not None and (share.get("view_count") or 0) >= max_views


def is_live(share: Mapping[str, Any], *, count_views: bool) -> bool:
    if share.get("status") != "active" or is_time_expired(share):
        return False
    return not (count_views and is_view_exhausted(share))


def password_matches(stored: str | None, given: str | None) -> bool:
    """Constant-time comparison (the old ``!=`` leaked a timing signal)."""
    if not stored or given is None:
        return False
    return hmac.compare_digest(stored.encode(), given.encode())


def _password_fingerprint(password: str | None) -> str:
    return hashlib.sha256((password or "").encode()).hexdigest()[:16]


def _grant_payload(share_id: int, expires: int, password: str | None) -> str:
    fingerprint = _password_fingerprint(password)
    return f"{_GRANT_DOMAIN}|{share_id}|{expires}|{fingerprint}"


def sign_share_grant(share: Mapping[str, Any], *, now: int | None = None) -> str:
    """The visitor credential for ``share`` (call only after its password, if
    any, has been checked)."""
    from app.api.media_auth import _hmac, _signing_secret

    share_id = int(share["id"])
    expires = int(now if now is not None else time.time()) + GRANT_TTL_SECONDS
    sig = _hmac(
        _signing_secret(), _grant_payload(share_id, expires, share.get("password"))
    )
    return f"{GRANT_PREFIX}.{share_id}.{expires}.{sig}"


def _parse_grant(token: str) -> tuple[int, int, str] | None:
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != GRANT_PREFIX:
        return None
    try:
        return int(parts[1]), int(parts[2]), parts[3]
    except ValueError:
        return None


def _grant_signature_ok(
    share: Mapping[str, Any], share_id: int, expires: int, sig: str
) -> bool:
    from app.api.media_auth import _hmac, _verify_secrets

    payload = _grant_payload(share_id, expires, share.get("password"))
    return any(
        hmac.compare_digest(sig, _hmac(secret, payload)) for secret in _verify_secrets()
    )


async def load_share(
    *, code: str | None = None, share_id: int | None = None
) -> dict[str, Any] | None:
    """The ``shares`` columns the access checks need, by code or by id."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Shares

    stmt = select(
        Shares.id,
        Shares.share_code,
        Shares.share_type,
        Shares.resource_id,
        Shares.status,
        Shares.expires_at,
        Shares.max_views,
        Shares.view_count,
        Shares.password,
    )
    if code is not None:
        stmt = stmt.where(Shares.share_code == code)
    elif share_id is not None:
        stmt = stmt.where(Shares.id == share_id)
    else:
        return None
    async with read_scope() as session:
        row = (await session.execute(stmt.limit(1))).mappings().first()
    return dict(row) if row else None


async def resolve_share_token(token: str | None) -> dict[str, Any] | None:
    """The live share ``token`` opens, or None (see the module docstring)."""
    if not token:
        return None
    grant = _parse_grant(token)
    if grant is not None:
        share_id, expires, sig = grant
        if time.time() > expires:
            return None
        share = await load_share(share_id=share_id)
        if not share or not _grant_signature_ok(share, share_id, expires, sig):
            return None
        return share if is_live(share, count_views=False) else None

    share = await load_share(code=token)
    if not share:
        return None
    if share.get("password"):
        # The code is printed in the link; it proves nothing about the
        # password. Only a grant opens a protected share.
        return None
    return share if is_live(share, count_views=True) else None


async def token_opens_share(token: str | None, share_id: Any) -> bool:
    """True when ``token`` resolves to exactly the share ``share_id``."""
    try:
        opened = await resolve_share_token(token)
    except Exception as e:  # noqa: BLE001 — a lookup failure denies, logged
        logger.error(f"Share token check failed: {e}")
        return False
    return opened is not None and str(opened["id"]) == str(share_id)
