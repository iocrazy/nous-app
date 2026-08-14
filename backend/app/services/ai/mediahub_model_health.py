# backend/app/services/ai/mediahub_model_health.py

"""Connectivity probe for admin-configured platform (Nous) AI models.

Single source of truth for "is this platform model actually reachable" — used
by BOTH the manual admin ``POST /admin/mediahub-models/{id}/test`` endpoint and the
scheduled health poll (``scheduled_health.probe_mediahub_models_step``). Keeping one
implementation avoids the two drifting apart.

The probe performs a real minimal inference per model TYPE (chat / embedding /
asr) so account-level limits surface (e.g. a Volcengine ``SetLimitExceeded`` on
a specific model), not just key reachability. It never raises.

It also declares its own boundary: a type it has no protocol for comes back
``not_probed`` rather than ``fail`` (see ``PROBEABLE_TYPES``). A probe that
cannot speak a protocol should answer "I can't check this", not hand back a
verdict that was never going to be anything but red.

Every failure also carries a CODE from a closed enum (``classify_probe_failure``)
alongside the free-text reason: the reason is admin-only (it embeds upstream
hosts, private base_urls and upstream model ids), the code is what users see.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import httpx

from app.services.ai.providers.ai_provider import AIProviderFactory
from app.services.ai.providers.embedding_config import _is_multimodal

# The model types this probe can actually speak a protocol for. Everything else
# is reported as ``not_probed`` — not as a failure — and never leaves the
# process.
#
# Why there is no image/video/tts probe rather than a TODO: a real text-to-image
# or text-to-video call COSTS MONEY AND PRODUCES AN ASSET on every run, and this
# runs hourly per enabled model; and the CLI-backed models (``jimeng-cli-*``)
# have an empty ``base_url`` because they have no HTTP endpoint at all, so there
# is nothing an HTTP probe could reach even in principle.
#
# What the old code did instead was send them all to ``/chat/completions``,
# which is why 3 of the 4 red lights on 2026-08-14 were structurally impossible
# to clear: a text-to-image endpoint 404s on a chat path and an empty base_url
# builds an invalid URL. All three models were healthy.
#
# Widen this set only together with a branch below that genuinely speaks that
# type's protocol (test_mediahub_probe_not_probed.py pins the two together).
PROBEABLE_TYPES = frozenset({"llm", "embedding", "asr"})

# Values ``mediahub_models.last_test_status`` may hold. Twin of the DB CHECK in
# migration 428 / ``models/ai.py`` — both sides must change together.
PROBE_STATUSES = ("ok", "fail", "not_probed")

# Closed enum of failure reasons. Closed is the whole point: a user-facing
# value derived ONLY from the exception type and the HTTP status can never
# leak what the message would (2026-08-14 recorded
# "UnsupportedProtocol: URL missing 'ht..." — a URL fragment — into the DB).
# Anything added here must stay derivable from those two signals alone.
PROBE_FAILURE_CODES = (
    "timeout",
    "unreachable",
    "auth",
    "rate_limit",
    "model_not_found",
    "upstream_error",
    "bad_response",
    "other",
)


def classify_probe_failure(
    *,
    status_code: Optional[int] = None,
    exc: Optional[BaseException] = None,
    bad_response: bool = False,
) -> str:
    """Map the signals a failed probe already holds onto ``PROBE_FAILURE_CODES``.

    Pure and side-effect free — the classification rule is worth reading and
    testing on its own, separately from the I/O around it.

    Deliberately does NOT look at any message text. Two failing models on
    2026-08-14 needed opposite responses (a ``ReadTimeout`` on a local engine:
    wait; an ``HTTP 429``: go fix quota), and both signals were already
    structured. Matching substrings would make this a text parser whose output
    is shown to every user — the exact thing the closed enum rules out.

    ``exc`` outranks ``status_code``: an exception means the request never
    completed, so any status alongside it describes some earlier attempt.
    """
    if exc is not None:
        # TimeoutException is itself a TransportError subclass — check first.
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.TransportError):
            # ConnectError / UnsupportedProtocol / ReadError / ProtocolError /
            # ProxyError: all "no answer came back from the network".
            return "unreachable"
        return "other"

    if status_code is not None and status_code != 200:
        if status_code in (401, 403):
            return "auth"
        if status_code == 429:
            return "rate_limit"
        if status_code == 404:
            return "model_not_found"
        # Includes the <400 non-200 statuses (a 3xx the client didn't follow is
        # still "the upstream answered with something we can't use").
        return "upstream_error"

    if bad_response:
        return "bad_response"

    return "other"


# Per-request budget for a probe. Was 20s, which is a plausible cause of the
# 2026-08-14 false red on ``mediahub-deepseek-v4-flash``: cold starts (upstream
# scale-from-zero, a self-hosted engine paging a model in) routinely exceed it,
# and the probe cannot tell a slow start from a dead endpoint. A model wrongly
# marked unreachable costs far more than 40 extra seconds on an hourly ping.
_PROBE_TIMEOUT = 60.0


def probe_result_status(result: Mapping[str, Any]) -> str:
    """Map a probe result onto the ``last_test_status`` value to persist.

    Both writers (the hourly poll and the admin Test endpoint) go through here
    for the same reason they share the probe itself: two hand-written copies of
    this three-way choice would drift, and the failure mode of drifting is a
    false red light — the exact thing being fixed.

    A result without ``not_probed`` is a plain failure, never "didn't check".
    """
    if result.get("ok"):
        return "ok"
    if result.get("not_probed"):
        return "not_probed"
    return "fail"


async def probe_mediahub_model(row: Dict[str, Any]) -> Dict[str, Any]:
    """Real connectivity probe for one platform model, by type.

    Returns ``{ok, detail, error, dims, code, not_probed}``. Never raises — a
    transport/HTTP failure is reported as ``ok=False`` with the error text plus
    a ``PROBE_FAILURE_CODES`` value. ``code`` is ``None`` on success, so writing
    it on every probe clears a previous failure's code.

    A type outside ``PROBEABLE_TYPES`` returns ``not_probed=True`` without
    sending anything. ``ok`` stays False there — it did not succeed — so the
    only way to read it as healthy is to look at ``not_probed`` deliberately.
    """
    typ = (row.get("type") or "").strip()

    if typ not in PROBEABLE_TYPES:
        # Before the try block, and before any client is built: the point is not
        # to fail gracefully, it is to not make the call. Hourly × per model,
        # every one of these was a guaranteed-failing request plus a WARNING.
        return {
            "ok": False,
            "not_probed": True,
            # Admin-visible reason. Names the boundary that was hit and nothing
            # else — no host, no base_url, no credential — so unlike a probe
            # failure's free text this is safe wherever it ends up.
            "detail": f"no protocol probe for type={typ}",
            "error": None,
            # NULL, not a code: PROBE_FAILURE_CODES enumerates why a probe
            # FAILED, and nothing failed here.
            "code": None,
            "dims": None,
        }

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
                # ALWAYS ``other`` when it fails, never a guess. This branch has
                # free text and nothing else — no status_code, no exception
                # object — so any code here would have to come from reading that
                # text, and a code read from a message is no longer a value that
                # is safe to show a user by construction. Making the provider
                # layer return structured codes is the real fix (design §3).
                "code": None if ok else "other",
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
                    "code": classify_probe_failure(status_code=r.status_code),
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
                "code": (
                    None
                    if dims
                    else classify_probe_failure(
                        status_code=r.status_code, bad_response=True
                    )
                ),
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
                "code": classify_probe_failure(status_code=r.status_code),
            }
        ok = bool((r.json() or {}).get("choices"))
        return {
            "ok": ok,
            "detail": "chat ok" if ok else "",
            "error": None if ok else "no choices in response",
            "dims": None,
            "code": (
                None
                if ok
                else classify_probe_failure(
                    status_code=r.status_code, bad_response=True
                )
            ),
        }
    except Exception as e:  # noqa: BLE001 — probe is best-effort
        # Qualify the reason with the exception TYPE, never bare str(e):
        # httpx's ReadTimeout / ConnectTimeout / ReadError all stringify to ""
        # (verified inside nous-backend), which landed in the DB as a red light
        # with a blank reason — indistinguishable from a genuinely broken model.
        # See test_mediahub_model_health_diagnosable.py.
        reason = f"{type(e).__name__}: {str(e) or '<no message>'}"
        return {
            "ok": False,
            "detail": "",
            "error": reason[:200],
            "dims": None,
            "code": classify_probe_failure(exc=e),
        }
