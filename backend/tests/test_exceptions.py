"""Unit tests for the unified error envelope (app/core/exceptions.py)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    register_exception_handlers,
)


class _FloatBody(BaseModel):
    """A constrained float — the shape every "seconds"/"score"/"ratio" field in
    this codebase has, and the one that makes a non-finite input reachable.

    ⚠️ Module level, not nested in ``_build_app``. This file has
    ``from __future__ import annotations``, so FastAPI resolves the handler's
    annotations against the MODULE namespace; a class defined inside the
    factory is invisible there and the parameter silently degrades to a query
    param (``loc: ['query', 'body']``) instead of a request body.
    """

    seconds: float = Field(..., ge=0)


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/floaty")
    async def floaty(body: _FloatBody) -> dict:
        return {"seconds": body.seconds}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("resource gone")

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise PermissionDeniedError("nope")

    @app.get("/invalid")
    async def invalid() -> None:
        raise ValidationError("bad input", details={"field": "name"})

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("already exists")

    @app.get("/custom")
    async def custom() -> None:
        raise AppError("teapot", status_code=418, code="im_a_teapot")

    return app


def test_unhandled_returns_envelope() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "internal_error"
    assert body["error"] == "Internal server error"


def test_not_found_maps_to_404() -> None:
    client = TestClient(_build_app())
    response = client.get("/missing")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["error"] == "resource gone"


def test_permission_denied_maps_to_403() -> None:
    client = TestClient(_build_app())
    response = client.get("/forbidden")
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_validation_error_attaches_details() -> None:
    client = TestClient(_build_app())
    response = client.get("/invalid")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"] == {"field": "name"}


def test_conflict_maps_to_409() -> None:
    client = TestClient(_build_app())
    response = client.get("/conflict")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_custom_app_error_status_and_code() -> None:
    client = TestClient(_build_app())
    response = client.get("/custom")
    assert response.status_code == 418
    body = response.json()
    assert body["code"] == "im_a_teapot"
    assert body["error"] == "teapot"


@pytest.mark.parametrize("token", ["NaN", "-Infinity"])
def test_nonfinite_input_is_a_422_not_a_500(token: str) -> None:
    """A non-finite float in the request body must not turn its own 422 into a 500.

    Reachable, not hypothetical: FastAPI parses bodies with ``json.loads``,
    which **accepts the bare tokens** ``NaN`` / ``Infinity`` even though they
    are not standard JSON. Pydantic then correctly rejects the value — and the
    error it produces echoes the offending ``input`` verbatim. Starlette renders
    JSON with ``allow_nan=False``, so serializing that body used to raise and
    the caller received an opaque 500 instead of the actionable 422.

    Same failure class the ``jsonable_encoder`` call in the handler already
    fixes once (exception objects in ``ctx``); this is the variant it missed.

    ⚠️ Must be sent as RAW content. ``json={"seconds": float("nan")}`` cannot
    even be encoded by Python's own json module, so writing it that way tests
    the test client's limits rather than the server's behaviour.
    """
    client = TestClient(_build_app())

    resp = client.post(
        "/floaty",
        content=f'{{"seconds": {token}}}',
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "validation_error"
    # The offending value is still reported — stringified, not dropped. An
    # error that hides what you sent is a worse error, not a safer one.
    assert token.lower().replace("infinity", "inf") in str(body["details"]).lower()


def test_a_finite_float_still_reports_its_real_numeric_input() -> None:
    """The scrub must not stringify ordinary numbers on its way past."""
    client = TestClient(_build_app())

    resp = client.post("/floaty", json={"seconds": -1.5})

    assert resp.status_code == 422
    inputs = [e.get("input") for e in resp.json()["details"]]
    assert -1.5 in inputs, inputs


def test_positive_infinity_passes_ge_zero_so_the_guard_must_live_downstream() -> None:
    """``inf >= 0`` is TRUE, so a ``ge=0`` float field does not reject +Infinity.

    Pinned as a fact about the framework, not as an endorsement: it means a
    constrained-float schema is NOT sufficient protection against a non-finite
    value reaching business logic. Anything that hands the number to a
    subprocess or a filesystem call needs its own ``math.isfinite`` check —
    ``/distribution/covers/grab-frame`` has one, because an infinite
    ``-ss`` offset would otherwise reach ffmpeg.

    (The response here comes back as ``null`` rather than raising, which is its
    own quiet trap: the caller sees a 200 with a missing number.)
    """
    client = TestClient(_build_app())

    resp = client.post(
        "/floaty",
        content='{"seconds": Infinity}',
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"seconds": None}
