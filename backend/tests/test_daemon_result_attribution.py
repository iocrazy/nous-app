"""A daemon-produced image must be distinguishable from an uploaded one.

Today every daemon product lands as `origin_kind='canvas_upload'` with
`params={produced_by, job_id}` — prompt, model, ratio, canvas and node all
empty. In the database a generated image and a hand-uploaded one look
identical, so nothing about daemon generations can be audited at all.

The attribution rides on the upload ticket, which already exists, is
already one-shot, and already carries the owner. No new table.
"""

from __future__ import annotations

import io
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

r = sys.modules["app.api.codex_daemon_router"]

ATTRIBUTION = {
    "canvas_id": 42,
    "node_id": "n1",
    "prompt": "a cat",
    "model": "gpt-image-2",
    "provider": "codex-local",
    "requested": {"ratio": "16:9"},
    "effective": {"ratio": "16:9"},
    "dropped": [],
}


class _FakeRedis:
    """Dict-backed stand-in for the async client.

    ``get_async_redis`` builds its client with ``decode_responses=True``, so
    the real one hands back ``str`` — this one does the same rather than the
    ``bytes`` a default client would return.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def execute_command(self, command: str, *args):
        if command.upper() != "GETDEL":
            raise AssertionError(f"unexpected redis command: {command}")
        self.expires.pop(args[0], None)
        return self.store.pop(args[0], None)


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    client = _FakeRedis()

    async def _get():
        return client

    monkeypatch.setattr(r, "get_async_redis", _get)
    return client


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _png(width: int, height: int) -> bytes:
    """A real image — the outcome block is built from real pixels, never
    from what the caller claimed the shape was."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def captured_register(monkeypatch) -> dict:
    """Capture the real call ``_register_daemon_result`` makes — the origin
    it builds is exactly what this task is about."""
    from app.services.library import generated_media_service as gms

    captured: dict = {}

    async def fake_register(**kw):
        captured.update(kw)
        return {"id": 99}

    monkeypatch.setattr(gms, "register_generated_media", fake_register)
    return captured


@pytest.mark.asyncio
async def test_ticket_carries_the_job_attribution(fake_redis):
    from app.api.codex_daemon_router import mint_upload_ticket

    ticket = await mint_upload_ticket(
        user_id="u1", scope_id=7, job_id="j1", attribution=ATTRIBUTION
    )
    stored = json.loads(await fake_redis.get(f"codex_upload:{ticket}"))
    assert stored["attribution"]["canvas_id"] == 42
    assert stored["attribution"]["requested"]["ratio"] == "16:9"
    # The owner the ticket already carried must survive the addition.
    assert stored["user_id"] == "u1" and stored["scope_id"] == 7


