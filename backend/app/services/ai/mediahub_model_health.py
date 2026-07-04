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
            return {
                "ok": bool(res.get("success")),
                "detail": "reachable" if res.get("success") else "",
                "error": res.get("error"),
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
            async with httpx.AsyncClient(timeout=20.0) as c:
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
        async with httpx.AsyncClient(timeout=20.0) as c:
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
        return {"ok": False, "detail": "", "error": str(e)[:200], "dims": None}
