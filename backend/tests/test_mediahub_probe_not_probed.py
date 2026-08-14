# backend/tests/test_mediahub_probe_not_probed.py

"""F1 — the probe must say "I can't check this", not "this is broken".

Production (2026-08-14): 3 of the 4 red lights on the admin AI page were
``image`` / ``video`` models that the probe had POSTed to
``{base_url}/chat/completions``, because it only dispatched ``asr`` and
``embedding`` specially. A text-to-image endpoint 404s on a chat path and the
CLI-backed models have no ``base_url`` at all — so those rows could never come
back green no matter how healthy the model was. The models were all fine.

The fix is not a real image probe (that would cost money and generate assets
every hour, and CLI models have no HTTP endpoint at all) — it is for the probe
to declare its own boundary: ``not_probed``.

Two properties are pinned here, and the second is the one that actually costs
something to get wrong:

  1. ``ok`` is False for ``not_probed``. It did not succeed; only the separate
     ``not_probed`` key distinguishes "didn't check" from "checked and failed",
     so no caller can accidentally count it as a healthy model.
  2. NO request leaves the process. Asserting the return value alone would
     still pass if the probe fired the request and threw the answer away — and
     that is exactly the cost (72 pointless calls/day + the log noise) this
     change exists to remove. The transport is replaced with one that raises,
     so a request that IS sent fails the test loudly.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.mediahub_model_health import (
    PROBE_STATUSES,
    PROBEABLE_TYPES,
    probe_mediahub_model,
    probe_result_status,
)


class _NetworkTouched(BaseException):
    """Raised by the fake transport when the probe sends anything.

    Deliberately a ``BaseException`` and not an ``Exception``: the probe wraps
    its body in ``except Exception``, so an ordinary error would be swallowed
    and reported as an ordinary failed probe — the test would go green while
    the request was still being sent. This escapes that handler.
    """


class _ExplodingClient:
    """httpx.AsyncClient stand-in that refuses to be used at all."""

    def __init__(self, *a, **k):
        raise _NetworkTouched("probe constructed an HTTP client")


def _no_network():
    """Patch every route out of the process: the HTTP client AND the provider
    factory (the ``asr`` branch's way out)."""
    return (
        patch(
            "app.services.ai.mediahub_model_health.httpx.AsyncClient",
            _ExplodingClient,
        ),
        patch(
            "app.services.ai.mediahub_model_health.AIProviderFactory.test_connection",
            new=AsyncMock(side_effect=_NetworkTouched("probe called the provider")),
        ),
    )


def _row(typ: str) -> dict:
    """A row shaped like the real ones: an image/video model configured through
    the CLI provider genuinely has an empty base_url."""
    return {
        "type": typ,
        "actual_model": f"some-{typ}-model",
        "actual_provider": "jimeng-cli",
        "base_url": "",
        "api_key": "k",
    }


@pytest.mark.parametrize("typ", ["image", "video", "tts"])
@pytest.mark.asyncio
async def test_unprobeable_type_returns_not_probed_without_touching_the_network(
    typ: str,
) -> None:
    client_patch, provider_patch = _no_network()
    with client_patch, provider_patch:
        result = await probe_mediahub_model(_row(typ))

    assert result["not_probed"] is True
    # Not a success. A caller counting "green lights" must not pick this up.
    assert result["ok"] is False
    # Not a failure either: no reason code, because there is no failure to
    # classify. ``last_test_code`` is the enum of why a probe FAILED.
    assert result["code"] is None
    assert result["error"] is None
    assert result["dims"] is None
    # The detail names the type, so the admin reading the row learns which
    # boundary was hit. It carries no host / base_url / credential.
    assert result["detail"] == f"no protocol probe for type={typ}"


@pytest.mark.parametrize("typ", ["image", "video", "tts"])
@pytest.mark.asyncio
async def test_unprobeable_type_maps_to_the_not_probed_status(typ: str) -> None:
    """The persisted ``last_test_status``, via the mapping both writers share."""
    client_patch, provider_patch = _no_network()
    with client_patch, provider_patch:
        result = await probe_mediahub_model(_row(typ))

    assert probe_result_status(result) == "not_probed"


@pytest.mark.parametrize("typ", sorted(PROBEABLE_TYPES))
@pytest.mark.asyncio
async def test_probeable_types_still_reach_the_transport(typ: str) -> None:
    """Positive control — without it the two tests above would also pass if the
    probe had simply stopped probing everything.

    Each probeable type must still hit the network, i.e. must still trip the
    exploding transport. ``_NetworkTouched`` escapes the probe's own
    ``except Exception``, so it surfaces here as the raise pytest is watching
    for rather than as a quietly-recorded failed probe.
    """
    row = {
        "type": typ,
        "actual_model": "m",
        "actual_provider": "openai",
        "base_url": "https://example.invalid/v1",
        "api_key": "k",
    }
    client_patch, provider_patch = _no_network()
    with client_patch, provider_patch:
        with pytest.raises(_NetworkTouched):
            await probe_mediahub_model(row)


def test_probe_result_status_maps_all_three_outcomes() -> None:
    """The mapping is shared by the hourly poll and the admin Test endpoint so
    the two cannot drift — the same reason the probe itself is shared."""
    assert probe_result_status({"ok": True, "detail": "chat ok"}) == "ok"
    assert probe_result_status({"ok": False, "error": "HTTP 401"}) == "fail"
    assert probe_result_status({"ok": False, "not_probed": True}) == "not_probed"
    # A result that predates the key (or any dict without it) is still a plain
    # failure, never silently "not checked".
    assert probe_result_status({"ok": False}) == "fail"


def test_probeable_types_is_exactly_the_three_documented_types() -> None:
    """Change-detector, and only that: it pins the literal membership so
    widening the set is a deliberate two-line edit rather than a slip.

    It does NOT verify that the probe has a branch per type — the behavioural
    guard for that is ``test_probeable_types_still_reach_the_transport``
    above, which makes each member actually hit the transport.
    """
    assert PROBEABLE_TYPES == frozenset({"llm", "embedding", "asr"})


def test_probe_statuses_matches_the_orm_check_constraint() -> None:
    """``PROBE_STATUSES`` claims to be the twin of the DB CHECK — so read the
    other twin and compare, instead of asserting a second copy of the literal.

    Without this the constant is a comment shaped like code: nothing would stop
    the CHECK from gaining a fourth value (or losing ``not_probed``) while the
    tuple stayed put. The ORM constraint is the checkable stand-in for the DB —
    migration 428 wrote both, and ``schema-drift`` keeps ORM and DB aligned.
    """
    from app.models.ai import MediahubModels

    constraint = next(
        c
        for c in MediahubModels.__table__.constraints
        if c.name == "mediahub_models_last_test_status_check"
    )
    literals = set(re.findall(r"'([^']*)'", str(constraint.sqltext)))
    assert literals == set(PROBE_STATUSES)
