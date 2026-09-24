import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.distribution_webhook as wh


def test_verify_signature_roundtrip():
    body = '{"event":"create_video"}'
    good = hashlib.sha1(("secret" + body).encode()).hexdigest()
    assert wh.verify_douyin_signature("secret", body, good) is True
    assert wh.verify_douyin_signature("secret", body, "deadbeef") is False


def _client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(wh.router, prefix="/api/v1")

    from app.services.distribution.douyin_adapter import DouyinCredentials

    async def fake_creds():
        return DouyinCredentials("ck", "secret", "https://x/cb")

    monkeypatch.setattr(wh, "get_douyin_credentials", fake_creds)
    return TestClient(app)


def test_webhook_bad_signature_403(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/api/v1/distribution/webhook/douyin",
        content='{"event":"create_video"}',
        headers={"x-douyin-signature": "wrong"},
    )
    assert resp.status_code == 403


def test_webhook_create_video_flips_pending_share(monkeypatch):
    client = _client(monkeypatch)
    flipped = {}

    async def fake_find(share_id):
        return (
            {"id": "99", "task_id": "77", "status": "pending_share"}
            if share_id == "sh1"
            else None
        )

    async def fake_set(account_row_id, status, **fields):
        flipped.update(account_row_id=account_row_id, status=status, **fields)

    async def fake_reaggregate(task_id):
        flipped["reaggregated"] = task_id

    monkeypatch.setattr(wh.publish_repo, "find_task_account_by_share_id", fake_find)
    monkeypatch.setattr(wh.publish_repo, "set_account_status", fake_set)
    monkeypatch.setattr(wh, "_reaggregate_task_tracking", fake_reaggregate)

    body = '{"event":"create_video","content":"{\\"share_id\\":\\"sh1\\",\\"item_id\\":\\"it9\\"}"}'
    sig = __import__("hashlib").sha1(("secret" + body).encode()).hexdigest()
    resp = client.post(
        "/api/v1/distribution/webhook/douyin",
        content=body,
        headers={"x-douyin-signature": sig},
    )
    assert resp.status_code == 200
    assert flipped["status"] == "success"
    assert flipped["platform_item_id"] == "it9"
    assert flipped["reaggregated"] == "77"


def test_webhook_verify_event_returns_challenge(monkeypatch):
    client = _client(monkeypatch)
    body = '{"event":"verify_webhook","challenge":123}'
    sig = __import__("hashlib").sha1(("secret" + body).encode()).hexdigest()
    resp = client.post(
        "/api/v1/distribution/webhook/douyin",
        content=body,
        headers={"x-douyin-signature": sig},
    )
    assert resp.status_code == 200 and resp.json()["challenge"] == 123


# --- fail closed without a usable secret -------------------------------------


def _client_with_secret(monkeypatch, secret) -> TestClient:
    app = FastAPI()
    app.include_router(wh.router, prefix="/api/v1")

    from app.services.distribution.douyin_adapter import DouyinCredentials

    async def fake_creds():
        if secret is None:
            raise wh.CredentialsNotConfigured("distribution.douyin missing")
        return DouyinCredentials("ck", secret, "https://x/cb")

    monkeypatch.setattr(wh, "get_douyin_credentials", fake_creds)
    return TestClient(app)


def _flip_recorder(monkeypatch) -> dict:
    flipped: dict = {}

    async def fake_find(share_id):
        return {"id": "99", "task_id": "77", "status": "pending_share"}

    async def fake_set(account_row_id, status, **fields):
        flipped["status"] = status

    async def fake_reaggregate(task_id):
        return None

    monkeypatch.setattr(wh.publish_repo, "find_task_account_by_share_id", fake_find)
    monkeypatch.setattr(wh.publish_repo, "set_account_status", fake_set)
    monkeypatch.setattr(wh, "_reaggregate_task_tracking", fake_reaggregate)
    return flipped


_FLIP_BODY = '{"event":"create_video","content":"{\\"share_id\\":\\"sh1\\",\\"item_id\\":\\"i\\"}"}'


def test_blank_secret_rejects_a_signature_anyone_can_compute(monkeypatch):
    """SHA1("" + body) needs no secret. It used to flip the record."""
    client = _client_with_secret(monkeypatch, "")
    flipped = _flip_recorder(monkeypatch)
    forged = hashlib.sha1(_FLIP_BODY.encode()).hexdigest()
    resp = client.post(
        "/api/v1/distribution/webhook/douyin",
        content=_FLIP_BODY,
        headers={"x-douyin-signature": forged},
    )
    assert resp.status_code == 403
    assert flipped == {}


def test_real_secret_still_flips(monkeypatch):
    client = _client_with_secret(monkeypatch, "secret")
    flipped = _flip_recorder(monkeypatch)
    sig = hashlib.sha1(("secret" + _FLIP_BODY).encode()).hexdigest()
    resp = client.post(
        "/api/v1/distribution/webhook/douyin",
        content=_FLIP_BODY,
        headers={"x-douyin-signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json() == {"msg": "ok"}
    assert flipped == {"status": "success"}


def test_unconfigured_credentials_is_403_not_500(monkeypatch):
    client = _client_with_secret(monkeypatch, None)
    flipped = _flip_recorder(monkeypatch)
    resp = client.post(
        "/api/v1/distribution/webhook/douyin",
        content=_FLIP_BODY,
        headers={"x-douyin-signature": "x"},
    )
    assert resp.status_code == 403
    assert flipped == {}


def test_missing_signature_header_is_403(monkeypatch):
    client = _client_with_secret(monkeypatch, "secret")
    resp = client.post("/api/v1/distribution/webhook/douyin", content=_FLIP_BODY)
    assert resp.status_code == 403


def test_non_utf8_body_is_403_not_500(monkeypatch):
    client = _client_with_secret(monkeypatch, "secret")
    resp = client.post(
        "/api/v1/distribution/webhook/douyin",
        content=b"\xff\xfe",
        headers={"x-douyin-signature": "x"},
    )
    assert resp.status_code == 403
