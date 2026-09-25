"""AI Library MCP servers, approval requests and commitments: wire parity
after they gained response models (OpenAPI P4).

The repositories hand the routes frozen value objects (``UserMCPServer``,
``ApprovalRequest``, ``Commitment``); the fixtures build those with every
field set, then with every optional field empty.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agent_framework.commitments import Commitment
from app.core.deps import get_auth
from app.main import app
from app.repositories.approval_requests_repository import ApprovalRequest
from app.repositories.user_mcp_servers_repository import UserMCPServer
from tests.api.ai_library_wire_helpers import AUTH, USER, fake_auth
from tests.api.wire_parity import SAMPLE_BIGINT, SAMPLE_TS, assert_wire_unchanged

r = sys.modules["app.api.ai_library_router"]

pytestmark = pytest.mark.unit

BASE = "/api/v1/ai-library"
MCP_REPO = (
    "app.repositories.user_mcp_servers_repository.get_user_mcp_servers_repository"
)
APPROVAL_REPO = (
    "app.repositories.approval_requests_repository.get_approval_requests_repository"
)
COMMIT_REPO = "app.repositories.commitment_repository.get_commitment_repository"
SERVER_ID = UUID(int=77)


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _repo(**methods: Any) -> MagicMock:
    repo = MagicMock()
    for name, value in methods.items():
        setattr(repo, name, value)
    return repo


# --------------------------------------------------------------------------- #
# MCP servers
# --------------------------------------------------------------------------- #


def _server(**over: Any) -> UserMCPServer:
    values = dict(
        id=SERVER_ID,
        user_id=UUID(USER),
        name="notion",
        url="https://mcp.example.com/rpc",
        bearer_token="sekret",
        description="Notion bridge",
        enabled=True,
    )
    values.update(over)
    return UserMCPServer(**values)


SERVERS = [_server(), _server(bearer_token=None, description=None, enabled=False)]


@pytest.mark.asyncio
async def test_mcp_list(client) -> None:
    repo = _repo(list_for_user=AsyncMock(return_value=SERVERS))
    with patch(MCP_REPO, lambda: repo):
        raw = await r.list_mcp_servers(auth=AUTH)
        resp = await client.get(f"{BASE}/mcp-servers")
    assert_wire_unchanged(resp, raw)
    assert "sekret" not in resp.text
    assert all("bearer_token" not in item for item in resp.json()["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize("server", SERVERS)
async def test_mcp_create(client, server) -> None:
    repo = _repo(create=AsyncMock(return_value=server))
    body = {"name": "notion", "url": "https://mcp.example.com/rpc"}
    with patch(MCP_REPO, lambda: repo):
        raw = await r.create_mcp_server(payload=r._MCPServerCreate(**body), auth=AUTH)
        resp = await client.post(f"{BASE}/mcp-servers", json=body)
    assert_wire_unchanged(resp, raw, status=201)


@pytest.mark.asyncio
@pytest.mark.parametrize("server", SERVERS)
async def test_mcp_update(client, server) -> None:
    repo = _repo(
        get_by_id=AsyncMock(return_value=server),
        update=AsyncMock(return_value=True),
    )
    with patch(MCP_REPO, lambda: repo):
        raw = await r.update_mcp_server(
            server_id=SERVER_ID,
            payload=r._MCPServerUpdate(enabled=False),
            auth=AUTH,
        )
        resp = await client.patch(
            f"{BASE}/mcp-servers/{SERVER_ID}", json={"enabled": False}
        )
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_mcp_update_row_gone_on_reread_is_a_typed_404(client) -> None:
    """Deleted between the update and the re-read used to be ``200 {}``,
    which the client took for a saved row."""
    repo = _repo(
        get_by_id=AsyncMock(side_effect=[_server(), None]),
        update=AsyncMock(return_value=True),
    )
    with patch(MCP_REPO, lambda: repo):
        resp = await client.patch(
            f"{BASE}/mcp-servers/{SERVER_ID}", json={"enabled": False}
        )
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# --------------------------------------------------------------------------- #
# approval requests
# --------------------------------------------------------------------------- #


def _approval(**over: Any) -> ApprovalRequest:
    values = dict(
        id=UUID(int=5),
        user_id=UUID(USER),
        agent_id=UUID(int=6),
        session_id=str(SAMPLE_BIGINT),
        run_id=str(SAMPLE_BIGINT + 1),
        hook_name="publish_gate",
        reason="About to publish",
        payload={"workflow_id": "wf-9", "n": 1},
        status="pending",
        decided_at=None,
        decided_by=None,
        decision_note=None,
        created_at=SAMPLE_TS,
        expires_at=SAMPLE_TS + timedelta(hours=1),
    )
    values.update(over)
    return ApprovalRequest(**values)


@pytest.mark.asyncio
async def test_approval_list(client) -> None:
    rows = [_approval(), _approval(session_id=None, run_id=None, payload={})]
    repo = _repo(list_pending_for_user=AsyncMock(return_value=rows))
    with patch(APPROVAL_REPO, lambda: repo):
        raw = await r.list_approval_requests(auth=AUTH, limit=50)
        resp = await client.get(f"{BASE}/approval-requests")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["approve", "reject"])
async def test_approval_decisions(client, verb) -> None:
    handler = {
        "approve": r.approve_approval_request,
        "reject": r.reject_approval_request,
    }[verb]
    request_id = UUID(int=5)

    def _fresh_repo():
        return _repo(
            get_by_id=AsyncMock(return_value=_approval(payload={})),
            decide=AsyncMock(return_value=True),
        )

    with patch(APPROVAL_REPO, _fresh_repo):
        raw = await handler(
            request_id=request_id, payload=r._ApprovalDecision(), auth=AUTH
        )
        resp = await client.post(
            f"{BASE}/approval-requests/{request_id}/{verb}", json={}
        )
    assert_wire_unchanged(resp, raw)


# --------------------------------------------------------------------------- #
# commitments
# --------------------------------------------------------------------------- #


def _commitment(**over: Any) -> Commitment:
    values = dict(
        id=SAMPLE_BIGINT,
        agent_id=str(UUID(int=6)),
        user_id=USER,
        session_id=str(UUID(int=8)),
        description="Follow up on the storyboard",
        trigger_type="time",
        trigger_at=SAMPLE_TS,
        trigger_event=None,
        status="pending",
        created_at=SAMPLE_TS,
        fulfilled_at=SAMPLE_TS + timedelta(minutes=5),
        expires_at=SAMPLE_TS + timedelta(days=1),
    )
    values.update(over)
    return Commitment(**values)


@pytest.mark.asyncio
async def test_commitment_list(client) -> None:
    rows = [
        _commitment(),
        _commitment(
            session_id=None,
            trigger_type="event",
            trigger_at=None,
            trigger_event="scene_rendered",
            status="fulfilled",
            created_at=None,
            fulfilled_at=None,
            expires_at=None,
        ),
        _commitment(trigger_type="next_session", trigger_at=None, status="expired"),
    ]
    repo = _repo(list_for_user=AsyncMock(return_value=rows))
    with patch(COMMIT_REPO, lambda: repo):
        raw = await r.list_my_commitments(auth=AUTH, status=None, limit=100)
        resp = await client.get(f"{BASE}/commitments")
    assert_wire_unchanged(resp, raw)
    # A BIGINT id stays a JSON number on this surface (照实).
    assert resp.json()["items"][0]["id"] == SAMPLE_BIGINT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verb, method, status",
    [
        ("fulfill", "mark_fulfilled", "fulfilled"),
        ("cancel", "mark_cancelled", "cancelled"),
    ],
)
async def test_commitment_transitions(client, verb, method, status) -> None:
    handler = {"fulfill": r.fulfill_commitment, "cancel": r.cancel_commitment}[verb]

    def _fresh_repo():
        return _repo(
            get_by_id=AsyncMock(return_value=_commitment()),
            **{method: AsyncMock(return_value=_commitment(status=status))},
        )

    with patch(COMMIT_REPO, _fresh_repo):
        raw = await handler(commitment_id=SAMPLE_BIGINT, auth=AUTH)
        resp = await client.post(f"{BASE}/commitments/{SAMPLE_BIGINT}/{verb}")
    assert_wire_unchanged(resp, raw)
