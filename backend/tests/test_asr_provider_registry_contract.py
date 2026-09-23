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


# ─────────────────────────────────────────────────────────────────────────────
# volcengine: a speech key, not the doubao chat client
# ─────────────────────────────────────────────────────────────────────────────
# Until 2026-09-22 ``volcengine`` sat in a side dict pointing at DoubaoProvider,
# described as "an alias for doubao". It is the Volcengine SPEECH product
# (openspeech bigasr / seed-asr, api_key + app_id). Now that it is a protocol
# declaring ``asr``, the parametrized "resolves" test above would happily bless
# DoubaoProvider — so what it resolves TO is pinned here.


@pytest.mark.unit
def test_volcengine_is_asr_only_and_never_a_chat_key():
    proto = next(p for p in pp.all_protocols() if p.key == "volcengine")
    assert proto.model_types == ("asr",)
    assert proto.is_chat_key is False
    assert "volcengine" not in pp.chat_provider_keys()


@pytest.mark.unit
def test_volcengine_resolves_to_the_speech_provider_with_app_id():
    from app.services.ai.providers.ai_provider import (
        DoubaoProvider,
        VolcengineAsrProvider,
    )

    provider = AIProviderFactory.get_provider(
        "volcengine", {"api_key": "k", "app_id": "app-1", "model": "bigasr"}
    )
    assert isinstance(provider, VolcengineAsrProvider)
    assert not isinstance(provider, DoubaoProvider)
    # app_id is half of the old-console credential; dropping it on the way in
    # would turn every old-console key into an auth failure at probe time.
    assert provider.app_id == "app-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_volcengine_transcribe_is_an_explicit_refusal():
    """The real path is ``ai_transcription._run_volcengine_asr``: the API pulls
    a public URL, it does not accept an upload. A provider-level transcribe
    that "works" on a local path would be a lie; this one says where to go."""
    provider = AIProviderFactory.get_provider("volcengine", {"api_key": "k"})
    with pytest.raises(NotImplementedError, match="_run_volcengine_asr"):
        await provider.transcribe("/tmp/a.wav")


class _FakeResp:
    def __init__(self, code: str, message: str = ""):
        self.headers = {"X-Api-Status-Code": code, "X-Api-Message": message}


class _FakeClient:
    """Answers the openspeech submit probe per resource id."""

    calls: list[dict] = []

    def __init__(self, codes: dict[str, str], **_kw):
        self._codes = codes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.calls.append({"url": url, "headers": dict(headers or {})})
        code = self._codes[headers["X-Api-Resource-Id"]]
        message = "resource not granted" if code.startswith("45") else ""
        return _FakeResp(code, message)


def _patch_httpx(monkeypatch, codes: dict[str, str]) -> None:
    import httpx

    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(codes, **kw))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_volcengine_test_connection_runs_the_openspeech_probe(monkeypatch):
    """No special case in test_connection any more — it goes through the
    provider like every other key, and the provider's list_models IS the
    probe. Same result shape as before: the granted models."""
    _patch_httpx(
        monkeypatch, {"volc.seedasr.auc": "45000030", "volc.bigasr.auc": "20000000"}
    )
    res = await AIProviderFactory.test_connection(
        "volcengine", {"api_key": "k", "app_id": "app-1"}
    )
    assert res["success"] is True
    assert res["models"] == ["bigasr"]
    assert all("openspeech" in c["url"] for c in _FakeClient.calls)
    # Old console: app_id + access key headers, not X-Api-Key.
    assert _FakeClient.calls[0]["headers"]["X-Api-App-Key"] == "app-1"
    assert _FakeClient.calls[0]["headers"]["X-Api-Access-Key"] == "k"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_volcengine_probe_failure_and_missing_key(monkeypatch):
    _patch_httpx(
        monkeypatch, {"volc.seedasr.auc": "45000030", "volc.bigasr.auc": "45000030"}
    )
    res = await AIProviderFactory.test_connection("volcengine", {"api_key": "k"})
    assert res["success"] is False
    assert res["error"] == "resource not granted"
    assert _FakeClient.calls[0]["headers"]["X-Api-Key"] == "k"

    res = await AIProviderFactory.test_connection("volcengine", {"api_key": ""})
    assert res["success"] is False
    assert res["models"] is None
    assert res["error"] == "API Key is required"
