"""Tests for local JWKS-based JWT verification in app.core.deps.

ES256-only: covers happy path, signature/audience/expiry rejection,
JWKS caching, and explicit rejection of legacy HS256 tokens.
"""

import base64
import json
import time
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

SUPABASE_URL = "http://test-supabase.local"
EC_KID = "ec-key-1"
HS_KID = "hs-key-1"


@pytest.fixture(autouse=True)
def patch_supabase_url(monkeypatch):
    """Pin settings.SUPABASE_URL so JWKS URL is stable across tests."""
    from app.core import config as cfg

    monkeypatch.setattr(cfg.settings, "SUPABASE_URL", SUPABASE_URL)


@pytest.fixture(autouse=True)
def reset_jwks_cache():
    """Clear deps.py module-level JWKS singleton between tests."""
    from app.core import deps

    if hasattr(deps, "_jwks_client"):
        deps._jwks_client = None
    yield
    if hasattr(deps, "_jwks_client"):
        deps._jwks_client = None


@pytest.fixture
def ec_keypair():
    private = ec.generate_private_key(ec.SECP256R1())
    return private, private.public_key()


@pytest.fixture
def hs_secret():
    # 32 bytes — matches Supabase GOTRUE_JWT_SECRET sizing
    return "0123456789abcdef0123456789abcdef"


@pytest.fixture
def jwks_dict(ec_keypair, hs_secret):
    """Build a JWKS document with one ES256 (ec) and one HS256 (oct) key."""
    _, public = ec_keypair
    ec_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public))
    ec_jwk.update({"kid": EC_KID, "alg": "ES256", "use": "sig"})

    hs_jwk = {
        "kty": "oct",
        "kid": HS_KID,
        "k": base64.urlsafe_b64encode(hs_secret.encode()).rstrip(b"=").decode(),
        "alg": "HS256",
        "use": "sig",
    }
    return {"keys": [ec_jwk, hs_jwk]}


@pytest.fixture
def jwks_fetch(jwks_dict):
    """Patch PyJWKClient.fetch_data so no real network call happens."""
    with patch.object(jwt.PyJWKClient, "fetch_data", return_value=jwks_dict) as m:
        yield m


def _make_token(payload_overrides, key, algorithm, kid):
    payload = {
        "sub": "test-user-id",
        "aud": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    payload.update(payload_overrides)
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})


def _make_es256(ec_keypair, **overrides):
    private, _ = ec_keypair
    return _make_token(overrides, private, "ES256", EC_KID)


def _make_hs256(hs_secret, **overrides):
    return _make_token(overrides, hs_secret, "HS256", HS_KID)


# ============================================================================
# Happy paths — both signature algorithms are accepted
# ============================================================================


@pytest.mark.unit
async def test_es256_valid_token_returns_auth_context(ec_keypair, jwks_fetch):
    from app.core.deps import _validate_bearer_token

    token = _make_es256(ec_keypair, sub="user-es256")
    ctx = await _validate_bearer_token(f"Bearer {token}")

    assert ctx.user_id == "user-es256"
    assert ctx.auth_type == "jwt"
    assert ctx.scopes is None


@pytest.mark.unit
async def test_hs256_token_rejected(hs_secret, jwks_fetch):
    """Legacy HS256 tokens (signed before ES256 cutover) must be rejected.
    User has to re-login to get a new ES256-signed session."""
    from app.core.deps import _validate_bearer_token

    token = _make_hs256(hs_secret, sub="legacy-user")
    with pytest.raises(HTTPException) as exc:
        await _validate_bearer_token(f"Bearer {token}")
    assert exc.value.status_code == 401


# ============================================================================
# Rejection paths — every malformed/expired/wrong-claim case → 401
# ============================================================================


@pytest.mark.unit
async def test_bad_signature_rejected(ec_keypair, jwks_fetch):
    from app.core.deps import _validate_bearer_token

    token = _make_es256(ec_keypair)
    # Flip the last 4 chars of the signature (still base64-shaped)
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")

    with pytest.raises(HTTPException) as exc:
        await _validate_bearer_token(f"Bearer {tampered}")
    assert exc.value.status_code == 401


@pytest.mark.unit
async def test_wrong_audience_rejected(ec_keypair, jwks_fetch):
    from app.core.deps import _validate_bearer_token

    token = _make_es256(ec_keypair, aud="not-authenticated")

    with pytest.raises(HTTPException) as exc:
        await _validate_bearer_token(f"Bearer {token}")
    assert exc.value.status_code == 401


@pytest.mark.unit
async def test_expired_token_rejected(ec_keypair, jwks_fetch):
    from app.core.deps import _validate_bearer_token

    token = _make_es256(ec_keypair, exp=int(time.time()) - 60)

    with pytest.raises(HTTPException) as exc:
        await _validate_bearer_token(f"Bearer {token}")
    assert exc.value.status_code == 401


@pytest.mark.unit
async def test_missing_bearer_prefix_rejected():
    from app.core.deps import _validate_bearer_token

    with pytest.raises(HTTPException) as exc:
        await _validate_bearer_token("NotBearer xxx")
    assert exc.value.status_code == 401


# ============================================================================
# JWKS caching — must hit network once, not twice
# ============================================================================


@pytest.mark.unit
async def test_jwks_fetched_once_then_cached(ec_keypair, jwks_fetch):
    """Two consecutive verifications must trigger only one JWKS fetch."""
    from app.core.deps import _validate_bearer_token

    token1 = _make_es256(ec_keypair, sub="user-1")
    token2 = _make_es256(ec_keypair, sub="user-2")

    await _validate_bearer_token(f"Bearer {token1}")
    after_first = jwks_fetch.call_count
    assert after_first >= 1, "JWKS must be fetched at least once"

    await _validate_bearer_token(f"Bearer {token2}")
    after_second = jwks_fetch.call_count

    assert (
        after_second == after_first
    ), "Second verification should hit cache, not refetch JWKS"
