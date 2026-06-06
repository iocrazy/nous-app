"""CI guard: every raw ``text()`` statement in ``resources_repository_orm.py``
that touches the ``resources`` table must be EITHER routed through ``scoped_sql``
OR an explicitly allowlisted membership/folder-scoped method (A3).

THE BYPASS RISK
===============
The app-layer scope choke point (``app/db/scope.py::_enforce_scope`` on
``do_orm_execute``) enforces tenant isolation on ORM statements touching
``resources`` when ``SCOPE_ENFORCE_RESOURCES`` is on. A raw ``text()`` statement
is INVISIBLE to that ORM event — it bypasses injection AND the fail-closed raise
entirely. So a raw read on ``resources`` is an enforcement gap unless it routes
through the ``scoped_sql`` backstop (which BUILDS the ambient tenant predicate
and fail-closes when no scope / no identity is present).

WHY "CALLS scoped_sql", NOT "CONTAINS :scope_user_id" (the security tightening)
-------------------------------------------------------------------------------
``scoped_sql`` is now a predicate BUILDER returning ``(predicate_sql, params)``;
the caller AND-splices ``{pred}`` into its WHERE. The literal SQL therefore no
longer contains the ``:scope_user_id`` token at all — it lives in the helper.
More importantly, a bare-substring check is FAIL-OPEN: a token parked in the
SELECT list, a ``:scope_user_id = :scope_user_id`` tautology, or ``... OR 1=1``
all "contain the token" yet run an unfiltered cross-tenant read. So the guard
keys on the AUTHORITATIVE signal — the enclosing method CALLS ``scoped_sql`` (the
builder owns the predicate shape; the caller cannot misplace it). A method that
hand-writes ``:scope_user_id`` WITHOUT routing through the builder is treated as
NOT scoped (it must use the builder), closing the misplacement blind spot.

This test ``ast``-scans ``resources_repository_orm.py`` for every ``text(...)``
call whose SQL references the ``resources`` table (``FROM resources`` /
``JOIN resources`` / ``UPDATE resources``) and asserts each is one of:

  1. SCOPED — its enclosing method calls ``scoped_sql`` (the only sanctioned way
     to express a raw tenant predicate; the helper builds + binds it);
  2. ALLOWLISTED — one of the five methods whose ``resources`` access is scoped
     by folder / resource_items membership (``scope_id`` / folder), NOT by
     ``creator_id``. Forcing creator scope on these would break team-library and
     shared-folder semantics, so they are deliberate, documented bypasses.

Any NEW bare ``text()`` on ``resources`` outside those two categories FAILS —
pointing the dev at ``scoped_sql`` so the choke-point bypass is closed.

Run: ``uv run pytest tests/db/test_resources_text_sql_guard.py -v``
"""

from __future__ import annotations

import ast
from pathlib import Path

# The helper that BUILDS the tenant predicate. The authoritative "scoped" signal
# is that the enclosing method calls it (not a substring of the SQL text).
_SCOPED_SQL_FN = "scoped_sql"
# The bound token the builder emits. Used only to flag a HAND-WRITTEN predicate
# (token in the literal SQL without routing through the builder) as suspicious.
_SCOPE_TOKEN = ":scope_user_id"

_REPO_FILE = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "repositories"
    / "resources_repository_orm.py"
)

# Methods whose raw text() on ``resources`` is scoped by folder/resource_items
# MEMBERSHIP (scope_id / folder_id), not by creator_id — forcing creator scope
# would break team-library + shared-folder semantics. Each carries its reason.
_ALLOWLIST: dict[str, str] = {
    "get_resource_items": (
        "JOIN resources via resource_items filtered by i.scope_id (library/folder "
        "membership), not creator_id"
    ),
    "_resource_ids_for_platforms": (
        "resolves resource ids by media_id platform lookup; scoped downstream by "
        "the membership-filtered get_resource_items that consumes the ids"
    ),
    "get_trashed_resources": (
        "JOIN resources via resource_items filtered by i.scope_id (scope membership), "
        "not creator_id"
    ),
    "restore_folder_cascade": (
        "UPDATE resources by folder subtree membership (resource_items.folder_id), "
        "not creator_id"
    ),
    "trash_folder_cascade": (
        "UPDATE resources by folder subtree membership (resource_items.folder_id), "
        "not creator_id"
    ),
}


