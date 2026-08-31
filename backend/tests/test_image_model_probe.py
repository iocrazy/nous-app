# backend/tests/test_image_model_probe.py

"""The image probe: a paid, opt-in probe that checks the RESULT, not just reach.

Why this exists. On 2026-08-30 every image row on the admin page read
``not_probed``, and there was no way to tell "nobody has used this model" from
"this model is broken" — in 90 days the catalog's five image models had
produced exactly 4 real generations between them, and those 4 came back with
the wrong aspect ratio while the call reported success.

Two design constraints are pinned here, because getting either wrong is
expensive in a way a green test suite would not show:

  1. **Cost.** The scheduled poll runs hourly over every enabled model. A
     text-to-image call spends money and produces an asset on every run, so the
     poll must NEVER reach this branch. The probe is gated on an explicit
     ``allow_costly=True`` that only the admin Test button passes.

  2. **Falsifiability.** "The endpoint answered 200" and "the endpoint produced
     what we asked for" are different questions, and only the second one decides
     whether a user gets the cover they chose. So the probe requests a
     deliberately NON-SQUARE aspect and measures the bytes that come back. A
     probe that cannot go red on the known defect would be decoration.
"""

from __future__ import annotations

import io
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.mediahub_model_health import (
    probe_mediahub_model,
    probe_result_status,
)


class _NetworkTouched(BaseException):
    """Escapes the probe's ``except Exception`` so a stray call fails loudly.

    Same reasoning as test_mediahub_probe_not_probed.py: an ordinary Exception
    would be swallowed and recorded as an ordinary failed probe, and the test
    would pass while the request was still going out — which is precisely the
    cost this gate exists to prevent.
    """


class _ExplodingClient:
    def __init__(self, *a: Any, **k: Any) -> None:
        raise _NetworkTouched("probe constructed an HTTP client")


def _no_network():
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


def _png_bytes(width: int, height: int) -> bytes:
    """Real encoded PNG bytes — the probe decodes what it downloads, so a fake
    that only claims a size would not exercise the measurement at all."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (128, 128, 128)).save(buf, format="PNG")
    return buf.getvalue()


def _row(provider: str = "doubao", typ: str = "image") -> dict:
    return {
        "type": typ,
        "actual_model": "doubao-seedream-3-0-t2i-250415",
        "actual_provider": provider,
        "base_url": "https://ark.example.invalid/api/v3",
        "api_key": "k",
    }


class _FakeResult:
    """Stand-in for ImageGenResult.

    ``width``/``height`` are settable so a test can make the result CLAIM the
    requested size while the bytes say otherwise — the real class parses those
    two fields out of the requested ``size`` string, so they are an echo of the
    request rather than an observation.
    """

    def __init__(
        self, image_url: Optional[str], width: int = 1280, height: int = 720
    ) -> None:
        self.image_url = image_url
        self.width = width
        self.height = height


class _FakeProvider:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def generate(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _FakeProtocol:
    def __init__(self, provider: Any, supports: bool = True) -> None:
        self._provider = provider
        self.supports_http_image_probe = supports

    def build_image_provider(self, row: dict) -> tuple:
        return self._provider, row.get("actual_model") or ""


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code


class _FakeClient:
    """Async-context httpx stand-in that serves one canned response."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __call__(self, *a: Any, **k: Any) -> "_FakeClient":
        return self

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        return self._response


def _wired(provider: Any, response: _FakeResponse, supports: bool = True):
    """Patch both seams: which protocol answers, and what the download returns."""
    return (
        patch(
            "app.services.ai.provider_protocols.resolve_generation_protocol",
            lambda p: _FakeProtocol(provider, supports),
        ),
        patch(
            "app.services.ai.mediahub_model_health.httpx.AsyncClient",
            _FakeClient(response),
        ),
    )


# ─────────────────────────── 1. the cost gate ───────────────────────────


@pytest.mark.asyncio
async def test_image_is_not_probed_without_the_explicit_opt_in() -> None:
    """The default path checks nothing and sends nothing."""
    client_patch, provider_patch = _no_network()
    with client_patch, provider_patch:
        result = await probe_mediahub_model(_row())

    assert result["not_probed"] is True
    # Not a success and not a failure: nothing was attempted.
    assert result["ok"] is False
    assert result["code"] is None
    assert result["error"] is None
    assert probe_result_status(result) == "not_probed"
    # The reason is the honest one: a probe exists, it is just too costly to
    # run on this path — not "no protocol for this type".
    assert "costs a generation" in result["detail"]


