"""Every protocol that claims ASR must be buildable by AIProviderFactory.

THE INCIDENT (2026-09-22, production)
─────────────────────────────────────
Migration 480 moved the self-hosted engine's three catalog rows from
``actual_provider='openai'`` to ``'nous'``. Two of them kept working. The third
did not::

    mediahub-moss-asr | asr | nous | fail |
      Unknown provider: nous. Available: openai, deepseek, doubao, volcengine,
      minimax, kimi, qwen, modelscope, ollama, lmstudio

There are TWO provider registries in this codebase and they are not connected:

  * ``provider_protocols`` — the documented single source. The admin dropdown
    reads it, ``adapters.factory`` derives its key set from it, and a contract
    test (``test_factory_provider_keys_are_registry_derived``) pins that link.
  * ``AIProviderFactory._registry`` — a hand-written dict in
    ``services/ai/providers/ai_provider.py``. Nothing derives it, nothing
    checks it, and ``get_provider`` raises on an unknown key with no fallback.

Adding ``nous`` to the first did not add it to the second, and no test noticed,
because only ONE probe type reaches the second one: ``llm`` and ``embedding``
probe over direct httpx (which is why those two rows stayed green), while
``asr`` goes through ``AIProviderFactory.test_connection``.

The blast radius was not just the red dot. ``resolve_nous_model`` returns
the row's ``actual_provider`` as the provider key, so
``ai_transcription`` → ``WhisperService(provider_key='nous')`` →
``get_provider('nous')`` raised on every real transcription too.

THE INVARIANT
─────────────
A protocol that lists ``asr`` in ``model_types`` is telling the admin UI "you
may file an ASR row under me". An ASR row's ``actual_provider`` is handed
straight to ``AIProviderFactory``. So the two statements have to agree, and
this file is the agreement.

The rest of the protocols are classified explicitly rather than ignored — a new
protocol must answer "does this need an AIProviderFactory entry?" instead of
inheriting silence, the same rule the tool-descriptor allowlist uses.
"""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp
from app.services.ai.providers.ai_provider import (
    PROTOCOLS_WITHOUT_AI_PROVIDER,
    AIProviderFactory,
)

# The exemption list is NOT re-declared here. It lives next to the registry
# it describes (`PROTOCOLS_WITHOUT_AI_PROVIDER` in ai_provider.py) — a second
# copy in the test file would be one more pair of lists that can drift, which
# is the defect this whole area is being cleaned of.


def _asr_protocols() -> list[str]:
    return [p.key for p in pp.all_protocols() if "asr" in p.model_types]


@pytest.mark.unit
def test_the_registry_has_not_gone_empty():
    """Guards the guard. Every assertion below iterates a derived list, so an
    empty one would make this whole file pass while checking nothing."""
    assert _asr_protocols(), "no protocol declares asr — the queries below are vacuous"


@pytest.mark.unit
@pytest.mark.parametrize("key", _asr_protocols())
def test_every_asr_protocol_resolves(key: str):
    """The exact production failure, one test per key.

    ``get_provider`` is called for real rather than checking dict membership:
    an entry pointing at a class that cannot be constructed with
    ``(api_key, base_url, model)`` would satisfy membership and still raise
    here — which is the thing that actually happens at runtime.
    """
    provider = AIProviderFactory.get_provider(
        key, {"api_key": "k", "base_url": "http://engine.invalid/v1", "model": "m"}
    )
    assert provider is not None


@pytest.mark.unit
def test_nous_resolves_to_the_moss_asr_client():
    """``nous`` must land on OpenAIProvider specifically, not merely on
    something.

    That class is not a generic stand-in — its ``transcribe`` carries the
    self-hosted server's quirks by name: moss-asr reads the ``context`` form
    field, ignores two OpenAI params, and returns a diarization ``speaker``
    label that ``WhisperService`` only persists when present. Routing ``nous``
    anywhere else would probe green and then transcribe wrong.
    """
    from app.services.ai.providers.ai_provider import OpenAIProvider

    provider = AIProviderFactory.get_provider(
        "nous", {"api_key": "k", "base_url": "http://engine.invalid/v1", "model": "m"}
    )
    assert isinstance(provider, OpenAIProvider)


@pytest.mark.unit
def test_base_url_reaches_the_client():
    """The engine has no public endpoint, so a provider built without the
    row's base_url would silently dial api.openai.com with the engine's key."""
    provider = AIProviderFactory.get_provider(
        "nous", {"api_key": "k", "base_url": "http://engine.invalid/v1", "model": "m"}
    )
    assert "engine.invalid" in str(provider._client.base_url)


@pytest.mark.unit
def test_every_protocol_is_classified():
    """No third state: a protocol either resolves through AIProviderFactory or
    is on the exemption list with a reason."""
    unclassified = [
        p.key
        for p in pp.all_protocols()
        if p.key not in AIProviderFactory._registry
        and p.key not in PROTOCOLS_WITHOUT_AI_PROVIDER
    ]
    assert not unclassified, (
        f"protocols with no AIProviderFactory entry and no exemption: "
        f"{unclassified}. Add a registry entry, or add it to PROTOCOLS_WITHOUT_AI_PROVIDER "
        f"with the reason no ASR row can be filed under it."
    )


@pytest.mark.unit
def test_no_exempt_protocol_claims_asr():
    """The exemption list has to stay honest. A protocol that starts declaring
    ``asr`` while sitting on this list would let the admin file an ASR row
    under a key the transcription path cannot build — the 2026-09-22 failure,
    reintroduced through the escape hatch added to prevent it.
    """
    lying = [
        p.key
        for p in pp.all_protocols()
        if p.key in PROTOCOLS_WITHOUT_AI_PROVIDER and "asr" in p.model_types
    ]
    assert not lying, (
        f"{lying} declare asr but are exempted from AIProviderFactory. An ASR "
        f"row filed under one of these raises 'Unknown provider' at probe time "
        f"and on every real transcription."
    )
