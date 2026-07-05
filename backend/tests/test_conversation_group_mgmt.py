"""Permission-matrix tests for ConversationService group management.

Matrix (Feishu-style, user-approved 2026-07-04):
  invite / add agent          any member
  remove regular member/agent admin, owner
  remove admin                owner only
  grant / revoke admin        owner only
  transfer ownership          owner only (old owner → member)
  edit name / visibility      admin, owner
  leave                       any member except owner (must transfer)
  dissolve (archive)          owner only
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.conversation_service import ConversationService

OWNER = "user-owner"
ADMIN = "user-admin"
MEMBER = "user-member"
OUTSIDER = "user-outsider"

ROLES = {OWNER: "owner", ADMIN: "admin", MEMBER: "member"}


def _make_repo(
    conv_type: str = "group", roles: dict[str, str] | None = None
) -> AsyncMock:
    repo = AsyncMock()
    role_map = ROLES if roles is None else roles

    async def get_member_role(*, conversation_id: int, user_id: str):
        return role_map.get(user_id)

    async def is_member(*, conversation_id: int, user_id: str):
        return user_id in role_map

    repo.get_member_role.side_effect = get_member_role
    repo.is_member.side_effect = is_member
    repo.conversation_scope_and_type.return_value = {
        "scope_id": 99,
        "type": conv_type,
    }
    repo.list_members.return_value = []
    repo.remove_user_member.return_value = True
    repo.remove_agent_member.return_value = True
    repo.set_member_role.return_value = True
    repo.transfer_owner.return_value = None
    repo.update_conversation.return_value = {"id": 1, "name": "Renamed"}
    repo.archive_conversation.return_value = True
    return repo


def _svc(repo: AsyncMock) -> ConversationService:
    return ConversationService(repo=repo)


# ── list_members ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_members_requires_membership():
    repo = _make_repo()
    with pytest.raises(PermissionError):
        await _svc(repo).list_members(conversation_id=1, user_id=OUTSIDER)


@pytest.mark.asyncio
async def test_list_members_ok_for_member():
    repo = _make_repo()
    assert await _svc(repo).list_members(conversation_id=1, user_id=MEMBER) == []


# ── remove_member ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,target", [(OWNER, MEMBER), (OWNER, ADMIN), (ADMIN, MEMBER)]
)
async def test_remove_member_allowed(actor: str, target: str):
    repo = _make_repo()
    out = await _svc(repo).remove_member(
        conversation_id=1, user_id=actor, target_user_id=target
    )
    assert out == {"removed": True}
    repo.remove_user_member.assert_awaited_once_with(conversation_id=1, user_id=target)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,target", [(ADMIN, ADMIN), (MEMBER, ADMIN), (MEMBER, MEMBER)]
)
async def test_remove_member_forbidden(actor: str, target: str):
    # ADMIN removing itself is the leave path (allowed) — use a second admin.
    roles = {**ROLES, "user-admin2": "admin"}
    target = "user-admin2" if actor == target == ADMIN else target
    repo = _make_repo(roles=roles)
    if actor == MEMBER and target == MEMBER:
        target = "user-member2"
        roles["user-member2"] = "member"
    with pytest.raises(PermissionError):
        await _svc(repo).remove_member(
            conversation_id=1, user_id=actor, target_user_id=target
        )
    repo.remove_user_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_leave_ok_for_member_and_admin():
    for actor in (MEMBER, ADMIN):
        repo = _make_repo()
        out = await _svc(repo).remove_member(
            conversation_id=1, user_id=actor, target_user_id=actor
        )
        assert out == {"removed": True}


@pytest.mark.asyncio
async def test_owner_cannot_leave_without_transfer():
    repo = _make_repo()
    with pytest.raises(PermissionError, match="transfer"):
        await _svc(repo).remove_member(
            conversation_id=1, user_id=OWNER, target_user_id=OWNER
        )


@pytest.mark.asyncio
async def test_remove_member_target_not_member():
    repo = _make_repo()
    with pytest.raises(ValueError):
        await _svc(repo).remove_member(
            conversation_id=1, user_id=OWNER, target_user_id=OUTSIDER
        )


@pytest.mark.asyncio
async def test_remove_member_rejects_non_group_conversation():
    repo = _make_repo(conv_type="direct_agent")
    with pytest.raises(PermissionError, match="group"):
        await _svc(repo).remove_member(
            conversation_id=1, user_id=OWNER, target_user_id=MEMBER
        )


# ── set_member_role ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "member"])
async def test_owner_sets_role(role: str):
    repo = _make_repo()
    out = await _svc(repo).set_member_role(
        conversation_id=1, user_id=OWNER, target_user_id=MEMBER, role=role
    )
    assert out == {"updated": True, "role": role}
    repo.set_member_role.assert_awaited_once_with(
        conversation_id=1, user_id=MEMBER, role=role
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [ADMIN, MEMBER, OUTSIDER])
async def test_non_owner_cannot_set_role(actor: str):
    repo = _make_repo()
    with pytest.raises(PermissionError):
        await _svc(repo).set_member_role(
            conversation_id=1, user_id=actor, target_user_id=MEMBER, role="admin"
        )
    repo.set_member_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_cannot_set_own_role():
    repo = _make_repo()
    with pytest.raises(ValueError, match="transfer"):
        await _svc(repo).set_member_role(
            conversation_id=1, user_id=OWNER, target_user_id=OWNER, role="member"
        )


# ── transfer_ownership ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_transfers_ownership():
    repo = _make_repo()
    out = await _svc(repo).transfer_ownership(
        conversation_id=1, user_id=OWNER, to_user_id=MEMBER
    )
    assert out == {"transferred": True}
    repo.transfer_owner.assert_awaited_once_with(
        conversation_id=1, from_user_id=OWNER, to_user_id=MEMBER
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [ADMIN, MEMBER])
async def test_non_owner_cannot_transfer(actor: str):
    repo = _make_repo()
    with pytest.raises(PermissionError):
        await _svc(repo).transfer_ownership(
            conversation_id=1, user_id=actor, to_user_id=MEMBER
        )


@pytest.mark.asyncio
async def test_transfer_to_self_rejected():
    repo = _make_repo()
    with pytest.raises(ValueError):
        await _svc(repo).transfer_ownership(
            conversation_id=1, user_id=OWNER, to_user_id=OWNER
        )


# ── remove_agent ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [OWNER, ADMIN])
async def test_remove_agent_allowed(actor: str):
    repo = _make_repo()
    out = await _svc(repo).remove_agent(
        conversation_id=1, user_id=actor, agent_id="agent-uuid"
    )
    assert out == {"removed": True}


@pytest.mark.asyncio
async def test_member_cannot_remove_agent():
    repo = _make_repo()
    with pytest.raises(PermissionError):
        await _svc(repo).remove_agent(
            conversation_id=1, user_id=MEMBER, agent_id="agent-uuid"
        )


# ── update_conversation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [OWNER, ADMIN])
async def test_update_conversation_allowed(actor: str):
    repo = _make_repo()
    out = await _svc(repo).update_conversation(
        conversation_id=1, user_id=actor, name="Renamed", type="public"
    )
    assert out == {"id": 1, "name": "Renamed"}
    repo.update_conversation.assert_awaited_once_with(
        conversation_id=1, name="Renamed", type="public"
    )


@pytest.mark.asyncio
async def test_member_cannot_update_conversation():
    repo = _make_repo()
    with pytest.raises(PermissionError):
        await _svc(repo).update_conversation(
            conversation_id=1, user_id=MEMBER, name="Renamed"
        )


@pytest.mark.asyncio
async def test_update_conversation_rejects_blank_name():
    repo = _make_repo()
    with pytest.raises(ValueError, match="empty"):
        await _svc(repo).update_conversation(
            conversation_id=1, user_id=OWNER, name="   "
        )


# ── dissolve ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_dissolves_group():
    repo = _make_repo()
    out = await _svc(repo).dissolve_conversation(conversation_id=1, user_id=OWNER)
    assert out == {"archived": True}
    repo.archive_conversation.assert_awaited_once_with(conversation_id=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [ADMIN, MEMBER])
async def test_non_owner_cannot_dissolve(actor: str):
    repo = _make_repo()
    with pytest.raises(PermissionError):
        await _svc(repo).dissolve_conversation(conversation_id=1, user_id=actor)