def _literal_parts(node: ast.AST) -> str:
    """Concatenate every readable literal string fragment inside a SQL expression
    (string literal, implicit/``+`` concat, f-string literal segments). Mirrors
    the helper in test_no_returning_via_fetch.py."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal_parts(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_parts(node.left) + " " + _literal_parts(node.right)
    if isinstance(node, ast.Name):
        return ""  # resolved separately via in-function assignments
    return ""


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _references_resources(sql: str) -> bool:
    """True if the SQL reads/writes the ``resources`` table (NOT a same-prefixed
    table like ``resource_items`` / ``resource_versions`` / ``resource_tags``).
    We match the table token after FROM/JOIN/UPDATE with a trailing word boundary
    so ``FROM resource_items`` does NOT trip it."""
    lowered = " ".join(sql.lower().split())
    for kw in ("from resources", "join resources", "update resources"):
        idx = 0
        while True:
            pos = lowered.find(kw, idx)
            if pos == -1:
                break
            after = pos + len(kw)
            # word boundary: end-of-string or a non-identifier char (space, etc).
            nxt = lowered[after] if after < len(lowered) else " "
            if not (nxt.isalnum() or nxt == "_"):
                return True
            idx = after
    return False


def _enclosing_method_node(
    tree: ast.AST, target: ast.Call
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The FunctionDef/AsyncFunctionDef node enclosing ``target`` (innermost)."""
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if inner is target:
                    best = node
    return best


def _method_calls_scoped_sql(method: ast.AST | None) -> bool:
    """True if ``scoped_sql(...)`` is called anywhere inside the method body."""
    if method is None:
        return False
    return any(
        isinstance(n, ast.Call) and _called_name(n) == _SCOPED_SQL_FN
        for n in ast.walk(method)
    )


def _in_function_sql(method: ast.AST | None) -> str:
    """All readable SQL-string literals assigned to a Name inside ``method``
    (catches ``sql = (...)`` then ``text(sql)``)."""
    if method is None:
        return ""
    parts: list[str] = []
    for inner in ast.walk(method):
        if isinstance(inner, ast.Assign):
            readable = _literal_parts(inner.value)
            if readable.strip():
                parts.append(readable)
    return " ".join(parts)


