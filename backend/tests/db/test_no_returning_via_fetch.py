"""CI grep-guard: the ``fetch_one(...RETURNING)``-as-write antipattern must
not come back (Task 5.6).

THE BUG CLASS (#498 / silent-rollback P0 — the whole reason for the ORM
migration): ``db_engine.fetch_one`` / ``fetch_val`` (and the
``AsyncpgRepository`` ``self.fetch_one`` / ``self.fetch_value`` wrappers) run on
``engine.connect()`` (NO transaction → NEVER commits). An
``INSERT/UPDATE/DELETE ... RETURNING`` issued through one of them EXECUTES, hands
back the RETURNING row (visible within the connection), then SILENTLY ROLLS BACK
on connection close — the caller thinks it persisted but it didn't.

This test walks the whole ``app/`` source with ``ast`` (not a brittle line
regex), finds every call whose function name is a non-committing read helper
(``fetch_one`` / ``fetch_val`` / ``fetch_value``), and inspects its first
string-literal SQL argument. If that SQL is a WRITE (contains ``RETURNING`` or
begins with INSERT/UPDATE/DELETE), the test FAILS — that write would silently
roll back. Reroute it through the committing ``db_engine.execute`` /
``execute_returning_one`` / ``execute_returning_val`` instead.

Robust to false positives by design: it only flags calls where the SQL is a
*literal* the parser can read AND that literal is actually a write. SELECTs,
dynamic SQL built elsewhere, and the committing helpers are all ignored.

Run: ``uv run pytest tests/db/test_no_returning_via_fetch.py -v``
"""

from __future__ import annotations

import ast
from pathlib import Path

# The non-committing read helpers. A write routed through any of these silently
# rolls back. (``fetch_all`` is omitted: a write-RETURNING that wants many rows
# is vanishingly rare and the same scan extension would apply; the live foot-gun
# is the single-row/scalar helpers.)
_READ_HELPERS = {"fetch_one", "fetch_val", "fetch_value"}

# A SQL literal is a WRITE if it contains RETURNING (any cased) or its first
# keyword is a mutating verb.
_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "UPSERT")

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _string_literal_args(call: ast.Call) -> list[str]:
    """Collect the (possibly implicitly-concatenated) string-literal value of
    each positional arg that is a pure string literal. Implicit concatenation
    of adjacent string literals is already folded by the parser into a single
    ``ast.Constant``; an explicit ``"a" + "b"`` becomes a ``BinOp`` we also
    fold here. Non-literal args (variables, f-strings) yield nothing."""
    out: list[str] = []
    for arg in call.args:
        val = _fold_str(arg)
        if val is not None:
            out.append(val)
    return out


def _fold_str(node: ast.AST) -> str | None:
    """Return the constant string value of a literal / literal-concatenation
    node, or None if it isn't a pure string literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_str(node.left)
        right = _fold_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _is_write_sql(sql: str) -> bool:
    upper = sql.upper()
    if "RETURNING" in upper:
        return True
    # First non-whitespace word is a mutating verb.
    stripped = upper.lstrip().lstrip("(")
    return any(stripped.startswith(v) for v in _WRITE_VERBS)


def _called_name(call: ast.Call) -> str | None:
    """The bare method/function name being called (``x.fetch_one`` →
    ``fetch_one``; ``fetch_one`` → ``fetch_one``)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _scan_file(path: Path) -> list[str]:
    """Return human-readable ``file:line — sql-snippet`` for every write routed
    through a read helper in this file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - app/ is always valid python
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name not in _READ_HELPERS:
            continue
        for sql in _string_literal_args(node):
            if _is_write_sql(sql):
                snippet = " ".join(sql.split())[:80]
                try:
                    rel: Path | str = path.relative_to(_APP_ROOT.parent)
                except ValueError:  # e.g. synthetic test files outside the repo
                    rel = path.name
                hits.append(f"{rel}:{node.lineno} — {snippet!r}")
                break
    return hits


def test_no_write_routed_through_read_helper():
    """No ``fetch_one`` / ``fetch_val`` / ``fetch_value`` call in ``app/`` may
    carry a write SQL literal (RETURNING or INSERT/UPDATE/DELETE). Writes must
    go through the committing ``db_engine.execute*`` helpers."""
    assert _APP_ROOT.is_dir(), f"app/ not found at {_APP_ROOT}"

    offenders: list[str] = []
    for py in sorted(_APP_ROOT.rglob("*.py")):
        offenders.extend(_scan_file(py))

    assert not offenders, (
        "fetch_one/fetch_val(...RETURNING)-as-write antipattern reintroduced — "
        "these run on a non-committing connection and SILENTLY ROLL BACK the "
        "write (the #498 class). Reroute through db_engine.execute / "
        "execute_returning_one / execute_returning_val:\n  " + "\n  ".join(offenders)
    )


def test_guard_detects_a_synthetic_offender(tmp_path):
    """Sanity-check the scanner itself catches a planted offender, so a future
    refactor can't silently neuter the guard into always-passing."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "async def f(repo):\n"
        "    return await repo.fetch_one(\n"
        '        "INSERT INTO t (a) VALUES ($1) RETURNING *", 1\n'
        "    )\n",
        encoding="utf-8",
    )
    assert _scan_file(bad), "scanner failed to flag a planted RETURNING write"

    good = tmp_path / "good.py"
    good.write_text(
        "async def f(repo):\n"
        '    return await repo.fetch_one("SELECT * FROM t WHERE id = $1", 1)\n',
        encoding="utf-8",
    )
    assert not _scan_file(good), "scanner wrongly flagged a plain SELECT"