@pytest.mark.asyncio
async def test_the_hourly_poll_never_opts_in() -> None:
    """End-to-end through the real poll, not by reading the call site.

    The poll is the caller whose behaviour costs money if it changes, so this
    drives ``probe_mediahub_models_step`` itself with an enabled image row and a
    transport that explodes on contact. Asserting on the source text (or on a
    mocked probe) would keep passing if someone threaded ``allow_costly=True``
    through a different route.
    """
    from app.workflows import scheduled_health

    recorded: list[tuple] = []

    class _Repo:
        async def list_all(self) -> list[dict]:
            return [dict(_row(), id="1", is_enabled=True, name="seedream")]

        async def record_test_result(
            self, model_id: str, status: str, detail: str, code: Any
        ) -> dict:
            recorded.append((model_id, status, detail, code))
            return {"last_tested_at": "2026-08-30T00:00:00Z"}

    client_patch, provider_patch = _no_network()
    with (
        client_patch,
        provider_patch,
        patch(
            "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
            lambda: _Repo(),
        ),
    ):
        summary = await scheduled_health.probe_mediahub_models_step()

    assert [r[1] for r in recorded] == ["not_probed"]
    assert summary.get("not_probed") == 1
    assert summary.get("ok") == 0
    # The summary key is ``failed`` (the counter behind it is ``fail``) — read
    # it by its real name rather than a plausible one, or a missing key would
    # read as "zero failures" via ``.get``.
    assert summary.get("failed") == 0


# ──────────────────── 2. families with no HTTP endpoint ────────────────────


@pytest.mark.parametrize("provider", ["codex", "jimeng-cli", "codex-local"])
@pytest.mark.asyncio
async def test_cli_and_daemon_families_stay_not_probed_even_when_asked(
    provider: str,
) -> None:
    """Opting into a costly probe cannot conjure an endpoint that isn't there.

    ``codex`` and ``jimeng-cli`` shell out to a local binary; ``codex-local``
    dials the user's own paired device. All three carry an empty ``base_url``
    in the catalog because there is nothing to dial from this process.
    """
    client_patch, provider_patch = _no_network()
    with client_patch, provider_patch:
        result = await probe_mediahub_model(
            dict(_row(provider=provider), base_url=""), allow_costly=True
        )

    assert result["not_probed"] is True
    assert result["ok"] is False
    assert result["code"] is None
    assert probe_result_status(result) == "not_probed"
    # Names the provider and nothing else — safe wherever the detail ends up.
    assert provider in result["detail"]
    for leak in ("api_key", "k", "Authorization"):
        assert leak not in result["detail"].replace("jimeng-cli", "")


# ─────────────────────── 3. the probe that can go red ───────────────────────


@pytest.mark.asyncio
async def test_a_generator_that_honors_the_aspect_is_ok() -> None:
    provider = _FakeProvider(_FakeResult("https://cdn.example.invalid/a.png"))
    proto_patch, client_patch = _wired(provider, _FakeResponse(_png_bytes(1280, 720)))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is True
    assert result["not_probed"] is False
    assert result["code"] is None
    assert probe_result_status(result) == "ok"
    # The measured size is reported, so the admin sees evidence rather than a
    # bare green dot.
    assert "1280x720" in result["detail"]
    # And it asked for a non-square aspect — the whole point of the check.
    assert provider.calls[0]["aspect_ratio"] == "16:9"


@pytest.mark.asyncio
async def test_a_generator_that_ignores_the_aspect_goes_red() -> None:
    """THE test. A square answer to a 16:9 request is a failure, not a success.

    This is the shape of the defect that shipped: 4 of 4 real generations came
    back with an aspect nobody asked for while the call reported ``ok``. A probe
    that only checked reachability would have painted this model green.
    """
    provider = _FakeProvider(_FakeResult("https://cdn.example.invalid/a.png"))
    proto_patch, client_patch = _wired(provider, _FakeResponse(_png_bytes(1024, 1024)))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is False
    # A real failure, not "didn't check" — the admin must see red here.
    assert result["not_probed"] is False
    assert result["code"] == "bad_response"
    assert probe_result_status(result) == "fail"
    # Both numbers, so the reason distinguishes "down" from "ignores size".
    assert "16:9" in result["error"] and "1024x1024" in result["error"]


@pytest.mark.asyncio
async def test_the_aspect_is_measured_from_the_bytes_not_from_the_request() -> None:
    """The result object's own width/height are an echo and must not be trusted.

    ``ImageGenResult.width/height`` are parsed out of the ``size`` we sent, so a
    check that read them would be asserting our own arithmetic. Here the result
    claims a perfect 1280x720 while the actual image is square: the probe must
    still fail.
    """
    provider = _FakeProvider(
        _FakeResult("https://cdn.example.invalid/a.png", width=1280, height=720)
    )
    proto_patch, client_patch = _wired(provider, _FakeResponse(_png_bytes(900, 900)))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is False
    assert "900x900" in result["error"]


