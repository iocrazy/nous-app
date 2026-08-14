# backend/app/services/ai/mediahub_model_health.py

"""Connectivity probe for admin-configured platform (Nous) AI models.

Single source of truth for "is this platform model actually reachable" — used
by BOTH the manual admin ``POST /admin/mediahub-models/{id}/test`` endpoint and the
scheduled health poll (``scheduled_health.probe_mediahub_models_step``). Keeping one
implementation avoids the two drifting apart.

The probe performs a real minimal inference per model TYPE (chat / embedding /
asr) so account-level limits surface (e.g. a Volcengine ``SetLimitExceeded`` on
a specific model), not just key reachability. It never raises.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from app.services.ai.providers.ai_provider import AIProviderFactory
from app.services.ai.providers.embedding_config import _is_multimodal

# Per-request budget for a probe. Was 20s, which is a plausible cause of the
# 2026-08-14 false red on ``mediahub-deepseek-v4-flash``: cold starts (upstream
# scale-from-zero, a self-hosted engine paging a model in) routinely exceed it,
# and the probe cannot tell a slow start from a dead endpoint. A model wrongly
# marked unreachable costs far more than 40 extra seconds on an hourly ping.
_PROBE_TIMEOUT = 60.0


async def probe_mediahub_model(row: Dict[str, Any]) -> Dict[str, Any]:
    """Real connectivity probe for one platform model, by type.

    Returns ``{ok, detail, error, dims}``. Never raises — a transport/HTTP
    failure is reported as ``ok=False`` with the error text.
    """
    typ = (row.get("type") or "").strip()
    model = (row.get("actual_model") or "").strip()
    base = (row.get("base_url") or "").rstrip("/")
    key = row.get("api_key") or ""
    provider = (row.get("actual_provider") or "").strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        if typ == "asr":
            res = await AIProviderFactory.test_connection(
                provider_key=provider,
                config={
                    "api_key": key,
                    "app_id": row.get("app_id") or "",
                    "base_url": base,
                    "model": model,
                },
            )
            # Same "never an empty reason" guarantee as the except-branch below,
            # applied here too: this branch returns before reaching it, and the
            # message comes from AIProviderFactory.test_connection, whose own
            # fallback is a bare ``str(e)`` — the exact shape that made the
            # 2026-08-14 red light undiagnosable. Its 10s budget makes an
            # empty-stringifying timeout MORE likely here than on the chat path.
            ok = bool(res.get("success"))
            err = res.get("error") or ""
            return {
                "ok": ok,
                "detail": "reachable" if ok else "",
                "error": None if ok else (err or "asr probe failed with no message"),
                "dims": None,
            }

        if typ == "embedding":
            if _is_multimodal(model, base):
                url = (
                    base
                    if "embeddings/multimodal" in base
                    else base + "/embeddings/multimodal"
                )
                payload = {"model": model, "input": [{"type": "text", "text": "ping"}]}
            else:
                url = base + "/embeddings"
                payload = {"model": model, "input": "ping"}
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
                r = await c.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                return {
                    "ok": False,
                    "detail": "",
                    "error": f"HTTP {r.status_code}: {r.text[:160]}",
                    "dims": None,
                }
            data = (r.json() or {}).get("data")
            emb = None
            if isinstance(data, dict):
                emb = data.get("embedding")
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                emb = data[0].get("embedding")
            dims = len(emb) if isinstance(emb, list) else None
            return {
                "ok": dims is not None,
                "detail": f"{dims} dims" if dims else "",
                "error": None if dims else "no embedding vector in response",
                "dims": dims,
            }

        # llm (and any chat-completions provider)
        url = base + "/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
            r = await c.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            return {
                "ok": False,
                "detail": "",
                "error": f"HTTP {r.status_code}: {r.text[:160]}",
                "dims": None,
            }
        ok = bool((r.json() or {}).get("choices"))
        return {
            "ok": ok,
            "detail": "chat ok" if ok else "",
            "error": None if ok else "no choices in response",
            "dims": None,
        }
    except Exception as e:  # noqa: BLE001 — probe is best-effort
        # Qualify the reason with the exception TYPE, never bare str(e):
        # httpx's ReadTimeout / ConnectTimeout / ReadError all stringify to ""
        # (verified inside nous-backend), which landed in the DB as a red light
        # with a blank reason — indistinguishable from a genuinely broken model.
        # See test_mediahub_model_health_diagnosable.py.
        reason = f"{type(e).__name__}: {str(e) or '<no message>'}"
        return {"ok": False, "detail": "", "error": reason[:200], "dims": None}
