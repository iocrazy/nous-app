"""Distribution routes typed in P7: wire parity over real HTTP.

- ``GET  /accounts/{id}/music/charts``         → ``DistributionMusicChartsPage``
- ``POST /accounts/{id}/music/charts/refresh`` → ``DistributionMusicHarvestResult``
- ``POST /accounts/{id}/refresh``              → ``DistributionAccountRow``
- ``GET  /music/seed.png``                     → declared ``image/png`` bytes
- ``POST /webhook/douyin``                     → Douyin's receipt shapes
- ``GET  /accounts/oauth/{platform}/callback`` → a 307 (see
  ``test_distribution_oauth_callback.py``)

Rows come from the real repository code applied to ORM rows with every
column set (``tests/api/wire_parity.py``); each body must equal
``jsonable_encoder`` of what the handler returned before the models existed.
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
import app.api.distribution_webhook as wh
import app.repositories.music_charts_repository as mcr
import app.services.distribution.module_config as mc
from app.core.deps import get_current_user
from app.main import app
from app.models import MusicCharts, MusicChartTracks, SocialAccounts
from app.repositories.social_accounts_repository import _public_row
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    SAMPLE_TS,
    assert_wire_unchanged,
    sample_orm,
    sample_row,
)

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
ACCOUNT_ID = 7788


@pytest.fixture
def client(monkeypatch):
    async def enabled() -> bool:
        return True

    async def authorized(account_id, user):
        return {"id": str(account_id)}

    monkeypatch.setattr(mc, "is_module_enabled", enabled)
    monkeypatch.setattr(dr, "_authorize_account", authorized)
    app.dependency_overrides[get_current_user] = lambda: {"id": USER}
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


# --------------------------------------------------------------------------- #
# Music charts
# --------------------------------------------------------------------------- #


def _chart(chart_id: int, **overrides: Any) -> MusicCharts:
    return sample_orm(MusicCharts, id=chart_id, account_id=ACCOUNT_ID, **overrides)


def _track(chart_id: int, position: int, **overrides: Any) -> MusicChartTracks:
    return sample_orm(
        MusicChartTracks, chart_id=chart_id, position=position, **overrides
    )


class _Scalars:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


def _patch_chart_reads(monkeypatch, charts, tracks) -> None:
    results = [charts, tracks]

    class _Session:
        async def execute(self, stmt):
            return _Scalars(results.pop(0) if results else [])

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(mcr, "read_scope", _scope)


def _fixture_charts():
    good = _chart(SAMPLE_BIGINT + 1, ok=True, fetched_at=SAMPLE_TS)
    # A failed tab keeping yesterday's tracks, never successfully read.
    failed = _chart(
        SAMPLE_BIGINT + 2, ok=False, error="timeout", fetched_at=None, checked_at=None
    )
    tracks = [
        _track(good.id, 0),
        _track(good.id, 1, user_count=None),
        _track(failed.id, 0, user_count=0),
    ]
    return [good, failed], tracks


@pytest.mark.asyncio
async def test_list_music_charts_wire(client, monkeypatch) -> None:
    charts, tracks = _fixture_charts()
    _patch_chart_reads(monkeypatch, list(charts), list(tracks))
    expected_charts = await mcr.MusicChartsRepository().list_charts(ACCOUNT_ID)
    _patch_chart_reads(monkeypatch, list(charts), list(tracks))

    async def newest(self, account_id):
        return SAMPLE_TS

    monkeypatch.setattr(mcr.MusicChartsRepository, "newest_fetch", newest)
    from app.services.distribution.music_charts import DEFAULT_TTL_HOURS, is_stale

    resp = client.get(f"/api/v1/distribution/accounts/{ACCOUNT_ID}/music/charts")
    raw = {
        "charts": expected_charts,
        "last_success_at": SAMPLE_TS.isoformat(),
        "stale": is_stale(SAMPLE_TS),
        "never_harvested": False,
        "ttl_hours": DEFAULT_TTL_HOURS,
    }
    assert_wire_unchanged(resp, raw)
    assert resp.json()["charts"][0]["tracks"][1]["user_count"] is None


@pytest.mark.asyncio
async def test_list_music_charts_cold_cache_wire(client, monkeypatch) -> None:
    _patch_chart_reads(monkeypatch, [], [])

    async def newest(self, account_id):
        return None

    monkeypatch.setattr(mcr.MusicChartsRepository, "newest_fetch", newest)
    from app.services.distribution.music_charts import DEFAULT_TTL_HOURS

    resp = client.get(f"/api/v1/distribution/accounts/{ACCOUNT_ID}/music/charts")
    raw = {
        "charts": [],
        "last_success_at": None,
        "stale": True,
        "never_harvested": True,
        "ttl_hours": DEFAULT_TTL_HOURS,
    }
    assert_wire_unchanged(resp, raw)


HARVEST_RESULTS = [
    # success with a write summary
    {
        "success": True,
        "status": "success",
        "message": None,
        "detail": {"charts_read": 12},
        "stored": {"stored": 11, "kept": 1, "tracks": 230},
    },
    # typed refusal: busy account, nothing written
    {
        "success": False,
        "status": "failed",
        "message": "another browser session is already running for this account",
        "detail": {"reason": "account_busy"},
        "stored": {},
    },
    # decrypt failure (SessionOpResult.to_dict + stored)
    {
        "success": False,
        "status": "failed",
        "message": "session_state could not be decrypted",
        "detail": {"error_kind": "decrypt_failed"},
        "stored": {},
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("result", HARVEST_RESULTS)
async def test_refresh_music_charts_wire(client, monkeypatch, result) -> None:
    import app.services.distribution.music_charts as music_charts

    async def harvest(account_id, *, base_url):
        return dict(result)

    monkeypatch.setattr(music_charts, "harvest_account_charts", harvest)
    resp = client.post(
        f"/api/v1/distribution/accounts/{ACCOUNT_ID}/music/charts/refresh"
    )
    assert_wire_unchanged(resp, result)


# --------------------------------------------------------------------------- #
# Account refresh
# --------------------------------------------------------------------------- #


def _public(**overrides: Any) -> Dict[str, Any]:
    row = sample_row(SocialAccounts)
    row.update(id=ACCOUNT_ID, created_by=uuid.UUID(USER), **overrides)
    return _public_row(row)


@pytest.mark.asyncio
async def test_refresh_session_account_wire(client, monkeypatch) -> None:
    from unittest.mock import AsyncMock, MagicMock

    import app.services.distribution.registry as registry

    public = _public(
        auth_type="session", deleted_at=None, token_expires_at=None, platform="douyin"
    )
    repo = MagicMock()
    repo.get_with_session = AsyncMock(
        return_value={"id": ACCOUNT_ID, "platform": "douyin", "auth_type": "session"}
    )
    repo.update_profile = AsyncMock()
    repo.update_session_state = AsyncMock()
    repo.get_public = AsyncMock(return_value=public)
    monkeypatch.setattr(dr, "accounts_repo", repo)
    adapter = MagicMock()
    adapter.validate_session = AsyncMock(
        return_value={"success": True, "status": "ok", "message": "", "detail": {}}
    )
    monkeypatch.setattr(registry, "get_session_adapter", lambda _p: adapter)

    resp = client.post(f"/api/v1/distribution/accounts/{ACCOUNT_ID}/refresh")
    assert_wire_unchanged(resp, public)
    body = resp.json()
    assert "access_token" not in body and "session_state" not in body
    # isoformat, not "…Z"
    assert body["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_refresh_session_account_unbound_meanwhile_is_typed_404(
    client, monkeypatch
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    import app.services.distribution.registry as registry

    repo = MagicMock()
    repo.get_with_session = AsyncMock(
        return_value={"id": ACCOUNT_ID, "platform": "douyin", "auth_type": "session"}
    )
    repo.update_profile = AsyncMock()
    repo.update_session_state = AsyncMock()
    repo.get_public = AsyncMock(return_value=None)
    monkeypatch.setattr(dr, "accounts_repo", repo)
    adapter = MagicMock()
    adapter.validate_session = AsyncMock(
        return_value={"success": True, "status": "ok", "message": "", "detail": {}}
    )
    monkeypatch.setattr(registry, "get_session_adapter", lambda _p: adapter)

    resp = client.post(f"/api/v1/distribution/accounts/{ACCOUNT_ID}/refresh")
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_refresh_oauth_account_wire(client, monkeypatch) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.distribution.douyin_adapter import DouyinCredentials

    upserted = _public(auth_type="oauth", deleted_at=None)
    repo = MagicMock()
    repo.get_with_session = AsyncMock(
        return_value={"id": ACCOUNT_ID, "platform": "douyin", "auth_type": "oauth"}
    )
    repo.get_with_tokens = AsyncMock(
        return_value={
            "platform": "douyin",
            "refresh_token": "rt",
            "scope_type": "user",
            "scope_id": USER,
            "platform_user_id": "oid",
            "username": "creator",
            "avatar_url": None,
            "created_by": USER,
        }
    )
    repo.upsert_account = AsyncMock(return_value=upserted)
    monkeypatch.setattr(dr, "accounts_repo", repo)

    async def creds():
        return DouyinCredentials("ck", "cs", "https://x/cb")

    adapter = MagicMock()
    adapter.refresh_token = AsyncMock(return_value={"access_token": "at2"})
    monkeypatch.setattr(dr, "get_douyin_credentials", creds)
    monkeypatch.setattr(dr, "get_adapter", lambda platform, c: adapter)

    resp = client.post(f"/api/v1/distribution/accounts/{ACCOUNT_ID}/refresh")
    assert_wire_unchanged(resp, upserted)


# --------------------------------------------------------------------------- #
# seed.png and the webhook
# --------------------------------------------------------------------------- #


def test_seed_png_is_png_bytes(client) -> None:
    from app.services.distribution.music_charts import seed_png

    resp = client.get("/api/v1/distribution/music/seed.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == seed_png()


def _signed(body: str) -> Dict[str, str]:
    return {"x-douyin-signature": hashlib.sha1(("secret" + body).encode()).hexdigest()}


@pytest.fixture
def webhook_creds(monkeypatch):
    from app.services.distribution.douyin_adapter import DouyinCredentials

    async def creds():
        return DouyinCredentials("ck", "secret", "https://x/cb")

    monkeypatch.setattr(wh, "get_douyin_credentials", creds)


@pytest.mark.parametrize("challenge", [123, "abc"])
def test_webhook_challenge_echo_wire(client, webhook_creds, challenge) -> None:
    import json

    body = json.dumps({"event": "verify_webhook", "challenge": challenge})
    resp = client.post(
        "/api/v1/distribution/webhook/douyin", content=body, headers=_signed(body)
    )
    assert_wire_unchanged(resp, {"challenge": challenge})


def test_webhook_other_event_ack_wire(client, webhook_creds) -> None:
    body = '{"event":"some_other_event"}'
    resp = client.post(
        "/api/v1/distribution/webhook/douyin", content=body, headers=_signed(body)
    )
    assert_wire_unchanged(resp, {"msg": "ok"})


def test_openapi_declares_non_json_routes_honestly() -> None:
    paths = app.openapi()["paths"]
    seed = paths["/api/v1/distribution/music/seed.png"]["get"]["responses"]["200"]
    assert list(seed["content"]) == ["image/png"]
    callback = paths["/api/v1/distribution/accounts/oauth/{platform}/callback"]["get"]
    assert "307" in callback["responses"]
    assert all(
        "application/json" not in resp.get("content", {})
        for code, resp in callback["responses"].items()
        if code.startswith("2") or code.startswith("3")
    )
