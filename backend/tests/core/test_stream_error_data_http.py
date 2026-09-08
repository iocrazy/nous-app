"""Phase 2a: a typed HTTPException raised inside a streamed turn keeps its
code on the SSE error event instead of collapsing to internal_error."""

import pytest
from fastapi import HTTPException

from app.core.provider_errors import stream_error_data

pytestmark = pytest.mark.unit


def test_typed_http_detail_keeps_its_code():
    exc = HTTPException(
        status_code=409,
        detail={"code": "no_open_question", "message": "no open question 'q:1:2'"},
    )
    assert stream_error_data(exc) == {
        "error": "no open question 'q:1:2'",
        "code": "no_open_question",
        "status": 409,
    }


def test_plain_http_detail_gets_a_status_code():
    out = stream_error_data(HTTPException(status_code=404, detail="session not found"))
    assert out == {"error": "session not found", "code": "http_404", "status": 404}


def test_other_exceptions_stay_internal_error():
    assert stream_error_data(RuntimeError("boom"))["code"] == "internal_error"
