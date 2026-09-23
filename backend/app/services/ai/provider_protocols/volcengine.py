from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class VolcengineProtocol(ProviderProtocol):
    """The Volcengine SPEECH key — openspeech bigasr / seed-asr.

    Not an alias of ``doubao``. Same vendor, different product and a different
    credential (api_key, plus app_id on the old console). Until 2026-09-22 it
    sat in a side dict pointing at ``DoubaoProvider``, a chat client that
    cannot transcribe; every consumer had to special-case the key before that
    class was reached. ``VolcengineAsrProvider`` is its honest provider.

    ASR only, and deliberately NOT a chat key: ``is_chat_key`` would put it in
    ``adapters.factory``'s dispatch set and let a chat call build a chat
    adapter against a speech endpoint. No build hook for the same reason.
    """

    key = "volcengine"
    label = "Volcengine Speech (ASR)"
    description = (
        "Volcengine openspeech ASR (bigasr / seed-asr). API key, plus App ID "
        "for old-console keys. Speech only — the Doubao chat key is 'doubao'."
    )
    model_types = ("asr",)
    credential_kind = "api_key"
    ai_provider_name = "VolcengineAsrProvider"
