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
(``fetch_one`` / ``fetch_val`` / ``fetch_value``), and inspects its SQL argument.
If that SQL is a WRITE (contains ``RETURNING`` or begins with
INSERT/UPDATE/DELETE), the test FAILS — that write would silently roll back.
Reroute it through the committing ``db_engine.execute`` /
``execute_returning_one`` / ``execute_returning_val`` instead.

Two detection forms — BOTH matter, because the bug took the SECOND form (the
base ``insert`` / ``update_by_id`` built ``... RETURNING *`` via f-strings):

  1. **Literal SQL** — a pure string literal (incl. implicit / ``+`` concat)
     that is a write. Caught directly.
  2. **Non-literal SQL** — an f-string (``ast.JoinedStr``), a variable
     (``Name``), a ``BinOp`` concat, or a ``"".join(...)`` whose write signal we
     can still see: a write verb / ``RETURNING`` in an f-string's literal parts,
     OR (for a variable passed in) a write verb / ``RETURNING`` in a SQL string
     assigned to that name within the SAME enclosing function. A plain dynamic
     SELECT (``f"SELECT ... WHERE id={id}"``) carries NO write signal → ignored.

Robust to false positives by design: the verb/RETURNING signal must actually be
present in SQL text the scanner can read. Plain SELECTs (literal or dynamic),
SQL built entirely elsewhere with no in-function write signal, and the
committing helpers are all ignored.

