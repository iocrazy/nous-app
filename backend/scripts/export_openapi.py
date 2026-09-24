#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema to ``backend/openapi.json`` offline.

The file is committed: it is the diffable contract snapshot that the frontend
type generator (``npm run gen:api``) reads. CI re-runs this script and fails
if the committed file is stale (see ``.github/workflows/ci.yml``).

Usage:
    cd backend && uv run python scripts/export_openapi.py            # write
    cd backend && uv run python scripts/export_openapi.py --check    # diff only

No database is touched: importing ``app.main`` builds the app and registers
routes, and ``app.openapi()`` only walks those routes. The lifespan (DB, DBOS)
never runs because no server is started.

Determinism: the route surface depends on the process role (a ``worker`` role
mounts only the probes), so the role is pinned before import. Output is
key-sorted with a trailing newline so two runs are byte-identical.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

import argparse
import json
import os
import sys
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = BACKEND_DIR / "openapi.json"

# Env keys that change the exported surface. The role picks which routers are
# mounted; everything else is read from the committed config.yml.
_ROLE_KEYS = ("NOUS_ROLE", "MEDIAHUB_ROLE")


class DuplicateOperationIdError(RuntimeError):
    """Two operations share an operationId; generated client keys would collide."""


class SchemaNameCollisionError(RuntimeError):
    """Two Pydantic models share a class name.

    FastAPI then gives one the short name and the other a module-qualified
    name (``app__schemas__x__Foo``), and which one wins depends on set
    iteration order — it changes from process to process, so the export is
    not reproducible and the CI diff gate would flap. Rename one of them.
    """


@contextmanager
def _pinned_role() -> Iterator[None]:
    """Pin the process role for the import, restoring the caller's env after."""
    saved = {key: os.environ.get(key) for key in _ROLE_KEYS}
    for key in _ROLE_KEYS:
        os.environ.pop(key, None)
    os.environ["NOUS_ROLE"] = "combined"
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_schema() -> dict[str, Any]:
    """Import the app and return its OpenAPI schema; raise on duplicate ids."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    with _pinned_role(), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from app.main import app

        app.openapi_schema = None
        schema = app.openapi()
    duplicates = sorted(
        {str(w.message) for w in caught if "Duplicate Operation ID" in str(w.message)}
    )
    if duplicates:
        raise DuplicateOperationIdError("\n".join(duplicates))
    collisions = find_name_collisions(schema)
    if collisions:
        raise SchemaNameCollisionError(", ".join(collisions))
    return schema


def find_name_collisions(schema: dict[str, Any]) -> list[str]:
    """Return module-qualified schema keys, the mark of a class-name collision."""
    names = schema.get("components", {}).get("schemas", {})
    return sorted(name for name in names if name.startswith("app__"))


def render(schema: dict[str, Any]) -> str:
    """Serialize deterministically: sorted keys, 2-space indent, final newline."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the output file differs from a fresh export.",
    )
    args = parser.parse_args(argv)

    try:
        text = render(build_schema())
    except DuplicateOperationIdError as exc:
        print(f"Duplicate OpenAPI operationIds:\n{exc}", file=sys.stderr)
        return 2
    except SchemaNameCollisionError as exc:
        print(
            f"Pydantic models share a class name (export is not reproducible "
            f"until one is renamed): {exc}",
            file=sys.stderr,
        )
        return 2

    if args.check:
        current = args.output.read_text() if args.output.exists() else ""
        if current != text:
            print(
                f"{args.output} is stale: run `uv run python scripts/export_openapi.py`",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is up to date")
        return 0

    args.output.write_text(text)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
