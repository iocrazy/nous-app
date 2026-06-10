"""Regression tests for ?token= auth on the resource file endpoints.

Background (2026-06-10): Task Center's Download button navigates the
browser to ``/api/v1/resources/{id}/file`` — a bare URL can't carry an
Authorization header, so the only auth transport is ``?token=``. The
frontend's URL-auth credential is the signed media token (``mediaToken``
from /auth/media-token), but the file endpoints treated ``?token=``
exclusively as a Supabase JWT → every media-token download 403'd
("Access denied" — the auth was working, the transport contract wasn't).

These tests pin:
  1. both file endpoints try ``validate_media_cookie`` (media token)
     before falling back to the JWT path
  2. ``serve_version_file`` performs a resource-level ``check_media_access``
     (it used to serve any version file to ANY authenticated user)
"""

from __future__ import annotations

import importlib
import inspect


def _source(module: str, func: str) -> str:
    mod = importlib.import_module(module)
    return inspect.getsource(getattr(mod, func))


def test_serve_resource_file_accepts_media_token() -> None:
    source = _source("app.api.resources_crud_router", "serve_resource_file")

    media_tok = source.find("validate_media_cookie")
    jwt_fallback = source.find("get_auth(request")
    assert media_tok != -1, (
        "serve_resource_file must try the signed media token "
        "(validate_media_cookie) for ?token= — it's the frontend's only "
        "credential for <a download>/<img>/<video> URLs."
    )
    assert jwt_fallback != -1 and media_tok < jwt_fallback, (
        "media-token resolution must come BEFORE the JWT fallback so "
        "mediaToken URLs don't 401 inside get_auth."
    )


def test_serve_version_file_accepts_media_token_and_checks_access() -> None:
    source = _source("app.api.resources_versions_router", "serve_version_file")

    assert "validate_media_cookie" in source, (
        "serve_version_file must accept the signed media token via ?token= "
        "(same URL-auth transport as serve_resource_file)."
    )
    assert "check_media_access" in source, (
        "serve_version_file must check resource-level access — being "
        "authenticated is not authorization to read someone else's files."
    )
