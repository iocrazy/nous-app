"""``/api/v1/conversations`` management routes: wire parity after they gained
response models (OpenAPI P7).

Each route runs over real HTTP through ``app.main``. The service underneath
is the real ``ConversationService`` over a scripted repository, so the dict
compared against is what the unchanged handler returns for the same call
(``tests/api/wire_parity.py``). The attachment row comes from the ORM mapper
(``sample_row(GeneratedMedia)``) so every column is present with its native
type.
"""

from __future__ import annotations

import io
import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import Headers, UploadFile

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import GeneratedMedia, Resources
from app.schemas.conversation import (
    AgentAdd,
    MarkReadIn,
    MemberAdd,
    MemberRoleSet,
    OwnerTransfer,
)
from app.services.conversation_service import ConversationService
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged, sample_row

r = sys.modules["app.api.conversation_router"]

pytestmark = pytest.mark.unit

OWNER = "00000000-0000-0000-0000-000000000042"
MEMBER = "00000000-0000-0000-0000-000000000043"
CONV = SAMPLE_BIGINT + 7
AGENT_ID = "00000000-0000-0000-0000-0000000000a1"


def _ctx() -> AuthContext:
    return AuthContext(user_id=OWNER, auth_type="jwt")


async def _fake_auth() -> AuthContext:
    return _ctx()


