"""Every protocol declares WHOSE credential it runs on.

Admin → AI Models shows one card per ``actual_provider``. Three of those cards
drive the SAME gpt-image binary (``codex`` / ``codex-local`` / ``openai-images``)
and two drive the same dreamina CLI (``jimeng-cli`` / ``jimeng-local``). They
cannot be merged — sharing a ``generation_family`` would let ``db_registry``
build a SERVER-side provider for a row that is supposed to run on the user's
machine, which is the failure the split exists to prevent — so the cards are
permanently near-duplicates and the UI has to say what distinguishes them.

What distinguishes them is exactly one thing: whose credential, and therefore
whose machine and whose bill.

Declared on the protocol rather than derived in the frontend from key names,
for the reason CLAUDE.md gives for the slot table: a mirror that re-encodes the
same knowledge on the other side of the wire drifts, and the copy that drifts
is the one nobody tests. Here the backend is the single source and the admin
page just renders it.

No default: ``ProviderProtocol.credential_kind`` is empty on the base class and
this file rejects empty, so a new protocol must answer the question instead of
inheriting a plausible-looking wrong answer.
"""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp

# The closed vocabulary. Adding a value here is a deliberate act; the admin
# page has a label for each and renders an unknown one as the raw string.
_KINDS = {"api_key", "server_session", "user_device"}

# Pinned per protocol rather than computed, so a change of mind about any one
# of them shows up as a diff in this file — the same reason the prompt pin
# exists. Getting one wrong mislabels a card; getting `user_device` wrong would
# tell an admin that a generation runs on our hardware when it runs on the
# user's (or the reverse), which is also the billing question.
_EXPECTED = {
    "qwen": "api_key",
    "nous": "api_key",
    "openai": "api_key",
    "claude": "api_key",
    "deepseek": "api_key",
    "doubao": "api_key",
    "modelscope": "api_key",
    "ark": "api_key",
    "openai-images": "api_key",
    "codex": "server_session",
    "jimeng-cli": "server_session",
    "codex-local": "user_device",
    "jimeng-local": "user_device",
}


@pytest.mark.unit
def test_every_protocol_declares_a_credential_kind():
    missing = [p.key for p in pp.all_protocols() if not p.credential_kind]
    assert not missing, (
        f"protocols with no credential_kind: {missing}. Declare one — the admin "
        f"card has no other way to tell this provider apart from the one next "
        f"to it that drives the same binary."
    )


@pytest.mark.unit
def test_credential_kinds_are_in_the_closed_vocabulary():
    bad = {
        p.key: p.credential_kind
        for p in pp.all_protocols()
        if p.credential_kind not in _KINDS
    }
    assert not bad, f"unknown credential_kind values: {bad} (allowed: {_KINDS})"


@pytest.mark.unit
def test_registry_matches_the_pinned_map():
    actual = {p.key: p.credential_kind for p in pp.all_protocols()}
    assert actual == _EXPECTED


@pytest.mark.unit
def test_the_three_gpt_image_cards_are_told_apart_by_this_field_alone():
    """The regression this whole field exists for. These three share a binary
    and two of them share a quality-tier set; if their credential_kind ever
    collapses to one value the admin page is back to three cards it cannot
    distinguish."""
    kinds = {
        pp.get_chat_protocol("codex-local").credential_kind,
        next(p for p in pp.all_protocols() if p.key == "codex").credential_kind,
        next(p for p in pp.all_protocols() if p.key == "openai-images").credential_kind,
    }
    assert len(kinds) == 3


@pytest.mark.unit
def test_protocols_endpoint_ships_the_field():
    """Declared-but-not-serialized would leave the admin page deriving it from
    key names again — the mirror this field was added to remove."""
    import asyncio

    from app.api.admin.nous_model_router import list_provider_protocols

    resp = asyncio.run(list_provider_protocols(auth=object()))
    got = {p.key: p.credential_kind for p in resp.protocols}
    assert got == _EXPECTED
