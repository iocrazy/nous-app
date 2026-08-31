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

import io
from typing import Any, Dict, Mapping, Optional, Tuple

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

# Providers whose work runs on the USER's OWN machine via their paired daemon,
# not on any server this process can reach (spec 2026-08-27). There is no
# base_url to probe — the credential and the runtime both live on the user's
# device — so an HTTP probe is not "failing", it is not applicable.
#
# Same failure shape as the image/video rows above, one layer over: a
# ``codex-local`` row is type=llm, so it sails past PROBEABLE_TYPES and lands on
# the ``/chat/completions`` branch, where a NULL base_url builds an invalid URL
# and the row goes permanently red — a red light the admin CANNOT clear, on a
# model that is perfectly healthy whenever its owner's daemon is online.
#
# Deliberately NOT a new status: "we can't check this from here" is exactly what
# ``not_probed`` already means (mig 428). Liveness of a personal daemon is a
# per-user, per-moment fact; the catalog row is global, so no single value on it
# could be true for every user at once.
LOCAL_ENGINE_PROVIDERS = frozenset({"codex-local", "jimeng-local"})

# Values ``mediahub_models.last_test_status`` may hold. Twin of the DB CHECK in
# migration 428 / ``models/ai.py`` — both sides must change together, and
# test_mediahub_probe_not_probed.py::test_probe_statuses_matches_the_orm_check_constraint
# reads the ORM constraint back and compares, so the pairing is enforced rather
# than merely asserted here.
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

# ---------------------------------------------------------------- image probe
#
# Image models are NOT in PROBEABLE_TYPES and deliberately stay out of it: the
# scheduled poll runs hourly per enabled model, and a text-to-image call costs
# money and produces an asset every single time. So the image probe is gated on
# an explicit ``allow_costly=True``, which ONLY the admin "Test" button passes.
# The hourly poll keeps reporting image rows as ``not_probed``, unchanged.
#
# A deliberately NON-SQUARE aspect is requested. The failure this probe exists
# to catch is "the generator ignored the size we asked for and returned its own
# default", and that default is square — so asking for a square could not tell
# an honored request from an ignored one. Asking for 16:9 can.
_IMAGE_PROBE_ASPECT = "16:9"
_IMAGE_PROBE_ASPECT_VALUE = 16 / 9
# Generation is lossy about exact pixel counts (a provider may snap 1280x720 to
# its nearest supported size), so compare the RATIO with a tolerance rather than
# the pixels. 6% still separates 16:9 (1.778) from 4:3 (1.333) and 1:1.
_IMAGE_PROBE_TOLERANCE = 0.06
_IMAGE_PROBE_PROMPT = "a flat grey rectangle on a white background, no text"
_IMAGE_PROBE_TIMEOUT = 90.0
# The probe reads the produced image back to measure it. Cap what it will pull
# into memory: the URL is upstream-controlled, and an unbounded read on a probe
# that runs from an admin click is a free memory amplifier.
_IMAGE_PROBE_MAX_BYTES = 25 * 1024 * 1024


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


async def _measure_generated_image(url: str) -> Tuple[int, int]:
    """Download the produced image and return its REAL ``(width, height)``.

    Measured from the bytes, never from what we asked for. ``ImageGenResult``
    carries width/height parsed out of the requested ``size`` string — an echo
    of the request, not an observation of the result — so trusting it would
    make the aspect check assert that our own arithmetic is self-consistent.
    """
    async with httpx.AsyncClient(
        timeout=_IMAGE_PROBE_TIMEOUT, follow_redirects=True
    ) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise _ImageProbeHTTPError(response.status_code)
    body = response.content
    if not body:
        raise ValueError("image url returned 0 bytes")
    if len(body) > _IMAGE_PROBE_MAX_BYTES:
        raise ValueError(f"image exceeds the {_IMAGE_PROBE_MAX_BYTES}-byte probe cap")
    from PIL import Image  # local import: only this branch needs it

    with Image.open(io.BytesIO(body)) as im:
        width, height = int(im.width), int(im.height)
    if width <= 0 or height <= 0:
        raise ValueError(f"decoded a degenerate image ({width}x{height})")
    return width, height


