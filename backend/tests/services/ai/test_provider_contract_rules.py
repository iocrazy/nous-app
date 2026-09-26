"""Rule tests for the provider contract (fh4 T5).

The behaviour is proven through the real consumer in
``tests/runner/test_provider_contract_through_consumer.py``. These pin the
structural rules that keep it true as the code moves:

* every real adapter ``call()`` ends in ``normalize_envelope(...)``;
* ``ProviderResponseError`` stays an ``LLMCallError`` and only ever carries
  catalog codes — and its code survives pickling (DBOS keeps only ``args``);
* the catalog codes a failure can be filed under all have UI copy.
"""

from __future__ import annotations

import ast
import json
import pickle
import re
from pathlib import Path

import pytest

from app.services.ai import error_catalog as ec
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError, classify_error
from app.services.ai.provider_contract import (
    FinishReason,
    ProviderOutcome,
    ProviderResponseError,
    normalize_envelope,
)
from app.services.ai.runner import turn_end as te

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[3]
ADAPTERS = BACKEND / "app/services/ai/adapters"
FRONTEND = BACKEND.parent / "frontend"


# ── (a) every real adapter call() ends in normalize_envelope ─────────────


def _call_methods() -> list[tuple[str, ast.AsyncFunctionDef]]:
    out = []
    for path in sorted(ADAPTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            if any(ast.unparse(b).endswith("Protocol") for b in cls.bases):
                continue  # structural interface (base.py), not an implementation
            for fn in cls.body:
                if isinstance(fn, ast.AsyncFunctionDef) and fn.name == "call":
                    out.append((f"{path.name}:{cls.name}", fn))
    return out


def _returns(fn: ast.AsyncFunctionDef) -> list[ast.Return]:
    """Return statements of ``fn`` itself (not of nested functions)."""
    found: list[ast.Return] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                found.append(child)
            visit(child)

    visit(fn)
    return found


def test_every_adapter_call_returns_through_normalize_envelope():
    methods = _call_methods()
    names = {n.split(":")[1] for n, _ in methods}
    # anti-vacuity: the three real implementations must be found
    assert {"OpenAICompatibleAdapter", "ClaudeAdapter", "CodexDaemonAdapter"} <= names
    offenders = []
    for name, fn in methods:
        rets = _returns(fn)
        if not rets:
            offenders.append(f"{name}: no return")
        for r in rets:
            v = r.value
            ok = (
                isinstance(v, ast.Call)
                and isinstance(v.func, ast.Name)
                and v.func.id == "normalize_envelope"
            )
            if not ok:
                offenders.append(
                    f"{name}:{r.lineno} returns {ast.unparse(v) if v else None}"
                )
    assert not offenders, offenders


# ── (b) the error type and its codes ─────────────────────────────────────


def test_provider_response_error_is_an_llm_call_error():
    assert issubclass(ProviderResponseError, LLMCallError)


def test_new_codes_are_in_the_catalog():
    for code in (
        "PROVIDER_BAD_RESPONSE",
        "PROVIDER_CONTENT_FILTER",
        "PROVIDER_EMPTY_RESPONSE",
    ):
        assert code in ec.ALL_ERROR_CODES


_ERROR_SHAPES = {
    "not_a_dict": [],
    "body_error": {"error": {"code": "InternalServiceError", "message": "x"}},
    "no_choices": {"choices": []},
    "content_filter": {
        "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]
    },
    "sensitive": {
        "choices": [{"message": {"content": "x"}, "finish_reason": "sensitive"}]
    },
    "finish_error": {
        "choices": [{"message": {"content": "x"}, "finish_reason": "error"}]
    },
    "empty_unbilled": {
        "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 0},
    },
    "auth_body": {"error": {"code": 401, "message": "Invalid API key"}},
    "quota_body": {"error": {"code": "SetLimitExceeded", "message": "cap"}},
}


@pytest.mark.parametrize("name", sorted(_ERROR_SHAPES))
def test_every_error_shape_raises_with_a_catalog_code(name):
    with pytest.raises(ProviderResponseError) as ei:
        normalize_envelope(_ERROR_SHAPES[name], model="m")
    assert ei.value.error_code in ec.ALL_ERROR_CODES
    assert ec.classify_ai_error(ei.value) == ei.value.error_code


def test_body_codes_and_policies():
    def outcome(body):
        return ProviderOutcome.from_envelope(body)

    auth = outcome(_ERROR_SHAPES["auth_body"])
    assert (auth.error_code, auth.retry_policy) == ("PROVIDER_AUTH", "fail")
    cap = outcome(_ERROR_SHAPES["quota_body"])
    assert cap.error_code == "PROVIDER_QUOTA_CAP" and cap.retryable
    bad = outcome(_ERROR_SHAPES["body_error"])
    assert (bad.error_code, bad.retry_policy) == ("PROVIDER_BAD_RESPONSE", "retry_once")
    flt = outcome(_ERROR_SHAPES["content_filter"])
    assert flt.retry_policy == "fallback_only" and not flt.retryable


_OK_SHAPES = {
    "stop": {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},
    "tools": {
        "choices": [
            {
                "message": {"content": None, "tool_calls": [{"id": "c1"}]},
                "finish_reason": "tool_calls",
            }
        ]
    },
    "length": {"choices": [{"message": {"content": "ab"}, "finish_reason": "length"}]},
    "empty_billed": {
        "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 649},
    },
    "unknown_finish": {
        "choices": [{"message": {"content": "x"}, "finish_reason": "brand_new_word"}]
    },
}


@pytest.mark.parametrize("name", sorted(_OK_SHAPES))
def test_success_is_returned_byte_identical(name):
    body = _OK_SHAPES[name]
    before = json.dumps(body, sort_keys=True)
    out = normalize_envelope(body, model="m")
    assert out is body
    assert json.dumps(out, sort_keys=True) == before


def test_finish_vocabulary():
    assert ProviderOutcome.from_envelope(_OK_SHAPES["length"]).finish_reason is (
        FinishReason.LENGTH
    )
    assert ProviderOutcome.from_envelope(
        _OK_SHAPES["unknown_finish"]
    ).finish_reason is (FinishReason.UNKNOWN)


# ── (c) survives the DBOS boundary ───────────────────────────────────────


def test_provider_response_error_survives_pickling():
    with pytest.raises(ProviderResponseError) as ei:
        normalize_envelope(_ERROR_SHAPES["content_filter"], model="m")
    back = pickle.loads(pickle.dumps(ei.value))
    assert isinstance(back, ProviderResponseError)
    assert back.error_code == "PROVIDER_CONTENT_FILTER"
    assert back.retry_policy == "fallback_only"
    assert str(back) == str(ei.value)
    # the bare string alone (what task_tracking.error_msg keeps) still classifies
    assert ec.classify_ai_error(str(ei.value)) == "PROVIDER_CONTENT_FILTER"


def test_all_models_failed_marker_survives_pickling():
    exc = AllModelsFailed(
        "all 2 model(s) failed: a (x); b (y) [error_code:PROVIDER_EMPTY_RESPONSE]",
        attempts=[{"model": "b", "error_code": "PROVIDER_EMPTY_RESPONSE"}],
        error_code="PROVIDER_EMPTY_RESPONSE",
    )
    back = pickle.loads(pickle.dumps(exc))
    assert ec.classify_ai_error(back) == "PROVIDER_EMPTY_RESPONSE"


def test_marker_for_a_non_catalog_code_is_ignored():
    assert ec.classify_ai_error("boom [error_code:NotACode]") is None


# ── middleware + turn_end read the typed error ───────────────────────────


@pytest.mark.parametrize(
    "policy, expected",
    [
        ("retry", "retryable"),
        ("retry_once", "retry_once"),
        ("fallback_only", "fallback_only"),
        ("fail", "non_retryable"),
    ],
)
def test_classify_error_reads_the_typed_policy(policy, expected):
    err = ProviderResponseError(
        ProviderOutcome.error("PROVIDER_BAD_RESPONSE", "x", policy=policy)
    )
    assert classify_error(err) == expected


def test_classify_error_treats_transport_drops_as_retryable():
    import httpx

    assert (
        classify_error(httpx.RemoteProtocolError("Server disconnected")) == "retryable"
    )
    assert classify_error(httpx.ReadError("reset")) == "retryable"


def test_turn_end_exception_carries_the_catalog_code():
    err = ProviderResponseError(ProviderOutcome.error("PROVIDER_EMPTY_RESPONSE", "x"))
    reason, extra = te.classify_exception(err)
    assert reason is te.TurnEndReason.ERROR
    assert extra["error_code"] == "PROVIDER_EMPTY_RESPONSE"
    _, extra = te.classify_exception(RuntimeError("kaboom"))
    assert extra["error_code"] == "RuntimeError"  # no catalog code → class name


def test_every_finish_word_is_known_to_turn_end():
    """Every word of the closed vocabulary either has a non-COMPLETED
    turn_end reason or is explicitly a normal end."""
    normal = {"stop", "tool_calls", "empty", "unknown"}
    vocab = {f.value for f in FinishReason}
    assert vocab == normal | set(te.FINISH_REASON_MARKERS), vocab ^ (
        normal | set(te.FINISH_REASON_MARKERS)
    )


@pytest.mark.parametrize(
    "finish, reason, code",
    [
        ("content_filter", "error", "PROVIDER_CONTENT_FILTER"),
        ("error", "error", "PROVIDER_BAD_RESPONSE"),
        ("length", "provider_length", None),
    ],
)
def test_un_normalised_finish_is_not_filed_completed(finish, reason, code):
    from app.services.ai.adapters.base import StreamChunk

    got, extra = te.classify_run_result(
        {"content": "", "raw": {"choices": [{"finish_reason": finish}]}}
    )
    assert got.value == reason and extra.get("error_code") == code
    got, extra = te.classify_stream_end(StreamChunk(finish_reason=finish))
    assert got.value == reason and extra.get("error_code") == code


# ── every catalog code has UI copy on both locales and in the TS list ────


def test_every_catalog_code_has_ui_copy():
    ts = (FRONTEND / "utils/errorCatalog.ts").read_text(encoding="utf-8")
    ts_block = ts.split("export const AI_ERROR_CODES = [", 1)[1].split("] as const", 1)[
        0
    ]
    ts_codes = set(re.findall(r"'([A-Za-z_]+)'", ts_block))
    missing: list[str] = []
    for locale in ("en", "zh"):
        errors = json.loads(
            (FRONTEND / f"public/locales/{locale}.json").read_text(encoding="utf-8")
        )["errors"]
        missing += [
            f"{locale}:{c}"
            for c in ec.ALL_ERROR_CODES
            if "title" not in errors.get(c, {})
        ]
    missing += [f"ts:{c}" for c in ec.ALL_ERROR_CODES if c not in ts_codes]
    assert not missing, missing