@pytest.mark.asyncio
async def test_upload_files_the_product_as_a_canvas_run_not_an_upload(
    fake_redis, client, captured_register
):
    """origin_kind must say what this is; params must carry the four keys."""
    from app.api.codex_daemon_router import mint_upload_ticket

    ticket = await mint_upload_ticket(
        user_id="u1", scope_id=7, job_id="j1", attribution=ATTRIBUTION
    )
    resp = await client.post(
        "/api/v1/codex-daemon/upload",
        files={"file": ("out.png", _png(1536, 864), "image/png")},
        data={"ticket": ticket},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["gen_id"] == "99"

    origin = captured_register["origin"]
    assert origin.kind == "canvas_run"
    assert origin.canvas_id == 42 and origin.node_id == "n1"
    assert origin.prompt == "a cat" and origin.model == "gpt-image-2"
    assert origin.provider == "codex-local"
    assert origin.derivation_kind == "image_gen"
    assert origin.params["requested"] == {"ratio": "16:9"}
    assert origin.params["effective"] == {"ratio": "16:9"}
    assert origin.params["dropped"] == []
    assert origin.params["measured"] == {"width": 1536, "height": 864}
    assert origin.params["honored"] is True
    assert origin.params["produced_by"] == "codex-daemon"  # kept as a note
    assert origin.params["job_id"] == "j1"


@pytest.mark.asyncio
async def test_a_product_whose_shape_missed_is_recorded_as_not_honored(
    fake_redis, client, captured_register
):
    """The verdict is a measurement, not the daemon's word for it — a
    portrait answer to a 16:9 request must read as ``honored: false``."""
    from app.api.codex_daemon_router import mint_upload_ticket

    ticket = await mint_upload_ticket(
        user_id="u1", scope_id=7, job_id="j1", attribution=ATTRIBUTION
    )
    resp = await client.post(
        "/api/v1/codex-daemon/upload",
        files={"file": ("out.png", _png(864, 1536), "image/png")},
        data={"ticket": ticket},
    )
    assert resp.status_code == 200, resp.text
    params = captured_register["origin"].params
    assert params["measured"] == {"width": 864, "height": 1536}
    assert params["honored"] is False


@pytest.mark.asyncio
async def test_a_ticket_without_attribution_still_uploads(
    fake_redis, client, captured_register
):
    """Older daemons / other callers keep working; the record is just thinner."""
    from app.api.codex_daemon_router import mint_upload_ticket

    ticket = await mint_upload_ticket(user_id="u1", scope_id=7, job_id="j1")
    resp = await client.post(
        "/api/v1/codex-daemon/upload",
        files={"file": ("out.png", _png(64, 64), "image/png")},
        data={"ticket": ticket},
    )
    assert resp.status_code == 200, resp.text
    origin = captured_register["origin"]
    assert origin.kind == "canvas_upload"
    assert origin.params == {"produced_by": "codex-daemon", "job_id": "j1"}
    assert origin.canvas_id is None and origin.prompt is None


@pytest.mark.asyncio
async def test_dispatch_forwards_attribution_to_the_ticket():
    """The ticket is the only channel the daemon's upload can read this on."""
    from app.services.codex.daemon_dispatch import dispatch_to_daemon

    minted: dict = {}

    def fake_mint(**kw):
        minted.update(kw)
        return "TICKET"

    class _T:
        async def is_online(self, user_id: str) -> bool:
            return True

        async def send_job(self, user_id: str, job: dict) -> None:
            pass

        async def wait_result(self, job_id: str, timeout_s: float) -> dict:
            return {"gen_id": "991"}

    await dispatch_to_daemon(
        user_id="u1",
        scope_id=42,
        kind="image",
        payload={"prompt": "a cat"},
        transport=_T(),
        mint_ticket=fake_mint,
        attribution=ATTRIBUTION,
        timeout_s=5,
    )
    assert minted["attribution"] == ATTRIBUTION


@pytest.mark.asyncio
async def test_daemon_branch_hands_the_generation_contract_to_the_ticket():
    """Without this wiring every test above can pass while production still
    files daemon products as anonymous uploads."""
    from app.services.ai.provider_protocols.base import ProviderCapabilities
    from app.workflows.canvas_generation import generate_canvas_media_step

    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "99"}

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("codex", "gpt-image-2")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(
                return_value=ProviderCapabilities(
                    ratios=frozenset({"16:9"}),
                    quality=True,
                    resolution=False,
                    max_refs=9,
                    negative=False,
                    video_modes=frozenset(),
                    honours_ratio="prompt_hint",
                )
            ),
        ),
    ):
        await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="codex-local-image",
            params={"ratio": "16:9", "quality": "high", "negative": "blurry"},
            source_url=None,
            user_id="u1",
            canvas_id=42,
            node_id="n1",
        )

    attribution = captured["attribution"]
    assert attribution["canvas_id"] == 42 and attribution["node_id"] == "n1"
    assert attribution["prompt"] == "a cat"
    assert attribution["provider"] == "codex-local"
    assert attribution["model"] == "codex-local-image"
    assert attribution["requested"]["ratio"] == "16:9"
    assert attribution["effective"]["ratio"] == "16:9"
    # negative is not in this provider's capabilities → dropped, and the
    # record must be able to say "we never sent it".
    assert attribution["requested"]["negative"] == "blurry"
    assert "negative" not in attribution["effective"]
    assert attribution["dropped"] == ["negative"]
    # The whole payload must NOT ride along: it carries ref_urls and the
    # augmented prompt, and this sits in Redis for the ticket's TTL.
    assert set(attribution) == {
        "canvas_id",
        "node_id",
        "prompt",
        "model",
        "provider",
        "requested",
        "effective",
        "dropped",
    }
