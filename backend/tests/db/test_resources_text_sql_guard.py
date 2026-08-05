"""CI guard: every raw ``text()`` statement ANYWHERE under ``app/repositories/``
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

WHY THE WHOLE DIRECTORY, NOT JUST resources_repository.py (Phase C final review,
2026-08-05)
---------------------------------------------------------------------------------
This guard used to scan ONLY ``resources_repository.py``. Three consecutive
review rounds (Task 2, Task 3, and the final cross-branch review) each found a
NEW batch of ``resources``-touching raw SQL that a manual grep had missed —
the final review's catch was ``canvas_refs_repository.py``'s two ``JOIN
resources`` sites, invisible to the single-file scanner by construction (they
live in a completely different file) AND to a naive ``FROM/UPDATE resources``
grep (they're JOINs). Scanning every file under ``app/repositories/`` turns
this from a one-shot audit that goes stale the moment someone adds a new repo
file into a CI-enforced invariant: ANY new raw ``text()`` on ``resources``,
in ANY repository file, fails this test until it's routed through
``scoped_sql`` or explicitly allowlisted with a reason.

This test ``ast``-scans every ``.py`` file directly under ``app/repositories/``
for every ``text(...)`` call whose SQL references the ``resources`` table
(``FROM resources`` / ``JOIN resources`` / ``UPDATE resources``) and asserts
each is one of:

  1. SCOPED — its enclosing method calls ``scoped_sql`` (the only sanctioned way
     to express a raw tenant predicate; the helper builds + binds it);
  2. ALLOWLISTED — a method whose ``resources`` access is scoped by folder /
     resource_items / canvas / project MEMBERSHIP (``scope_id`` / folder /
     canvas_id / project_id), NOT by ``creator_id``. Forcing creator scope on
     these would break team-library and shared-folder/canvas semantics, so
     they are deliberate, documented bypasses. Allowlist keys are
     ``"filename.py::method_name"`` (qualified by file, since method names are
     not unique across the whole directory).

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

_REPO_DIR = Path(__file__).resolve().parents[2] / "app" / "repositories"

# Methods whose raw text() on ``resources`` is scoped by folder/resource_items/
# canvas/project MEMBERSHIP (scope_id / folder_id / canvas_id / project_id), not
# by creator_id — forcing creator scope would break team-library + shared-
# folder/canvas semantics. Keys are ``"filename.py::method_name"`` (qualified by
# file — method names collide across a 75-file directory). Each entry carries
# its reason.
#
# Phase C task 3 migrated FOUR former resources_repository.py allowlist entries
# (_resource_ids_for_platforms / validate_scope_image_ids / restore_folder_
# cascade / trash_folder_cascade) off raw text() to real ORM select()/update()
# — they no longer trip this scanner at all. Phase C final review migrated
# canvas_refs_repository.py's two JOIN-resources sites (list_assets_for_canvas
# / tree_for_projects) the same way — found only once the scan widened to the
# whole directory, since they were invisible to both the single-file scanner
# AND a naive FROM/UPDATE-only grep (JOIN shape). Positive pins for all six
# live in test_migrated_methods_no_longer_use_text_on_resources below (so a
# future regression back to raw SQL is caught instead of silently falling
# through with no allowlist entry to widen).
_ALLOWLIST: dict[str, str] = {
    "resources_repository.py::get_resource_items": (
        "JOIN resources via resource_items filtered by i.scope_id (library/folder "
        "membership), not creator_id"
    ),
    "resources_repository.py::get_trashed_resources": (
        "JOIN resources via resource_items filtered by i.scope_id (scope membership), "
        "not creator_id"
    ),
    "resources_repository.py::get_gallery_items": (
        "JOIN resources via gallery_items filtered by gi.gallery_id (gallery "
        "membership); access is gated at the router by check_media_access on the "
        "gallery — creator scoping would break team-library gallery reads"
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


def _repo_files() -> list[Path]:
    """Every ``.py`` file directly under ``app/repositories/`` (not recursive —
    there are no subpackages today; a future one would need this to grow a
    ``rglob``, at which point this comment should be revisited)."""
    return sorted(_REPO_DIR.glob("*.py"))


def _scan() -> list[tuple[str, int, str, str, bool]]:
    """Return ``(filename, lineno, method, full_sql, calls_scoped_sql)`` for
    every ``text(...)`` call, in any file under ``app/repositories/``, whose
    SQL references the ``resources`` table.

    ``full_sql`` includes both the inline ``text(...)`` literal AND any
    ``sql = (...)`` variable assigned in the same method (the f-string/variable
    splice form). ``calls_scoped_sql`` is the authoritative "scoped" signal."""
    hits: list[tuple[str, int, str, str, bool]] = []
    for repo_file in _repo_files():
        tree = ast.parse(repo_file.read_text(encoding="utf-8"), filename=str(repo_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _called_name(node) != "text":
                continue
            arg = node.args[0] if node.args else None
            if arg is None:
                continue
            method_node = _enclosing_method_node(tree, node)
            method = method_node.name if method_node is not None else "<module>"
            # SQL may be inline in text(...) OR built into a variable then passed
            # as text(sql); union both so the variable-splice form is seen.
            readable = _literal_parts(arg)
            if isinstance(arg, ast.Name):
                readable = (readable + " " + _in_function_sql(method_node)).strip()
            if not _references_resources(readable):
                continue
            full_sql = " ".join(readable.split())
            hits.append(
                (
                    repo_file.name,
                    node.lineno,
                    method,
                    full_sql,
                    _method_calls_scoped_sql(method_node),
                )
            )
    return hits


def test_every_resources_text_sql_is_scoped_or_allowlisted():
    """Each raw ``text()`` touching ``resources``, in ANY file under
    ``app/repositories/``, must route through ``scoped_sql`` (its enclosing
    method calls the builder) or be an allowlisted membership-scoped method.
    A new bare one — in this file OR any other repository file — fails
    closed here.

    A method that HAND-WRITES the ``:scope_user_id`` token WITHOUT calling the
    builder is NOT accepted — that is exactly the misplaceable form the builder
    refactor removed (token-in-SELECT-list / tautology / OR 1=1)."""
    assert _REPO_DIR.is_dir(), f"repositories dir not found at {_REPO_DIR}"

    offenders: list[str] = []
    for filename, lineno, method, full_sql, calls_builder in _scan():
        key = f"{filename}::{method}"
        allowlisted = key in _ALLOWLIST
        if calls_builder or allowlisted:
            continue
        why = ""
        if _SCOPE_TOKEN in full_sql:
            why = (
                " (it hand-writes :scope_user_id but does NOT call scoped_sql — a "
                "hand-written predicate can be misplaced; use the builder)"
            )
        offenders.append(
            f"  {filename}:{lineno} in {method}() — {full_sql[:90]!r}{why}"
        )

    assert not offenders, (
        "Raw text() on the `resources` table bypasses the ORM scope choke point "
        "(do_orm_execute is blind to text()) — it gets NO tenant injection and NO "
        "fail-closed raise, an enforcement gap. Route the tenant predicate through "
        "app.db.scope.scoped_sql (a predicate BUILDER: `pred, params = "
        'scoped_sql("r.creator_id", {...})` then `f"... WHERE {pred} ..."`), OR — '
        "if the read is scoped by folder/library/canvas/project membership "
        'rather than creator_id — add "filename.py::method" to the documented '
        "_ALLOWLIST in this test with a reason. Offenders:\n" + "\n".join(offenders)
    )


def _method_node(filename: str, method: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    path = _REPO_DIR / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method:
            return n
    raise AssertionError(f"{method} not found in {filename}")


def test_migrated_methods_no_longer_use_text_on_resources():
    """Positive pin covering every method this guard's history has migrated OFF
    raw ``resources`` text() — Phase C task 2 (2 methods, resources_repository.py,
    fully off text()/scoped_sql()), Phase C task 3 (4 methods, same file, fully
    off text() on resources specifically — two of them keep a legitimate
    ``folders``-table text() out of scope), and the Phase C final review
    (2 methods, canvas_refs_repository.py, fully off text()). Assert none of
    them has a raw text() touching `resources` any more, so a future
    regression back to raw SQL — which would silently need a NEW _ALLOWLIST
    entry that might not get added — is caught here instead of falling
    through with no signal at all."""
    # (filename, method, expected ORM builder call, "resources-only" vs "any text()")
    cases = [
        (
            "resources_repository.py",
            "get_completed_resource_by_url_and_creator",
            "select",
            "any",
        ),
        ("resources_repository.py", "get_owned_platform_ids", "select", "any"),
        (
            "resources_repository.py",
            "_resource_ids_for_platforms",
            "select",
            "resources_only",
        ),
        ("resources_repository.py", "validate_scope_image_ids", "select", "any"),
        (
            "resources_repository.py",
            "restore_folder_cascade",
            "update",
            "resources_only",
        ),
        ("resources_repository.py", "trash_folder_cascade", "update", "resources_only"),
        ("canvas_refs_repository.py", "list_assets_for_canvas", "select", "any"),
        ("canvas_refs_repository.py", "tree_for_projects", "select", "any"),
    ]
    for filename, method, expected_call, mode in cases:
        method_node = _method_node(filename, method)
        called_names = {
            _called_name(n) for n in ast.walk(method_node) if isinstance(n, ast.Call)
        }
        if mode == "any":
            assert "text" not in called_names, (
                f"{filename}::{method} calls text() again — it was migrated to "
                f"pure ORM {expected_call}(); a raw text() here needs scoped_sql() "
                "routing (see the negative guard above), not this positive pin"
            )
            assert _SCOPED_SQL_FN not in called_names, (
                f"{filename}::{method} calls {_SCOPED_SQL_FN}() again — expected "
                f"a pure ORM {expected_call}() now that it no longer runs raw SQL"
            )
        else:
            # resources_only: the method legitimately still calls text() for a
            # DIFFERENT table (folders, no scope mixin) — only the resources-
            # touching statement was migrated. See restore_folder_cascade /
            # trash_folder_cascade docstrings.
            for text_call in ast.walk(method_node):
                if (
                    not isinstance(text_call, ast.Call)
                    or _called_name(text_call) != "text"
                ):
                    continue
                arg = text_call.args[0] if text_call.args else None
                readable = _literal_parts(arg) if arg is not None else ""
                if isinstance(arg, ast.Name):
                    readable = (readable + " " + _in_function_sql(method_node)).strip()
                assert not _references_resources(readable), (
                    f"{filename}::{method} still has a raw text() touching "
                    f"`resources` at line {text_call.lineno} — it was migrated "
                    "to ORM; if it needs raw SQL again it must route through "
                    "scoped_sql() (see the negative guard above) or be "
                    "re-added to _ALLOWLIST with a reason, not silently "
                    "reintroduce a bare text()"
                )
        assert expected_call in called_names, (
            f"{filename}::{method} should build a SQLAlchemy {expected_call}() "
            "statement now — none found"
        )


def test_allowlist_entries_are_real_methods():
    """Every allowlisted ``"filename.py::method"`` key must actually resolve to
    a real file + method (no stale entries silently widening the bypass
    surface, and no typo'd filename that would make an entry a silent no-op)."""
    stale: list[str] = []
    for key in _ALLOWLIST:
        filename, _, method = key.partition("::")
        path = _REPO_DIR / filename
        if not path.is_file():
            stale.append(f"{key} (file does not exist)")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        method_names = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if method not in method_names:
            stale.append(f"{key} (method not found)")
    assert not stale, f"_ALLOWLIST has stale (non-existent) entries: {stale}"


# ── Synthetic planted-offender self-tests (the guard must not be a no-op) ──


def _scan_source(
    src: str, filename: str = "<synthetic>.py"
) -> list[tuple[str, str, bool]]:
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
    route through the builder (and is not allowlisted) — regardless of which
    file it's planted in (this fixture stands in for ANY file under
    app/repositories/, proving the directory-wide scan isn't accidentally
    scoped back down to one file)."""
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
    assert f"some_other_file.py::{method}" not in _ALLOWLIST


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


def test_scan_covers_more_than_one_file():
    """Sanity pin for the widening itself: the scan must walk MULTIPLE files
    under app/repositories/, not just resources_repository.py — otherwise this
    guard silently regresses back to the single-file blind spot the final
    review found (canvas_refs_repository.py)."""
    files_seen = {f.name for f in _repo_files()}
    assert len(files_seen) > 1, "expected multiple .py files under app/repositories/"
    assert "resources_repository.py" in files_seen
    assert "canvas_refs_repository.py" in files_seen
