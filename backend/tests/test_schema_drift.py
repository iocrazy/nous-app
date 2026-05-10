"""Schema-drift regression suite.

P2-9. CLAUDE.md documents three known schema-drift traps:
  - parsed_media has NO transcript_status (it's on `videos`)
  - libraries has NO team_id (use scope_type/scope_id)
  - SELECT *-then-rely on a phantom column generally

These were discovered the hard way (PG 42703 in prod). This module
turns the documented warnings into automated guards: if any backend
.py file references one of the known-bad column accesses, the test
fails so the offender catches it pre-merge.

Pattern: greps the source for ``.eq("phantom_column", ...)`` /
``select("..., phantom_column, ...")`` shapes against a per-table
forbidden list. ALLOWED_PATHS lets us whitelist genuinely-OK uses
(e.g. test fixtures, comments).

Adding a new known-drift trap: extend FORBIDDEN_REFERENCES below.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


# (table_name, forbidden_column, reason). Each entry encodes one
# known schema-drift trap from production observability.
FORBIDDEN_REFERENCES: list[tuple[str, str, str]] = [
    (
        "parsed_media",
        "transcript_status",
        "AI status fields live on the `videos` table, not parsed_media. "
        "See CLAUDE.md → 'parsed_media has no transcript_status'.",
    ),
    (
        "parsed_media",
        "summary_status",
        "Same as transcript_status — these moved to `videos`.",
    ),
    (
        "parsed_media",
        "visual_analysis_status",
        "Same as transcript_status — these moved to `videos`.",
    ),
    (
        "libraries",
        "team_id",
        "libraries uses scope_type/scope_id — there is no team_id column. "
        "See CLAUDE.md → 'libraries 表没有 team_id 列'.",
    ),
]


# Files allowed to mention the forbidden tokens (tests of the lint
# itself, this file, comment-only references in CLAUDE.md notes etc).
ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        # this file is itself the lint — it must contain the forbidden
        # tokens to define the rules
        "tests/test_schema_drift.py",
    }
)


def _check_table_column_combo(content: str, table: str, column: str) -> list[int]:
    """Return line numbers where the forbidden column appears as a
    QUERY STRING LITERAL near a mention of the offending table.

    Heuristic specifics (tuned to catch real bugs, ignore docs):
      - column must appear quoted ("foo" or 'foo') — naked identifier
        mentions in docstrings / variable names don't count
      - table must appear as a string literal too (".table('foo')"
        / 'from "foo"' / etc.)
      - both within a 5-line window (typical multi-line Supabase query)
      - skip lines inside triple-quoted docstrings
    """
    lines = content.splitlines()
    in_docstring = False
    docstring_quote = None
    code_lines: list[bool] = []  # parallel to lines: True if "real code"
    for line in lines:
        stripped = line.lstrip()
        if in_docstring:
            code_lines.append(False)
            if docstring_quote and docstring_quote in line:
                in_docstring = False
                docstring_quote = None
            continue
        # Single-line triple-quoted string (open + close on same line)
        for q in ('"""', "'''"):
            if stripped.startswith(q):
                rest = stripped[len(q) :]
                if q in rest:
                    code_lines.append(False)
                    break
                in_docstring = True
                docstring_quote = q
                code_lines.append(False)
                break
        else:
            if stripped.startswith("#"):
                code_lines.append(False)
            else:
                code_lines.append(True)

    # Quoted-context forms only — column must be in a string literal,
    # but other content can sit between the quote and the column name
    # (e.g. ``"id, transcript_status"`` — common SELECT-list shape).
    # Heuristic: column has a string quote somewhere on the line + the
    # column name appears as a whole word.
    column_pattern = re.compile(rf"""['"][^'"]*\b{re.escape(column)}\b[^'"]*['"]""")
    table_pattern = re.compile(rf"""['"][^'"]*\b{re.escape(table)}\b[^'"]*['"]""")

    table_lines: set[int] = set()
    for i, line in enumerate(lines):
        if not code_lines[i]:
            continue
        if table_pattern.search(line):
            table_lines.add(i)

    offenders: list[int] = []
    for i, line in enumerate(lines):
        if not code_lines[i]:
            continue
        if not column_pattern.search(line):
            continue
        for tline in table_lines:
            if abs(i - tline) <= 4:
                offenders.append(i + 1)  # 1-indexed
                break
    return offenders


def test_no_known_schema_drift_traps() -> None:
    """Fail if any backend .py file makes a select/filter against a
    known-bad (table, column) combination."""
    offenders: list[tuple[str, int, str, str]] = []

    for py_file in BACKEND_APP.rglob("*.py"):
        py_file.relative_to(BACKEND_APP.parent).as_posix()
        # The backend/ prefix isn't in rel_path because we walk from app/
        rel_path_full = str(py_file.relative_to(BACKEND_APP.parent))
        if rel_path_full in ALLOWED_PATHS:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for table, column, reason in FORBIDDEN_REFERENCES:
            offending_lines = _check_table_column_combo(content, table, column)
            for lineno in offending_lines:
                offenders.append((rel_path_full, lineno, f"{table}.{column}", reason))

    if offenders:
        msg_lines = [
            f"  {path}:{lineno}  →  references {combo}\n      {reason}"
            for path, lineno, combo, reason in offenders
        ]
        raise AssertionError(
            "Known schema-drift traps detected (see CLAUDE.md):\n"
            + "\n".join(msg_lines)
            + "\n\nIf this is a false positive, add the file to ALLOWED_PATHS "
            "in tests/test_schema_drift.py with a justification."
        )


def test_lint_catches_real_query_pattern() -> None:
    """Synthetic example: confirm the heuristic actually flags the
    dangerous shape (so a regression of the heuristic itself surfaces)."""
    bad_code = """
def fetch_status(client):
    return (
        client.table("parsed_media")
        .select("id, transcript_status")
        .execute()
    )
    """
    offenders = _check_table_column_combo(bad_code, "parsed_media", "transcript_status")
    assert offenders, "lint should flag dangerous pattern"


def test_lint_ignores_docstring_mention() -> None:
    """Synthetic example: a docstring describing the trap must NOT
    trigger the lint."""
    docs_only = '''
def safe(x):
    """parsed_media has no transcript_status — use videos table."""
    return x
    '''
    offenders = _check_table_column_combo(
        docs_only, "parsed_media", "transcript_status"
    )
    assert not offenders, "docstring mention should NOT be flagged"


def test_forbidden_references_are_documented() -> None:
    """Sanity: every entry in FORBIDDEN_REFERENCES has a non-empty reason
    so the failure message can guide the fix. Catches future entries
    added without context."""
    for table, column, reason in FORBIDDEN_REFERENCES:
        assert table, "table name must be non-empty"
        assert column, "column name must be non-empty"
        assert len(reason) > 30, (
            f"reason for {table}.{column} too short — give the next "
            "developer enough context to understand the trap"
        )
