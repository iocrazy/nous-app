"""A generation that already IS a resource promotes without a source grant.

C1: ``save_chat_temp_upload`` and ``backfill_generated_inbox`` register the
resource itself into the inbox — ``origin_kind='chat_upload'``,
``promoted_resource_id`` set, ``conversation_id`` NULL (there is no
conversation for a session-less upload, and the backfill can never recover
one). The old order ran the chat_upload conversation check BEFORE the
already-promoted short-circuit, so every one of those rows raised
``PermissionError`` on Save / Save as Asset.

The short-circuit copies nothing — it hands back a resource that exists — so
it needs no source-CONVERSATION grant. It still needs the caller to be able
to read the source SCOPE: without that, any gen_id would resolve to its
resource for any caller.
"""

from unittest.mock import AsyncMock

import pytest

import app.services.library.promote_generated_media_service as svc_mod

GEN_SCOPE = 42
PERSONAL = 999


def _gen_row(**over):
    base = {
        "id": "7",
        "scope_id": str(GEN_SCOPE),
        "creator_id": "u-uuid",
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "teams/42/temp/abc.png",
        "origin_kind": "chat_upload",
        "conversation_id": None,
        "promoted_resource_id": "555",
    }
    base.update(over)
    return base


def _wire(monkeypatch, *, gen, is_team_member=True, resource=None):
    conv_repo = AsyncMock()
    conv_repo.is_team_member.return_value = is_team_member
    conv_repo.is_member.return_value = True

    async def _personal(_user_id):
        return str(PERSONAL)

    monkeypatch.setattr(svc_mod, "get_conversation_repository", lambda: conv_repo)
    monkeypatch.setattr(svc_mod, "_resolve_personal_team_id", _personal)

    svc = svc_mod.PromoteGeneratedMediaService()

    async def _get_by_id(_gen_id):
        return gen

    async def _get_resource(_rid):
        return resource

    svc.gen_repo.get_by_id = _get_by_id
    svc.res_repo.get_resource_by_id = _get_resource
    return svc, conv_repo


async def test_already_promoted_chat_upload_without_conversation_returns_resource(
    monkeypatch,
):
    svc, conv_repo = _wire(
        monkeypatch,
        gen=_gen_row(),
        resource={"id": 555, "filename": "abc.png"},
    )
    out = await svc.promote(gen_id=7, user_id="u-1", target_scope_id=GEN_SCOPE)
    assert out == {"id": 555, "filename": "abc.png"}
    # the conversation-membership path must not even be consulted
    conv_repo.is_member.assert_not_awaited()


async def test_short_circuit_falls_back_to_the_id_when_the_resource_is_gone(
    monkeypatch,
):
    svc, _ = _wire(monkeypatch, gen=_gen_row(), resource=None)
    out = await svc.promote(gen_id=7, user_id="u-1", target_scope_id=GEN_SCOPE)
    assert out == {"id": "555"}


async def test_short_circuit_is_not_an_open_door(monkeypatch):
    """A caller who cannot read the source scope still gets PermissionError.

    Otherwise hoisting the short-circuit would turn any guessed gen_id into a
    resource lookup for any authenticated user.
    """
    svc, _ = _wire(
        monkeypatch,
        gen=_gen_row(),
        is_team_member=False,
        resource={"id": 555},
    )
    with pytest.raises(PermissionError):
        await svc.promote(gen_id=7, user_id="u-1", target_scope_id=PERSONAL)


async def test_unpromoted_chat_upload_without_conversation_still_raises(monkeypatch):
    """The real promote path is unchanged: no conversation, no copy."""
    svc, _ = _wire(
        monkeypatch,
        gen=_gen_row(promoted_resource_id=None),
        resource=None,
    )
    with pytest.raises(PermissionError, match="conversation_id"):
        await svc.promote(gen_id=7, user_id="u-1", target_scope_id=GEN_SCOPE)
