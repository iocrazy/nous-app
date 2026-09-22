"""The self-hosted nous-engine gateway is its own protocol.

Until now its three catalog rows (``nous-qwen3-llm`` /
``nous-qwen3-embedding-8b`` / ``mediahub-moss-asr``, all on
``http://10.0.0.10:8000/v1``) carried ``actual_provider='openai'`` — the
NATIVE OpenAI protocol, whose label the admin page renders verbatim. The rows
worked, because ``OpenAIAdapter`` and ``QwenAdapter`` are both thin subclasses
of ``OpenAICompatibleAdapter``, so nothing ever failed loudly enough to be
noticed. What broke was the NAME: Admin → AI Models called the self-hosted
engine "openai", next to a real OpenAI card.

``nous`` therefore exists for identity, not for behaviour — and the tests below
pin exactly that: its own adapter type, its own dispatch key, and a
base_url-not-api_key credential rule (the row's base_url IS the credential; the
engine sits on a private address and there is no public default to fall back
to).
"""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp
from app.services.ai.adapters import factory
from app.services.ai.adapters.nous import NousAdapter
from app.services.ai.provider_protocols.base import ProviderNotConfiguredError


@pytest.mark.unit
def test_nous_is_a_registered_chat_key():
    assert "nous" in pp.chat_provider_keys()


@pytest.mark.unit
def test_nous_builds_its_own_adapter_from_base_url():
    adapter = pp.get_chat_protocol("nous").build_chat_adapter(
        "qwen3-8-27b", {"api_key": "k", "base_url": "http://10.0.0.10:8000/v1"}
    )
    assert isinstance(adapter, NousAdapter)
    assert adapter.api_url == "http://10.0.0.10:8000/v1/chat/completions"
    assert adapter.default_model == "qwen3-8-27b"


@pytest.mark.unit
def test_nous_requires_base_url_not_api_key():
    # ``.provider`` is asserted on purpose: an UNREGISTERED "nous" degrades to
    # the qwen protocol, which ALSO raises on an empty base_url — so a bare
    # ``pytest.raises`` would stay green with the key missing entirely. Same
    # trap test_codex_local_without_user_id_raises_not_configured documents.
    with pytest.raises(ProviderNotConfiguredError) as excinfo:
        pp.get_chat_protocol("nous").build_chat_adapter(
            "m", {"api_key": "k", "base_url": ""}
        )
    assert excinfo.value.provider == "nous"


@pytest.mark.unit
def test_nous_row_dispatches_on_its_own_key_not_the_model_prefix_rule():
    # The admin named the provider on the row, so it wins. Unregistered, this
    # would fall through to whatever provider_key_for_model guesses from a
    # model string like "qwen3-8-27b" — which is how these rows ended up
    # indistinguishable from DashScope's in the first place.
    assert factory.resolve_provider_key("nous", "qwen3-8-27b") == "nous"


@pytest.mark.unit
def test_migration_and_code_are_safe_in_either_deploy_order():
    """run-migration.yml and deploy-gpu.yml fire independently (CLAUDE.md:
    "migration 与代码部署无顺序保证"), so BOTH intermediate states must chat.

    Code-first: a row still on 'openai' keeps building a working
    OpenAI-compatible adapter against its base_url.
    Migration-first: a row already on 'nous' meets code that has never heard of
    it, and ``get_chat_protocol`` falls back to the default qwen protocol —
    also an OpenAICompatibleAdapter over the row's base_url.

    Neither direction is a degradation users can observe, which is what makes
    this rename safe to ship as two independent PRs.
    """
    creds = {"api_key": "k", "base_url": "http://10.0.0.10:8000/v1"}
    code_first = pp.get_chat_protocol("openai").build_chat_adapter("m", creds)
    migration_first = pp.get_chat_protocol("not-yet-known-to-this-build")
    assert code_first.api_url == "http://10.0.0.10:8000/v1/chat/completions"
    assert migration_first.key == "qwen"
    assert (
        migration_first.build_chat_adapter("m", creds).api_url
        == "http://10.0.0.10:8000/v1/chat/completions"
    )
