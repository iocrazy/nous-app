"""There is ONE provider registry. ``AIProviderFactory`` projects it.

WHAT THIS REPLACES
──────────────────
``AIProviderFactory._registry`` used to be a hand-written dict sitting next to,
and completely disconnected from, ``provider_protocols`` — the registry the
admin dropdown reads and ``adapters.factory`` derives its key set from. Adding
``nous`` to the second did not add it to the first, nothing noticed, and every
transcription raised ``Unknown provider: nous`` until 2026-09-22 (PR #2375).

The fix there was the missing entry. The fix here is the shape: the dict is now
DERIVED, so the same mistake has nowhere to happen.

WHY A SECOND DICT STILL EXISTS
──────────────────────────────
Five keys have an ``AIProvider`` but no protocol: ``volcengine`` (an alias for
doubao), ``minimax``, ``kimi``, ``ollama``, ``lmstudio``. They are BYOK provider
cards a user can configure in Settings → AI Providers; they are not platform
catalog protocols, and promoting them would not be a refactor — it would add
them to ``chat_provider_keys()``, which feeds ``adapters.factory``'s dispatch
ladder and the admin's protocol dropdown. That is a behaviour change and does
not belong in the same commit as a rewiring.

So they live in ONE named, documented dict instead of being mixed into the
hand-written list. The difference that matters: a protocol can no longer be
silently absent — it is either mapped or explicitly exempt, and
``test_every_protocol_is_mapped_or_exempt`` rejects a third state.

THE PIN BELOW IS THE PROOF OF BEHAVIOUR-PRESERVATION
────────────────────────────────────────────────────
``_EXPECTED`` is the registry exactly as it read before this refactor, copied
key by key. If the derivation drops, adds or re-points anything, this fails.
"""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp
from app.services.ai.providers.ai_provider import (
    AIProviderFactory,
    DeepSeekProvider,
    DoubaoProvider,
    KimiProvider,
    LMStudioProvider,
    MiniMaxProvider,
    ModelScopeProvider,
    OllamaProvider,
    OpenAIProvider,
    QwenProvider,
)

# The registry as it read on master before this commit, verbatim.
_EXPECTED = {
    "openai": OpenAIProvider,
    "nous": OpenAIProvider,
    "deepseek": DeepSeekProvider,
    "doubao": DoubaoProvider,
    "volcengine": DoubaoProvider,
    "minimax": MiniMaxProvider,
    "kimi": KimiProvider,
    "qwen": QwenProvider,
    "modelscope": ModelScopeProvider,
    "ollama": OllamaProvider,
    "lmstudio": LMStudioProvider,
}


@pytest.mark.unit
def test_registry_is_unchanged_by_the_rewiring():
    assert AIProviderFactory._registry == _EXPECTED


@pytest.mark.unit
def test_the_protocol_half_really_comes_from_the_protocols():
    """Not just "the dict has the right contents" — that a hand-written dict
    would also satisfy. This asserts the protocol-derived entries are keyed by
    the protocol objects themselves, so a new protocol with a provider class
    lands in the registry without anyone editing it.
    """
    from app.services.ai.providers.ai_provider import _protocol_providers

    derived = _protocol_providers()
    assert derived, "nothing derived from provider_protocols — the wiring is dead"
    for key, cls in derived.items():
        protocol = next(p for p in pp.all_protocols() if p.key == key)
        assert protocol.ai_provider_name == cls.__name__
        assert _EXPECTED[key] is cls


@pytest.mark.unit
def test_every_protocol_is_mapped_or_exempt():
    """No third state. A protocol either names an AIProvider or is listed as
    having none — the check that would have caught the `nous` gap on the day it
    was introduced rather than six hours later in production.
    """
    from app.services.ai.providers.ai_provider import PROTOCOLS_WITHOUT_AI_PROVIDER

    unclassified = [
        p.key
        for p in pp.all_protocols()
        if not p.ai_provider_name and p.key not in PROTOCOLS_WITHOUT_AI_PROVIDER
    ]
    assert not unclassified, (
        f"protocols that neither name an AIProvider nor are exempt: "
        f"{unclassified}. Set ai_provider_name, or add the key to "
        f"PROTOCOLS_WITHOUT_AI_PROVIDER with the reason it has none."
    )


@pytest.mark.unit
def test_exemptions_are_honest():
    """An exempt protocol must genuinely have no provider class — otherwise the
    exemption list becomes the place where a real mapping goes to hide."""
    from app.services.ai.providers.ai_provider import PROTOCOLS_WITHOUT_AI_PROVIDER

    lying = [
        p.key
        for p in pp.all_protocols()
        if p.key in PROTOCOLS_WITHOUT_AI_PROVIDER and p.ai_provider_name
    ]
    assert not lying, f"{lying} are exempt but DO name an AIProvider"


@pytest.mark.unit
def test_byok_only_keys_are_named_and_disjoint_from_protocols():
    """The five extras must stay extras. If one of them ever becomes a protocol,
    it has to be removed from here in the same change — two sources for one key
    is the exact shape this refactor removes.
    """
    from app.services.ai.providers.ai_provider import BYOK_ONLY_PROVIDERS

    protocol_keys = {p.key for p in pp.all_protocols()}
    overlap = protocol_keys & set(BYOK_ONLY_PROVIDERS)
    assert not overlap, (
        f"{sorted(overlap)} are both a protocol and a BYOK-only extra. Delete "
        f"the extra and let the protocol carry it."
    )


@pytest.mark.unit
def test_unknown_key_still_raises_with_the_available_list():
    """The error text is load-bearing — it is what made the 2026-09-22 incident
    diagnosable from a single persisted probe message."""
    with pytest.raises(ValueError) as excinfo:
        AIProviderFactory.get_provider("not-a-provider", {})
    message = str(excinfo.value)
    assert "not-a-provider" in message
    assert "nous" in message and "openai" in message
