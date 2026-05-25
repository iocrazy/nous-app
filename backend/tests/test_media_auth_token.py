"""#276/#275 Task 1: dedicated secret + issued_at token format (crypto only)."""

from __future__ import annotations

import time

import pytest

from app.api import media_auth as m


def _patch_secrets(monkeypatch, *, media: str, service: str):
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", media, raising=False)
    monkeypatch.setattr(m.settings, "SUPABASE_SERVICE_ROLE_KEY", service, raising=False)


def test_sign_and_verify_roundtrip_new_format(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    tok = m._sign_token("user-1", now, now + 100)
    assert tok.count(".") == 3  # user_id.issued_at.expires_at.sig
    parsed = m._verify_token(tok)
    assert parsed is not None
    assert parsed.user_id == "user-1"
    assert parsed.issued_at == now


def test_expired_token_rejected(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    tok = m._sign_token("user-1", now - 200, now - 100)  # already expired
    assert m._verify_token(tok) is None


def test_tampered_signature_rejected(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    tok = m._sign_token("user-1", now, now + 100)
    assert m._verify_token(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None


def test_legacy_3part_token_accepted_during_grace(monkeypatch):
    # Old token signed with the OLD service-role key, 3-part format.
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    import hashlib
    import hmac

    payload = f"user-1.{now + 100}"
    sig = hmac.new(b"svc-key", payload.encode(), hashlib.sha256).hexdigest()[:32]
    legacy = f"{payload}.{sig}"
    parsed = m._verify_token(legacy)
    assert parsed is not None
    assert parsed.user_id == "user-1"
    assert parsed.issued_at is None  # legacy has no issued_at


def test_media_secret_falls_back_to_service_role_when_unset(monkeypatch):
    _patch_secrets(monkeypatch, media="", service="svc-key")
    assert m._signing_secret() == "svc-key"


def test_signing_prefers_media_secret(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    assert m._signing_secret() == "media-secret"
