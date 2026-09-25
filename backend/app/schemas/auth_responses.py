"""Response shapes of ``/api/v1/auth/*`` (password auth, media auth, temp token).

Every model mirrors the dict the route already built, key for key (spec
2026-09-24-openapi-typed-frontend-design.md §5; wire tests in
``tests/api/test_auth_wire.py``).

The user / session objects come from GoTrue (Supabase Auth) through
``app/services/infra/supabase_auth_service.py``, which projects a fixed set
of keys out of GoTrue's response. They are declared ``extra="allow"``: if that
projection ever passes another GoTrue key through, it reaches the client
instead of being dropped by the model. ``created_at`` is GoTrue's timestamp
already rendered by ``str()`` (``2026-09-24 01:02:03.456789+00:00``), so it is
a string here, not a datetime.

Tokens appear only on the routes that always returned them: sign-in, sign-up
and refresh (GoTrue session) and ``POST /auth/media-token``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.envelope import Envelope
from app.schemas.resource_rows import ResourceTagTagRow

# --------------------------------------------------------------------------- #
# GoTrue user / session projections
# --------------------------------------------------------------------------- #


class _GoTrueObject(BaseModel):
    model_config = ConfigDict(extra="allow")


class AuthSignUpUser(_GoTrueObject):
    id: str
    email: str | None
    created_at: str | None


class AuthUser(_GoTrueObject):
    """The user as sign-in and ``PUT /auth/me`` return it."""

    id: str
    email: str | None
    user_metadata: dict[str, Any]


class AuthCurrentUser(AuthUser):
    """``GET /auth/me``: the verified JWT's claims. ``created_at`` is not a
    claim, so it is always ``null`` (``GET /auth/profile`` has the account
    name and display id)."""

    app_metadata: dict[str, Any]
    created_at: str | None


class AuthRefreshedSession(_GoTrueObject):
    access_token: str
    refresh_token: str
    expires_at: int | None


class AuthSignInSession(AuthRefreshedSession):
    token_type: str


class AuthSignUpSession(_GoTrueObject):
    """Present only when GoTrue signs the new user straight in (no e-mail
    confirmation required)."""

    access_token: str | None
    refresh_token: str | None
    expires_at: int | None


# --------------------------------------------------------------------------- #
# Route bodies
# --------------------------------------------------------------------------- #


class AuthSignUpResponse(BaseModel):
    success: bool
    user: AuthSignUpUser
    session: AuthSignUpSession | None


class AuthSignInResponse(BaseModel):
    success: bool
    user: AuthUser
    session: AuthSignInSession


class AuthRefreshResponse(BaseModel):
    success: bool
    session: AuthRefreshedSession


class AuthCurrentUserResponse(BaseModel):
    success: bool
    user: AuthCurrentUser


class AuthUpdateUserResponse(BaseModel):
    success: bool
    user: AuthUser


class AuthMessageResponse(BaseModel):
    """Sign-out and reset-password. ``success`` can be false on sign-out: a
    GoTrue failure there is reported in the body, not as an error status."""

    success: bool
    message: str


class AuthAck(BaseModel):
    """``{"success": true}`` with nothing else."""

    success: bool


class AuthMediaToken(BaseModel):
    """``POST /auth/media-token``: pass ``token`` as ``?token=`` on media URLs.
    ``expires_at`` is a Unix timestamp in seconds."""

    token: str
    expires_at: int


class TempTokenCreatedTag(ResourceTagTagRow):
    """The ``tags`` row ``POST /auth/temp-token/{token}/tags`` created.

    ``group_id`` is the row's BIGINT (a JSON number) unless the route assigned
    a group, in which case it is the group id as a string — the id the request
    carried, or the "Uncategorized" group's.
    """

    group_id: int | str | None


class TempTokenCreatedTagResponse(Envelope[TempTokenCreatedTag]):
    pass
