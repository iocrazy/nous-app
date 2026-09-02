"""``CanvasKind`` must hold exactly what ``canvases_kind_check`` allows.

Two failure directions, both silent, both already suffered in this repo:

* A value in the CHECK but NOT in the Literal is a kind the API can never
  name. ``'costume'`` sat like that from mig 446 until P4 ruling G: the DB
  accepted it, `canvasKindFor` downgraded costume boards to ``'smart'``, and
  no error was raised anywhere along the way.
* A value in the Literal but NOT in the CHECK is a 23514 CheckViolation at
  INSERT. Migration 363's header names this exact class ("the Python enum
  learned a value, the DB CHECK didn't") after 362 had to repair it once.

The CHECK is the authority and it is read from the migration, so this test
cannot be satisfied by editing a second hand-typed list. It compares SETS:
``canvases_kind_check`` is rewritten by DROP + ADD (280, 357, 358, 362, 421,
446 …) and each rewrite restates the whole thing, so its real failure mode is
a value quietly going missing — which a membership assertion cannot see.

Runs without a database on purpose; ``tests/migrations/
test_445_asset_library_core.py`` checks the same constraint against a real
Postgres, and that one skips when ``INTEGRATION_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from app.schemas.canvas import CanvasKind, CreatableCanvasKind

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"

# Kinds a client may never POST. 'classic' is the retired canvas 1.0 engine
# (mig 361 soft-deleted every row); 'storyboard' is system-created only, via
# the dedicated get-or-create route. Both stay in CanvasKind so existing rows
# still serialize — see the schema module's header.
NOT_CREATABLE = {"classic", "storyboard"}


def _kinds_from_migrations() -> set[str]:
    """The literals the LATEST rewrite of ``canvases_kind_check`` allows."""
    pattern = re.compile(
        r"canvases_kind_check.*?CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)",
        re.IGNORECASE | re.DOTALL,
    )
    latest: str | None = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        found = pattern.findall(path.read_text(encoding="utf-8"))
        if found:
            latest = found[-1]
    if latest is None:
        pytest.fail("no migration defines canvases_kind_check")
    return set(re.findall(r"'([^']+)'", latest))


def test_the_parser_found_something() -> None:
    """A silent empty set would make every assertion below vacuously true."""
    assert len(_kinds_from_migrations()) > 5


def test_canvas_kind_matches_the_db_check() -> None:
    assert set(get_args(CanvasKind)) == _kinds_from_migrations()


def test_costume_is_in_both() -> None:
    assert "costume" in get_args(CanvasKind)
    assert "costume" in _kinds_from_migrations()


def test_creatable_is_canvas_kind_minus_the_two_system_kinds() -> None:
    assert (
        set(get_args(CreatableCanvasKind)) == set(get_args(CanvasKind)) - NOT_CREATABLE
    )


def test_costume_is_creatable() -> None:
    """The asset sheet's "Open In Canvas" POSTs this kind for a costume."""
    assert "costume" in get_args(CreatableCanvasKind)
