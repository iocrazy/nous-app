"""Nobody reads an adapter response as a flat dict again.

`test_response_envelope.py` proves the reader works. This file proves nothing
bypasses it — the failure that shipped was not a broken helper but six call
sites that never had one, each paired with a mock feeding it a shape no
adapter produces.

Two scans, because the defect needed BOTH halves to stay invisible:

  production  a `.get("content")` on an `adapter.call()` result is `None`,
              and four of the six coerced that to `""` — no exception, no
              log, no failing test.
  tests       a mock returning `{"content": ...}` makes the broken read pass,
              so the suite actively certified the bug.

Fixing only the production half leaves the mocks free to certify the next one.
"""

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[4]
APP = BACKEND / "app"
TESTS = BACKEND / "tests"

# `adapter.call(...)` assigned to a name, then that name read as a flat dict
# within a few lines. Deliberately narrow: it targets the exact defect shape
# rather than every `.get("content")` in the tree (most of those read
# `runner.run_turn()` output, where "content" IS the right key).
_CALL_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*await\s+[\w.]*adapter\.call\(", re.M)


def _flat_reads(text: str) -> list[tuple[str, str]]:
    hits = []
    lines = text.splitlines()
    for m in _CALL_ASSIGN.finditer(text):
        var = m.group(1)
        start = text[: m.start()].count("\n")
        for line in lines[start : start + 8]:
            if re.search(rf"\b{re.escape(var)}\s*\.\s*get\(\s*[\"']content[\"']", line):
                hits.append((var, line.strip()))
    return hits


def _dict_keys(node) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {
        k.value
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def _is_flat_response(node) -> bool:
    """A dict standing in for an ADAPTER response but missing the envelope.

    Two exclusions, both from real code in this repo rather than caution:

    * ``isError`` marks an MCP ``ToolCallResult.to_dict()``
      (``{"content": [...], "isError": bool}``) — a different ``.call``, whose
      flat shape is correct. ``mcp_reg.call`` fakes are not our defect.
    * a list-valued ``content`` is a block list, not the assistant string an
      adapter response carries.

    Requiring a string-valued ``content`` is what separates "someone faked an
    LLM reply wrong" from "someone faked a different protocol right".
    """
    if not isinstance(node, ast.Dict):
        return False
    keys = _dict_keys(node)
    if "choices" in keys or "role" in keys or "isError" in keys:
        return False
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and k.value == "content":
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                return True
            # f-string / variable stands in for a string body just as well.
            if isinstance(v, (ast.JoinedStr, ast.Name)):
                return True
            return False
    return False


def _returned_dicts(fn):
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            yield sub.value


def _bad_call_fakes(tree):
    """(lineno, snippet) for every `.call` fake returning a flat response."""
    funcs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [
            tgt
            for tgt in node.targets
            if isinstance(tgt, ast.Attribute) and tgt.attr == "call"
        ]
        if not targets:
            continue
        value = node.value
        # form 1: .call = AsyncMock(return_value={...})
        if isinstance(value, ast.Call):
            for kw in value.keywords:
                if kw.arg == "return_value" and _is_flat_response(kw.value):
                    yield node.lineno, "AsyncMock(return_value={'content': ...})"
        # form 2: .call = _some_async_fn  (defined in the same module)
        elif isinstance(value, ast.Name) and value.id in funcs:
            for d in _returned_dicts(funcs[value.id]):
                if _is_flat_response(d):
                    yield d.lineno, f"{value.id}() returns {{'content': ...}}"


@pytest.mark.unit
def test_no_production_code_reads_an_adapter_response_as_a_flat_dict():
    offenders: list[str] = []
    scanned = 0
    for path in sorted(APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "adapter.call(" not in text:
            continue
        scanned += 1
        for _, line in _flat_reads(text):
            offenders.append(f"{path.relative_to(BACKEND)}: {line}")
    # Self-check: a scan that inspected nothing must not read as "all clean".
    assert scanned > 0, "scanned no files containing adapter.call( — scanner broke"
    assert not offenders, (
        "these read an adapter response as a flat dict; the envelope is "
        "{'choices':[{'message':{'content':...}}]} — use "
        "app.services.ai.adapters.response.adapter_text:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_no_test_mocks_an_adapter_call_with_a_shape_no_adapter_produces():
    """A mock is a claim about a boundary. `{"content": ...}` claims something
    false, and a false claim in a fixture is worse than no test at all — it
    converts a production failure into a green suite.

    AST, not regex: the third offender found on 2026-08-23 was a plain
    ``async def _call(...): return {"content": ...}`` assigned to ``.call``,
    which a regex tuned to ``AsyncMock(return_value=...)`` walked straight
    past. A guard with a blind spot is where the next one lands.

    Only ``.call`` assignments are inspected, so fakes for ``run_turn`` — where
    a flat ``{"content": ...}`` IS the right shape — are untouched.
    """
    offenders: list[str] = []
    scanned = 0
    for path in sorted(TESTS.rglob("*.py")):
        src = path.read_text(encoding="utf-8", errors="replace")
        if ".call" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue
        scanned += 1
        for bad_line, snippet in _bad_call_fakes(tree):
            offenders.append(f"{path.relative_to(BACKEND)}:{bad_line}: {snippet}")
    assert scanned > 0, "scanned no test files mentioning .call — scanner broke"
    assert not offenders, (
        "these fake adapter.call with a shape no adapter returns; build the "
        "response as {'choices':[{'message':{'role':'assistant',"
        "'content':...}}]}:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_the_scanner_actually_matches_the_defect():
    """Both scans above pass by finding nothing — so prove they CAN find it.

    Without this, a regex that silently stopped matching would look exactly
    like a clean codebase.
    """
    sample = """
        resp = await adapter.call(composed, [{"role": "user", "content": p}])
        return resp.get("content") or ""
    """
    assert _flat_reads(sample), "the production scanner no longer detects the defect"

    # Both mock forms that have actually shipped in this repo.
    mock_forms = ast.parse(
        """
a.call = AsyncMock(return_value={"content": "x"})

async def _call(cs, messages):
    return {"content": "SUMMARY"}

b.call = _call
"""
    )
    found = {snippet for _, snippet in _bad_call_fakes(mock_forms)}
    assert len(found) == 2, f"the mock scanner misses a known form: {found}"

    # Negative cases, all taken from code that really lives in this repo.
    ok = ast.parse(
        """
a.call = AsyncMock(return_value={"choices": [{"message": {"content": "x"}}]})
r.run_turn = AsyncMock(return_value={"content": "x"})
mcp_reg.call = AsyncMock(
    return_value={"content": [{"type": "text", "text": "page created!"}], "isError": False}
)
"""
    )
    assert not list(_bad_call_fakes(ok)), (
        "the mock scanner flags correct fakes — an MCP ToolCallResult and a "
        "run_turn stand-in are both legitimately flat"
    )