def _repo() -> AsyncMock:
    repo = AsyncMock()
    roles = {OWNER: "owner", MEMBER: "member"}

    async def get_member_role(*, conversation_id: int, user_id: str):
        return roles.get(user_id)

    async def is_member(*, conversation_id: int, user_id: str):
        return user_id in roles

    repo.get_member_role.side_effect = get_member_role
    repo.is_member.side_effect = is_member
    repo.is_team_member.return_value = True
    repo.conversation_scope_and_type.return_value = {
        "scope_id": SAMPLE_BIGINT,
        "type": "group",
        "archived_at": None,
    }
    repo.get_conversation.return_value = {
        "id": CONV,
        "scope_id": SAMPLE_BIGINT,
        "type": "group",
        "history_mode": "shared",
        "last_seq": 3,
    }
    repo.add_members.return_value = 2
    repo.remove_user_member.return_value = True
    repo.remove_agent_member.return_value = False
    repo.set_member_role.return_value = True
    repo.transfer_owner.return_value = None
    repo.archive_conversation.return_value = True
    repo.mark_read.return_value = None
    repo.add_agent_member.return_value = None
    return repo


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth

    async def _allow() -> None:
        return None

    gate = r.router.dependencies[0].dependency
    app.dependency_overrides[gate] = _allow
    # A fresh service per call keeps the handler run and the HTTP run on
    # identical, independent repositories.
    monkeypatch.setattr(
        r, "get_conversation_service", lambda: ConversationService(repo=_repo())
    )
    yield
    app.dependency_overrides.pop(get_auth, None)
    app.dependency_overrides.pop(gate, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


BASE = f"/api/v1/conversations/{CONV}"


@pytest.mark.asyncio
async def test_add_members_wire(client) -> None:
    raw = await r.add_members(CONV, MemberAdd(user_ids=[MEMBER]), _ctx())
    resp = await client.post(f"{BASE}/members", json={"user_ids": [MEMBER]})
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_remove_member_wire(client) -> None:
    raw = await r.remove_member(CONV, MEMBER, _ctx())
    resp = await client.delete(f"{BASE}/members/{MEMBER}")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_set_member_role_wire(client) -> None:
    raw = await r.set_member_role(CONV, MEMBER, MemberRoleSet(role="admin"), _ctx())
    resp = await client.patch(f"{BASE}/members/{MEMBER}/role", json={"role": "admin"})
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_transfer_owner_wire(client) -> None:
    raw = await r.transfer_owner(CONV, OwnerTransfer(to_user_id=MEMBER), _ctx())
    resp = await client.post(f"{BASE}/transfer-owner", json={"to_user_id": MEMBER})
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_remove_agent_wire(client) -> None:
    raw = await r.remove_agent(CONV, AGENT_ID, _ctx())
    resp = await client.delete(f"{BASE}/agents/{AGENT_ID}")
    assert_wire_unchanged(resp, raw)
    assert resp.json() == {"removed": False}


@pytest.mark.asyncio
async def test_dissolve_wire(client) -> None:
    raw = await r.dissolve_conversation(CONV, _ctx())
    resp = await client.delete(BASE)
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_mark_read_wire(client) -> None:
    raw = await r.mark_read(CONV, MarkReadIn(last_read_seq=3), _ctx())
    resp = await client.post(f"{BASE}/read", json={"last_read_seq": 3})
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_add_agent_wire(client) -> None:
    agent = {
        "id": uuid.UUID(AGENT_ID),
        "slug": "helper",
        "user_id": uuid.UUID(OWNER),
        "is_system_preset": False,
        "capability_profile": {"chat": {"enabled": True}},
    }
    agent_repo = AsyncMock()
    agent_repo.get_by_slug.return_value = agent
    with patch(
        "app.services.conversation_service.get_agent_repository",
        return_value=agent_repo,
    ):
        raw = await r.add_agent(CONV, AgentAdd(agent_slug="helper"), _ctx())
        resp = await client.post(f"{BASE}/agents", json={"agent_slug": "helper"})
    assert_wire_unchanged(resp, raw)
    assert resp.json()["agent_id"] == AGENT_ID


def _upload() -> UploadFile:
    return UploadFile(
        file=io.BytesIO(b"\x89PNG"),
        filename="a.png",
        headers=Headers({"content-type": "image/png"}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dirty", [False, True])
async def test_upload_attachment_wire(client, monkeypatch, dirty: bool) -> None:
    row: dict[str, Any] = sample_row(GeneratedMedia)
    if dirty:
        # Nullable columns: a row without them must not 500.
        row.update(mime=None, file_size_bytes=None)
    monkeypatch.setattr(r, "save_chat_image", AsyncMock(return_value=row))
    raw = await r.upload_attachment(CONV, _ctx(), _upload())
    resp = await client.post(
        f"{BASE}/attachments", files={"file": ("a.png", b"\x89PNG", "image/png")}
    )
    assert_wire_unchanged(resp, raw)
    assert resp.json()["id"] == str(row["id"])


@pytest.mark.asyncio
async def test_promote_attachment_wire(client, monkeypatch) -> None:
    resource = sample_row(Resources)

    class _Svc:
        async def promote(self, **_kw: Any) -> dict[str, Any]:
            return resource

    monkeypatch.setattr(r, "PromoteGeneratedMediaService", _Svc)
    body = r.PromoteAttachmentBody(scope_id=SAMPLE_BIGINT)
    raw = await r.promote_attachment(SAMPLE_BIGINT + 1, body, _ctx())
    resp = await client.post(
        f"/api/v1/conversations/attachments/{SAMPLE_BIGINT + 1}/promote",
        json={"scope_id": SAMPLE_BIGINT},
    )
    assert_wire_unchanged(resp, raw)
    assert resp.json() == {"promoted_resource_id": str(resource["id"])}


# --------------------------------------------------------------------------- #
# Malformed ids: 400, never a driver DataError (500)
# --------------------------------------------------------------------------- #


def _uuid_strict_repo() -> AsyncMock:
    """A repository that fails like asyncpg does on a non-UUID bind."""
    repo = _repo()
    original = repo.get_member_role.side_effect

    async def get_member_role(*, conversation_id: int, user_id: str):
        try:
            uuid.UUID(user_id)
        except ValueError:
            # asyncpg's DataError is not a ValueError: nothing maps it to 400.
            raise RuntimeError("invalid input for query argument") from None
        return await original(conversation_id=conversation_id, user_id=user_id)

    repo.get_member_role.side_effect = get_member_role
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("DELETE", f"{BASE}/members/not-a-uuid", None),
        ("PATCH", f"{BASE}/members/not-a-uuid/role", {"role": "admin"}),
        ("POST", f"{BASE}/transfer-owner", {"to_user_id": "not-a-uuid"}),
        ("DELETE", f"{BASE}/agents/not-a-uuid", None),
        ("POST", f"{BASE}/members", {"user_ids": [MEMBER, "not-a-uuid"]}),
    ],
)
async def test_malformed_ids_are_400(client, monkeypatch, method, path, body) -> None:
    repo = _uuid_strict_repo()
    monkeypatch.setattr(
        r, "get_conversation_service", lambda: ConversationService(repo=repo)
    )
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 400, resp.text
    repo.remove_user_member.assert_not_awaited()
    repo.set_member_role.assert_not_awaited()
    repo.transfer_owner.assert_not_awaited()
    repo.remove_agent_member.assert_not_awaited()
    repo.add_members.assert_not_awaited()