Run: ``uv run pytest tests/db/test_no_returning_via_fetch.py -v``
"""

from __future__ import annotations

import ast
import re
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


def _literal_parts(node: ast.AST) -> str:
    """Concatenate every literal string fragment reachable inside a non-literal
    SQL expression — the parts we CAN read. Covers f-strings (``JoinedStr`` /
    its ``Constant`` segments; interpolations contribute nothing), ``+`` concat,
    and ``sep.join([... literals ...])``. Returns the joined readable text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal_parts(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return ""  # the interpolated expression itself is not readable SQL text
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_parts(node.left) + " " + _literal_parts(node.right)
    if isinstance(node, ast.Call):
        # "sep".join([...]) — scan the joined literal elements.
        parts: list[str] = []
        for a in node.args:
            if isinstance(a, (ast.List, ast.Tuple)):
                parts.extend(_literal_parts(e) for e in a.elts)
            else:
                parts.append(_literal_parts(a))
        return " ".join(parts)
    return ""


def _write_signal(text_blob: str) -> bool:
    """A write signal = RETURNING anywhere, or a mutating verb as a leading
    token of any whitespace-delimited clause. We check token-leading (not bare
    substring) so a column named ``last_updated`` or ``deleted_at`` inside a
    SELECT does NOT trip the verb check; RETURNING is distinctive enough to
    match anywhere."""
    upper = text_blob.upper()
    if "RETURNING" in upper:
        return True
    # Tokenize loosely; flag if any token starts a mutating statement. We look
    # for a verb followed by whitespace (so "UPDATE " / "INSERT " etc.), which a
    # write clause always has but an identifier like "UPDATED_AT" never does.
    for verb in _WRITE_VERBS:
        if re.search(rf"(^|[(\s]){verb}\s", upper):
            return True
    return False


def _called_name(call: ast.Call) -> str | None:
    """The bare method/function name being called (``x.fetch_one`` →
    ``fetch_one``; ``fetch_one`` → ``fetch_one``)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _enclosing_sql_assignments(scope: ast.AST) -> dict[str, str]:
    """Map ``var_name → readable SQL text`` for every ``name = <sql expr>``
    assignment directly inside one function scope. Used to resolve a SQL passed
    to fetch_* as a bare variable (``row = await db.fetch_one(sql, ...)``) back
    to the string it was built from in the same function — the form the base
    ``insert``/``update_by_id`` used. Nested-function assignments are ignored
    (different scope) to keep the heuristic tight."""
    out: dict[str, str] = {}
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        readable = _literal_parts(node.value)
        if not readable.strip():
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                # Accumulate (a name may be reassigned in if/else branches; any
                # branch carrying a write signal should count).
                out[tgt.id] = (out.get(tgt.id, "") + " " + readable).strip()
    return out


def _sql_arg(call: ast.Call) -> ast.AST | None:
    """The SQL argument of a fetch_* call. db_engine.fetch_one(sql, params) and
    self.fetch_one(sql, *args) both put SQL first; keyword ``sql=`` is also
    handled."""
    for kw in call.keywords:
        if kw.arg == "sql":
            return kw.value
    return call.args[0] if call.args else None


def _scan_scope(scope: ast.AST, path: Path, hits: list[str], rel: Path | str) -> None:
    """Scan one function/module scope for write-through-read-helper calls."""
    assignments = _enclosing_sql_assignments(scope)
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        if _called_name(node) not in _READ_HELPERS:
            continue
        arg = _sql_arg(node)
        if arg is None:
            continue

        # 1) Pure literal write SQL.
        literal = _fold_str(arg)
        if literal is not None:
            if _is_write_sql(literal):
                snippet = " ".join(literal.split())[:80]
                hits.append(f"{rel}:{node.lineno} — literal: {snippet!r}")
            continue  # a readable literal is fully judged; don't double-count

        # 2) Non-literal SQL (f-string / concat / join / variable). Look for a
        #    write signal in the readable parts, or — for a bare variable — in
        #    the SQL string assigned to that name in this same function.
        readable = _literal_parts(arg)
        if isinstance(arg, ast.Name):
            readable = (readable + " " + assignments.get(arg.id, "")).strip()
        if readable.strip() and _write_signal(readable):
            snippet = " ".join(readable.split())[:80]
            hits.append(f"{rel}:{node.lineno} — dynamic: {snippet!r}")


def _scan_file(path: Path) -> list[str]:
    """Return human-readable ``file:line — sql-snippet`` for every write routed
    through a read helper in this file (literal OR non-literal write SQL)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - app/ is always valid python
        return []
    try:
        rel: Path | str = path.relative_to(_APP_ROOT.parent)
    except ValueError:  # e.g. synthetic test files outside the repo
        rel = path.name

    hits: list[str] = []
    # Scan each function scope separately so the variable-assignment lookup is
    # function-local; also scan module top-level for module-scope fetch_* calls.
    func_nodes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for fn in func_nodes:
        _scan_scope(fn, path, hits, rel)
    # Module-scope calls not inside any function (rare, but don't miss them).
    _scan_module_level(tree, func_nodes, path, hits, rel)
    # A call nested inside an inner def is walked once per enclosing def; dedup
    # by (line, message) so it's reported a single time.
    return list(dict.fromkeys(hits))


def _scan_module_level(
    tree: ast.Module,
    func_nodes: list,
    path: Path,
    hits: list[str],
    rel: Path | str,
) -> None:
    """Catch fetch_* calls that sit at module top level (outside any def)."""
    func_call_ids = set()
    for fn in func_nodes:
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                func_call_ids.add(id(n))
    module_assignments = _enclosing_sql_assignments_module(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in func_call_ids:
            continue
        if _called_name(node) not in _READ_HELPERS:
            continue
        arg = _sql_arg(node)
        if arg is None:
            continue
        literal = _fold_str(arg)
        if literal is not None:
            if _is_write_sql(literal):
                snippet = " ".join(literal.split())[:80]
                hits.append(f"{rel}:{node.lineno} — literal: {snippet!r}")
            continue
        readable = _literal_parts(arg)
        if isinstance(arg, ast.Name):
            readable = (readable + " " + module_assignments.get(arg.id, "")).strip()
        if readable.strip() and _write_signal(readable):
            snippet = " ".join(readable.split())[:80]
            hits.append(f"{rel}:{node.lineno} — dynamic: {snippet!r}")


def _enclosing_sql_assignments_module(tree: ast.Module) -> dict[str, str]:
    """Module-top-level SQL assignments only (skip those inside functions)."""
    out: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            readable = _literal_parts(stmt.value)
            if not readable.strip():
                continue
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    out[tgt.id] = (out.get(tgt.id, "") + " " + readable).strip()
    return out


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


def test_guard_detects_a_literal_offender(tmp_path):
    """The scanner must catch a planted LITERAL RETURNING write, so a future
    refactor can't silently neuter the guard into always-passing."""
    bad = tmp_path / "bad_literal.py"
    bad.write_text(
        "async def f(repo):\n"
        "    return await repo.fetch_one(\n"
        '        "INSERT INTO t (a) VALUES ($1) RETURNING *", 1\n'
        "    )\n",
        encoding="utf-8",
    )
    hits = _scan_file(bad)
    assert hits, "scanner failed to flag a planted literal RETURNING write"
    assert "literal" in hits[0]


def test_guard_detects_an_fstring_offender(tmp_path):
    """THE GAP THIS COMMIT CLOSES: an f-string-built write reaching fetch_one
    is EXACTLY the form the base insert/update_by_id used (``f"... RETURNING
    *"``). The scanner must flag it even though the SQL is non-literal."""
    bad = tmp_path / "bad_fstring.py"
    bad.write_text(
        "async def f(repo, id):\n"
        "    return await repo.fetch_one(\n"
        '        f"UPDATE t SET x=1 WHERE id={id} RETURNING *", id\n'
        "    )\n",
        encoding="utf-8",
    )
    hits = _scan_file(bad)
    assert hits, "scanner MISSED an f-string RETURNING write (the bug's own form)"
    assert "dynamic" in hits[0]


def test_guard_detects_a_variable_built_offender(tmp_path):
    """Also flag the base-method form: SQL built into a variable via f-string,
    then passed by name to fetch_one — the write signal lives in the in-function
    assignment, not the call arg."""
    bad = tmp_path / "bad_variable.py"
    bad.write_text(
        "async def f(repo, table, id):\n"
        "    sql = f'UPDATE \"{table}\" SET x=1 WHERE id=$1 RETURNING *'\n"
        "    return await repo.fetch_one(sql, id)\n",
        encoding="utf-8",
    )
    hits = _scan_file(bad)
    assert hits, "scanner MISSED a variable-built RETURNING write"


def test_guard_does_not_flag_clean_selects(tmp_path):
    """No false positives: a literal SELECT and a dynamic (f-string / variable)
    SELECT must BOTH pass clean — the write detection keys on RETURNING / a
    leading write verb, neither of which a SELECT carries."""
    good = tmp_path / "good.py"
    good.write_text(
        "async def f(repo, id, table):\n"
        '    a = await repo.fetch_one("SELECT * FROM t WHERE id = $1", id)\n'
        '    b = await repo.fetch_one(f"SELECT * FROM t WHERE id={id}", id)\n'
        '    sql = f"SELECT updated_at, deleted_at FROM {table} WHERE id=$1"\n'
        "    c = await repo.fetch_one(sql, id)\n"
        "    return a, b, c\n",
        encoding="utf-8",
    )
    assert not _scan_file(good), "scanner wrongly flagged a clean SELECT"