class _ImageProbeHTTPError(Exception):
    """Carries the status code so ``classify_probe_failure`` can use it."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code} fetching the produced image")
        self.status_code = status_code


async def _probe_image_model(row: Dict[str, Any], prov: str) -> Dict[str, Any]:
    """Real text-to-image probe: generate one image, then measure it.

    Two independent things are checked, and both must hold for ``ok``:

    1. the provider produced bytes we can decode as an image at all, and
    2. its aspect ratio matches the one that was requested.

    (2) is the half that a reachability check would miss. A generator that
    quietly substitutes its own default size answers 200 with a perfectly valid
    image, so "the endpoint works" and "the endpoint does what we asked" are
    genuinely different questions — and the second is the one that decides
    whether a user gets the cover they chose.

    Never raises; every failure comes back as ``ok=False`` plus a closed-enum
    code, same contract as the other branches.
    """
    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(prov)
    if protocol is None or not protocol.supports_http_image_probe:
        # Not a failure: there is no HTTP endpoint to reach. CLI-backed
        # (``codex``, ``jimeng-cli``) and daemon-backed families live outside
        # this process entirely. Names the provider and nothing else.
        return {
            "ok": False,
            "not_probed": True,
            "detail": f"no HTTP image endpoint (provider={prov})",
            "error": None,
            "code": None,
            "dims": None,
        }

    try:
        provider, actual_model = protocol.build_image_provider(row)
        result = await provider.generate(
            prompt=_IMAGE_PROBE_PROMPT,
            model=actual_model,
            aspect_ratio=_IMAGE_PROBE_ASPECT,
        )
        image_url = getattr(result, "image_url", None)
        if not image_url:
            return {
                "ok": False,
                "not_probed": False,
                "detail": "",
                "error": "generation returned no image url",
                "code": "bad_response",
                "dims": None,
            }
        width, height = await _measure_generated_image(image_url)
    except _ImageProbeHTTPError as e:
        return {
            "ok": False,
            "not_probed": False,
            "detail": "",
            "error": str(e),
            "code": classify_probe_failure(status_code=e.status_code),
            "dims": None,
        }
    except Exception as e:  # noqa: BLE001 — probe is best-effort
        # Same "never a bare str(e)" rule as the outer handler: httpx timeouts
        # stringify to an empty string, and a blank reason on a red light is
        # indistinguishable from a model that is genuinely broken.
        return {
            "ok": False,
            "not_probed": False,
            "detail": "",
            "error": f"{type(e).__name__}: {str(e) or '<no message>'}"[:200],
            "code": classify_probe_failure(exc=e),
            "dims": None,
        }

    got = width / height
    honored = (
        abs(got - _IMAGE_PROBE_ASPECT_VALUE) / _IMAGE_PROBE_ASPECT_VALUE
        <= _IMAGE_PROBE_TOLERANCE
    )
    size = f"{width}x{height}"
    if not honored:
        return {
            "ok": False,
            "not_probed": False,
            "detail": "",
            # Says what was asked and what came back — the two numbers an admin
            # needs to tell "the model is down" from "the model ignores size".
            "error": (
                f"asked for {_IMAGE_PROBE_ASPECT}, got {size} "
                f"(ratio {got:.3f} vs {_IMAGE_PROBE_ASPECT_VALUE:.3f})"
            ),
            "code": "bad_response",
            "dims": None,
        }
    return {
        "ok": True,
        "not_probed": False,
        "detail": f"{size}, {_IMAGE_PROBE_ASPECT} honored",
        "error": None,
        "code": None,
        "dims": None,
    }


async def probe_mediahub_model(
    row: Dict[str, Any], *, allow_costly: bool = False
) -> Dict[str, Any]:
    """Real connectivity probe for one platform model, by type.

    ``allow_costly`` opts into probes that spend money and produce an asset —
    today only text-to-image. It defaults to False so the hourly poll keeps its
    behaviour by omission rather than by remembering to opt out; the admin
    "Test" button is the single caller that passes True.

    Returns ``{ok, detail, error, dims, code, not_probed}``. Never raises — a
    transport/HTTP failure is reported as ``ok=False`` with the error text plus
    a ``PROBE_FAILURE_CODES`` value. ``code`` is ``None`` on success, so writing
    it on every probe clears a previous failure's code.

    A type outside ``PROBEABLE_TYPES``, or a provider in
    ``LOCAL_ENGINE_PROVIDERS`` (the work runs on the user's own machine),
    returns ``not_probed=True`` without sending anything. ``ok`` stays False
    there — it did not succeed — so the only way to read it as healthy is to
    look at ``not_probed`` deliberately.
    """
    typ = (row.get("type") or "").strip()
    prov = (row.get("actual_provider") or "").strip().lower()

    if prov in LOCAL_ENGINE_PROVIDERS:
        # Checked BEFORE the type gate: a local row may be a probeable type
        # (codex-local is type=llm) and would otherwise be dialled. Same
        # "don't make the call" reasoning as below — here the call could not
        # succeed even in principle, because the endpoint is someone's laptop.
        return {
            "ok": False,
            "not_probed": True,
            # Names the boundary, no host / base_url / credential — same
            # safe-anywhere guarantee as the type branch.
            "detail": f"runs on the user's own machine (provider={prov})",
            "error": None,
            "code": None,
            "dims": None,
        }

    if typ == "image":
        # Ahead of the PROBEABLE_TYPES gate, which would otherwise answer
        # ``not_probed`` for every image row. ``image`` stays out of that set on
        # purpose: it names the types the SCHEDULED poll may dial, and this
        # branch is reachable only on an explicit admin request.
        if not allow_costly:
            return {
                "ok": False,
                "not_probed": True,
                "detail": "image probe runs only on an explicit Test (it costs a generation)",
                "error": None,
                "code": None,
                "dims": None,
            }
        return await _probe_image_model(row, prov)

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
