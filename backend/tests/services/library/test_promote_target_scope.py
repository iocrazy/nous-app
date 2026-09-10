"""Promote files the copy where the generation already lives.

`_registration_scope_id` (canvas_generation.py) already settled this argument
on the REGISTRATION side, and its docstring says why:

    "Before P4 this was always the runner's personal team, which left a team
    board checking its inputs against the team while filing its output in one
    person's private inbox ... Two scopes for one run is not a defensible
    split."

The promote step re-introduced exactly that split one layer later:
`generated_media_router._scope()` resolves the CALLER'S PERSONAL TEAM and
hands it in as the destination, so a team board's generation — correctly filed
in the team's Generated inbox — gets its Tier-2 copy yanked into whichever
member happened to open an editor on it.

The destination is therefore the generation's OWN scope unless a caller names
one. Callers that genuinely choose (the chat attachment endpoint takes
`body.scope_id`; the inbox service is handed the gated `?scope_id=`) keep
choosing, and the existing write-authorisation gate still runs against
whatever was chosen.
"""

from unittest.mock import AsyncMock

import pytest

import app.services.library.promote_generated_media_service as svc_mod

TEAM_SCOPE = 42      # the team board the generation belongs to
PERSONAL = 999       # the caller's own personal team
OTHER_TEAM = 77


def _gen_row(**over):
    base = {
        "id": "7",
        "scope_id": str(TEAM_SCOPE),
        "creator_id": "u-uuid",
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "teams/42/temp/abc.png",
        "origin_kind": "canvas_run",
        "conversation_id": None,
        "promoted_resource_id": None,
    }
    base.update(over)
    return base


class _MissingSource:
    """`materialize()` for a file we deliberately do not provide.

    The destination is decided BEFORE any bytes are read, so failing at the
    read is the cheapest place to stop: it keeps the test off storage, hashing
    and the resources table while still running the whole decision.
    """

    async def __aenter__(self):
        raise FileNotFoundError("no bytes in this test")

    async def __aexit__(self, *_):
        return False


def _wire(monkeypatch, *, gen, is_team_member=True):
    conv_repo = AsyncMock()
    conv_repo.is_team_member.return_value = is_team_member
    conv_repo.is_member.return_value = True

    async def _personal(_user_id):
        return str(PERSONAL)

    monkeypatch.setattr(svc_mod, "get_conversation_repository", lambda: conv_repo)
    monkeypatch.setattr(svc_mod, "_resolve_personal_team_id", _personal)
    monkeypatch.setattr(svc_mod, "materialize", lambda _p: _MissingSource())

    svc = svc_mod.PromoteGeneratedMediaService()

    async def _get_by_id(_gen_id):
        return gen

    svc.gen_repo.get_by_id = _get_by_id
    return svc, conv_repo


def _target_scopes(conv_repo) -> list[int]:
    """Every scope the service asked membership about, in order.

    Two calls happen: the source-READ check, then the target-WRITE check. When
    the destination defaults correctly the two are the same number — that
    equality IS the fix.
    """
    return [int(c.kwargs["team_id"]) for c in conv_repo.is_team_member.await_args_list]


async def test_defaults_to_the_generations_own_scope(monkeypatch):
    svc, conv_repo = _wire(monkeypatch, gen=_gen_row())

    with pytest.raises(ValueError, match="generation file missing"):
        await svc.promote(gen_id=7, user_id="u-1")

    # Both the read gate and the write gate asked about the TEAM the
    # generation lives in. A PERSONAL here is the bug: it means the copy was
    # about to be filed in one member's private library.
    assert _target_scopes(conv_repo) == [TEAM_SCOPE, TEAM_SCOPE]
    assert PERSONAL not in _target_scopes(conv_repo)


async def test_an_explicit_target_still_wins(monkeypatch):
    # The chat-attachment endpoint passes `body.scope_id`, and the inbox
    # service passes the gated `?scope_id=`. Defaulting must not take that
    # away from them.
    svc, conv_repo = _wire(monkeypatch, gen=_gen_row())

    with pytest.raises(ValueError, match="generation file missing"):
        await svc.promote(gen_id=7, user_id="u-1", target_scope_id=OTHER_TEAM)

    assert _target_scopes(conv_repo) == [TEAM_SCOPE, OTHER_TEAM]


async def test_a_non_member_is_refused_rather_than_filed_personally(monkeypatch):
    # The old behaviour had no way to express this: the destination was always
    # the caller's own personal team, so writing "somewhere they may write"
    # was automatic and a stranger's generation quietly became their file.
    svc, conv_repo = _wire(monkeypatch, gen=_gen_row(), is_team_member=False)

    with pytest.raises(PermissionError, match="not authorised to access"):
        await svc.promote(gen_id=7, user_id="u-1")


async def test_a_personal_generation_is_unaffected(monkeypatch):
    # The common case: the generation already lives in the caller's personal
    # team, so defaulting resolves to the same place the old code hardcoded.
    svc, conv_repo = _wire(monkeypatch, gen=_gen_row(scope_id=str(PERSONAL)))

    with pytest.raises(ValueError, match="generation file missing"):
        await svc.promote(gen_id=7, user_id="u-1")

    # Neither gate needed a membership lookup — both scopes ARE the personal
    # team, which each check short-circuits on.
    assert _target_scopes(conv_repo) == [PERSONAL]


def test_the_promote_endpoint_does_not_pin_the_caller_s_personal_scope():
    """The service default is dead code unless the router stops overriding it.

    A source scan rather than a request test: the thing that regresses is one
    argument at one call site, and it regresses silently — the endpoint keeps
    answering 200 while filing the copy in the wrong tenant.

    Read off DISK, not via ``import``: ``app.api.generated_media_router``
    resolves to the re-exported ``APIRouter`` object, not the module.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "app" / "api" / "generated_media_router.py"
    text = src.read_text(encoding="utf-8")

    marker = "async def promote_generation("
    assert marker in text, "promote_generation moved — this scan is now blind"
    body = text[text.index(marker) :]
    # Stop at the next top-level decorator so the scan sees this endpoint only.
    nxt = body.find("\n@router.", 1)
    if nxt != -1:
        body = body[:nxt]
    # The docstring EXPLAINS why the personal scope is not used, so scanning it
    # would match the very words that document the fix. Prose is not code.
    opened = body.find('"""')
    if opened != -1:
        closed = body.find('"""', opened + 3)
        if closed != -1:
            body = body[:opened] + body[closed + 3 :]

    assert "_scope(auth)" not in body, (
        "promote_generation is pinning the caller's personal team again — "
        "the destination is the generation's own scope"
    )
    assert "target_scope_id" not in body, (
        "promote_generation is naming a destination; it should let the service "
        "default to the generation's own scope"
    )