@pytest.mark.asyncio
async def test_a_near_miss_within_tolerance_still_passes() -> None:
    """Providers snap to their own supported sizes; the check compares ratios
    with a tolerance so a 1288x720 answer is not a false alarm."""
    provider = _FakeProvider(_FakeResult("https://cdn.example.invalid/a.png"))
    proto_patch, client_patch = _wired(provider, _FakeResponse(_png_bytes(1288, 720)))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is True


# ───────────────────────── 4. failure shapes ─────────────────────────


@pytest.mark.asyncio
async def test_a_non_200_on_the_produced_image_is_a_classified_failure() -> None:
    provider = _FakeProvider(_FakeResult("https://cdn.example.invalid/a.png"))
    proto_patch, client_patch = _wired(provider, _FakeResponse(b"", status_code=404))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is False
    assert result["not_probed"] is False
    # From the closed enum, derived from the status alone.
    assert result["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_zero_bytes_is_a_failure_not_a_pass() -> None:
    """An empty body decodes to nothing; "no output" is never a positive
    result (same rule as the timeout-is-not-a-negative-result discipline)."""
    provider = _FakeProvider(_FakeResult("https://cdn.example.invalid/a.png"))
    proto_patch, client_patch = _wired(provider, _FakeResponse(b""))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is False
    assert result["error"]


@pytest.mark.asyncio
async def test_a_generation_with_no_url_is_a_bad_response() -> None:
    provider = _FakeProvider(_FakeResult(None))
    proto_patch, client_patch = _wired(provider, _FakeResponse(_png_bytes(16, 9)))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is False
    assert result["code"] == "bad_response"


@pytest.mark.asyncio
async def test_a_raising_generator_never_escapes_the_probe() -> None:
    """The probe's contract is that it never raises — a failing model must not
    take down the hourly poll or the admin request."""
    provider = _FakeProvider(TimeoutError())
    proto_patch, client_patch = _wired(provider, _FakeResponse(b""))
    with proto_patch, client_patch:
        result = await probe_mediahub_model(_row(), allow_costly=True)

    assert result["ok"] is False
    # Never a bare empty message: TimeoutError() stringifies to "".
    assert "TimeoutError" in result["error"]


# ────────────────── 5. the axis is declared, never inferred ──────────────────


def test_every_generation_protocol_declares_whether_it_is_http_probeable() -> None:
    """No third "unclassified" state.

    Same discipline as the tool-descriptor allowlist: adding an image family
    must be an explicit yes/no, not an inherited default nobody looked at. If
    this map and the protocols disagree, one of them was changed without the
    other being considered.
    """
    from app.services.ai.provider_protocols import PROTOCOLS

    expected = {
        "ark": True,  # /images/generations — a plain HTTP call from here
        "codex": False,  # shells out to the codex CLI
        "jimeng-cli": False,  # shells out to the jimeng CLI
        # The two ``-local`` families run on the USER's own paired device, so
        # there is no endpoint this process could dial even in principle. They
        # are also caught earlier by LOCAL_ENGINE_PROVIDERS; declaring them
        # here as well keeps the axis total rather than relying on that guard.
        "codex-local": False,
        "jimeng-local": False,
    }
    actual = {
        p.key: p.supports_http_image_probe
        for p in PROTOCOLS
        if p.generation_family is not None
    }
    assert actual == expected


def test_a_non_generation_protocol_defaults_to_not_probeable() -> None:
    """Chat-only protocols inherit False — they build no image provider at all,
    so the flag must never make one of them look reachable for images."""
    from app.services.ai.provider_protocols import PROTOCOLS

    for p in PROTOCOLS:
        if p.generation_family is None:
            assert p.supports_http_image_probe is False


# ────────────── 6. the opt-in is actually wired at the call site ──────────────


@pytest.mark.asyncio
async def test_the_admin_test_endpoint_opts_into_the_costly_probe() -> None:
    """Without this the feature is dead code and every test above still passes.

    ``allow_costly`` defaults to False, so the image branch is reachable only if
    the endpoint passes True. Nothing else in the suite would notice if that
    argument were dropped: the probe's own tests call it directly.
    """
    from unittest.mock import MagicMock

    from app.api.admin.mediahub_model_router import test_mediahub_model

    seen: dict = {}

    async def _fake_probe(row: dict, **kwargs: Any) -> dict:
        seen.update(kwargs)
        return {"ok": True, "detail": "d", "error": None, "dims": None, "code": None}

    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[{"id": "42", "type": "image"}])
    repo.record_test_result = AsyncMock(return_value={"last_tested_at": "t"})

    with (
        patch(
            "app.api.admin.mediahub_model_router.get_mediahub_model_repository",
            return_value=repo,
        ),
        patch(
            "app.api.admin.mediahub_model_router._probe_mediahub_model",
            new=_fake_probe,
        ),
    ):
        await test_mediahub_model("42", MagicMock())

    assert seen.get("allow_costly") is True