def _scan() -> list[tuple[int, str, str, bool]]:
    """Return ``(lineno, method, full_sql, calls_scoped_sql)`` for every
    ``text(...)`` call whose SQL references the ``resources`` table.

    ``full_sql`` includes both the inline ``text(...)`` literal AND any
    ``sql = (...)`` variable assigned in the same method (the f-string/variable
    splice form). ``calls_scoped_sql`` is the authoritative "scoped" signal."""
    tree = ast.parse(_REPO_FILE.read_text(encoding="utf-8"), filename=str(_REPO_FILE))
    hits: list[tuple[int, str, str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node) != "text":
            continue
        arg = node.args[0] if node.args else None
        if arg is None:
            continue
        method_node = _enclosing_method_node(tree, node)
        method = method_node.name if method_node is not None else "<module>"
        # SQL may be inline in text(...) OR built into a variable then passed as
        # text(sql); union both so the variable-splice form is seen.
        readable = _literal_parts(arg)
        if isinstance(arg, ast.Name):
            readable = (readable + " " + _in_function_sql(method_node)).strip()
        if not _references_resources(readable):
            continue
        full_sql = " ".join(readable.split())
        hits.append(
            (node.lineno, method, full_sql, _method_calls_scoped_sql(method_node))
        )
    return hits


def test_every_resources_text_sql_is_scoped_or_allowlisted():
    """Each raw ``text()`` touching ``resources`` must route through
    ``scoped_sql`` (its enclosing method calls the builder) or be an allowlisted
    membership-scoped method. A new bare one fails closed here.

    A method that HAND-WRITES the ``:scope_user_id`` token WITHOUT calling the
    builder is NOT accepted — that is exactly the misplaceable form the builder
    refactor removed (token-in-SELECT-list / tautology / OR 1=1)."""
    assert _REPO_FILE.is_file(), f"repo file not found at {_REPO_FILE}"

    offenders: list[str] = []
    for lineno, method, full_sql, calls_builder in _scan():
        allowlisted = method in _ALLOWLIST
        if calls_builder or allowlisted:
            continue
        why = ""
        if _SCOPE_TOKEN in full_sql:
            why = (
                " (it hand-writes :scope_user_id but does NOT call scoped_sql — a "
                "hand-written predicate can be misplaced; use the builder)"
            )
        offenders.append(
            f"  {_REPO_FILE.name}:{lineno} in {method}() — {full_sql[:90]!r}{why}"
        )

    assert not offenders, (
        "Raw text() on the `resources` table bypasses the ORM scope choke point "
        "(do_orm_execute is blind to text()) — it gets NO tenant injection and NO "
        "fail-closed raise, an enforcement gap. Route the tenant predicate through "
        "app.db.scope.scoped_sql (a predicate BUILDER: `pred, params = "
        'scoped_sql("r.creator_id", {...})` then `f"... WHERE {pred} ..."`), OR — '
        "if the read is scoped by folder/library membership rather than creator_id "
        "— add the method to the documented _ALLOWLIST in this test with a reason. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_both_rerouted_methods_route_through_builder():
    """Positive pin: the two A3-rerouted creator-scoped reads MUST route through
    ``scoped_sql`` (so a future edit reverting them to a hand-written
    ``:creator_id`` filter is caught here, not just by the negative guard)."""
    scanned = {method: calls for _, method, _sql, calls in _scan()}
    for method in (
        "get_completed_resource_by_url_and_creator",
        "get_owned_platform_ids",
    ):
        assert (
            method in scanned
        ), f"{method} no longer has a resources text() — re-check"
        assert scanned[method], (
            f"{method} no longer routes through {_SCOPED_SQL_FN}() — it would "
            "bypass the choke point (or hand-write a misplaceable predicate)"
        )


def test_allowlist_entries_are_real_methods():
    """Every allowlisted name must actually be a method in the repo file (no
    stale entries silently widening the bypass surface)."""
    tree = ast.parse(_REPO_FILE.read_text(encoding="utf-8"), filename=str(_REPO_FILE))
    method_names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    stale = [m for m in _ALLOWLIST if m not in method_names]
    assert not stale, f"_ALLOWLIST has stale (non-existent) methods: {stale}"


# ── Synthetic planted-offender self-tests (the guard must not be a no-op) ──


def _scan_source(src: str) -> list[tuple[str, str, bool]]:
    """Run the same (method, full_sql, calls_scoped_sql) classification over a
    synthetic source string. Returns only resources-touching text() calls."""
    tree = ast.parse(src)
    out: list[tuple[str, str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node) != "text":
            continue
        arg = node.args[0] if node.args else None
        if arg is None:
            continue
        method_node = _enclosing_method_node(tree, node)
        method = method_node.name if method_node is not None else "<module>"
        readable = _literal_parts(arg)
        if isinstance(arg, ast.Name):
            readable = (readable + " " + _in_function_sql(method_node)).strip()
        if not _references_resources(readable):
            continue
        out.append(
            (method, " ".join(readable.split()), _method_calls_scoped_sql(method_node))
        )
    return out


def test_guard_detects_a_planted_bare_resources_text():
    """The scanner must flag a NEW bare ``text()`` on ``resources`` that does NOT
    route through the builder (and is not allowlisted)."""
    src = (
        "from sqlalchemy import text\n"
        "class R:\n"
        "    async def new_unscoped(self, session):\n"
        "        return await session.execute(\n"
        '            text("SELECT id FROM resources WHERE x = :x"), {"x": 1}\n'
        "        )\n"
    )
    hits = _scan_source(src)
    assert hits, "scanner failed to see the planted resources text()"
    method, _sql, calls_builder = hits[0]
    assert not calls_builder, "planted bare text() wrongly classified as scoped"
    assert method not in _ALLOWLIST


def test_guard_rejects_token_in_non_filtering_position():
    """THE TIGHTENING: a hand-written ``:scope_user_id`` parked in a NON-filtering
    position (here the SELECT list / a tautology) WITHOUT calling the builder must
    NOT count as scoped — the old bare-substring check would have passed it."""
    src = (
        "from sqlalchemy import text\n"
        "class R:\n"
        "    async def sneaky(self, session):\n"
        "        return await session.execute(\n"
        "            text(\n"
        '                "SELECT id, :scope_user_id AS _x FROM resources "\n'
        '                "WHERE :scope_user_id = :scope_user_id OR 1=1"\n'
        "            ),\n"
        '            {"scope_user_id": None},\n'
        "        )\n"
    )
    hits = _scan_source(src)
    assert hits, "scanner failed to see the sneaky resources text()"
    _method, full_sql, calls_builder = hits[0]
    assert _SCOPE_TOKEN in full_sql, "fixture should contain the token"
    assert not calls_builder, (
        "a hand-written token NOT routed through scoped_sql must be classified "
        "UNSCOPED — the builder ownership is the only accepted signal"
    )


def test_guard_accepts_builder_routed_method():
    """A method that calls ``scoped_sql`` and splices the built predicate is
    SCOPED even though its literal SQL no longer carries the token."""
    src = (
        "from sqlalchemy import text\n"
        "from app.db.scope import scoped_sql\n"
        "class R:\n"
        "    async def good(self, session, url):\n"
        '        pred, params = scoped_sql("r.creator_id", {"url": url})\n'
        '        sql = f"SELECT r.id FROM resources r WHERE {pred} AND r.x = :url"\n'
        "        return await session.execute(text(sql), params)\n"
    )
    hits = _scan_source(src)
    assert hits, "scanner failed to see the builder-routed resources text()"
    _method, full_sql, calls_builder = hits[0]
    assert _SCOPE_TOKEN not in full_sql, "builder form should NOT carry the token"
    assert calls_builder, "builder-routed method must be classified scoped"
