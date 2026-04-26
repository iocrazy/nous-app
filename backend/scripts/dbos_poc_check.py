"""DBOS PoC compatibility check (eng-review 2026-04-27, 12 items).

Run: uv run python scripts/dbos_poc_check.py
Pass criteria: every item prints PASS. Any FAIL -> stop and decide fallback.
"""
from __future__ import annotations

import os
import sys
import uuid

DB_URL = os.environ.get(
    "DBOS_POC_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    fails: list[str] = []

    section("PoC #1: dbos importable")
    try:
        import dbos as dbos_module

        print(f"PASS dbos {dbos_module.__version__ if hasattr(dbos_module, '__version__') else 'unknown'}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL import: {e!r}")
        fails.append("#1 import")
        return _summary(fails)

    section("PoC #2/#4: DBOS launches, isolated schema, no auth/realtime pollution")
    import psycopg

    pre_schemas = _list_schemas(DB_URL)
    pre_pub = _list_realtime_pub(DB_URL)

    from dbos import DBOS, DBOSConfig

    cfg: DBOSConfig = {"name": "mediahub-poc", "database_url": DB_URL}
    dbos = DBOS(config=cfg)
    try:
        DBOS.launch()
        post_schemas = _list_schemas(DB_URL)
        post_pub = _list_realtime_pub(DB_URL)

        new = sorted(set(post_schemas) - set(pre_schemas))
        if new == ["dbos"]:
            print(f"PASS dbos schema isolated; new schemas: {new}")
        elif "dbos" in new and len(new) == 1:
            print(f"PASS only dbos schema added: {new}")
        else:
            print(f"FAIL unexpected schema delta: {new}")
            fails.append("#2 schema isolation")

        if post_pub == pre_pub:
            print("PASS supabase_realtime publication unchanged")
        else:
            added = sorted(set(post_pub) - set(pre_pub))
            print(f"FAIL realtime publication grew: +{added}")
            fails.append("#4 realtime publication")

        section("PoC #5: launch did not require superuser")
        with psycopg.connect(DB_URL) as conn:
            cur = conn.execute("SELECT current_user, session_user, rolsuper FROM pg_roles WHERE rolname=current_user;")
            row = cur.fetchone()
            print(f"current_user={row[0]} session_user={row[1]} rolsuper={row[2]}")
            if row and row[2] is True:
                print("WARN connected as superuser — re-run with non-superuser to validate NAS scenario")
                fails.append("#5 ran as superuser (re-run with restricted role)")
            else:
                print("PASS DBOS launched without superuser")

        section("PoC #6: workflow accepts a call")

        @DBOS.workflow()
        def hello_workflow(name: str) -> str:
            return f"hello {name}"

        out = hello_workflow("poc")
        if out == "hello poc":
            print(f"PASS workflow returned: {out}")
        else:
            print(f"FAIL workflow returned: {out!r}")
            fails.append("#6 workflow call")

    finally:
        try:
            DBOS.destroy()
        except Exception as e:  # noqa: BLE001
            print(f"note: DBOS.destroy raised {e!r}")

    section("PoC #3: PG version >= 14 and required extensions present")
    with psycopg.connect(DB_URL) as conn:
        ver = conn.execute("SELECT version();").fetchone()[0]
        print(f"version: {ver}")
        major = _pg_major(ver)
        if major >= 14:
            print(f"PASS PG major={major}")
        else:
            print(f"FAIL PG major={major} < 14")
            fails.append("#3 PG version")
        rows = conn.execute(
            "SELECT name, installed_version FROM pg_available_extensions "
            "WHERE name IN ('pgcrypto','uuid-ossp','pg_trgm') ORDER BY name;"
        ).fetchall()
        names = {r[0]: r[1] for r in rows}
        missing = [n for n in ("pgcrypto", "uuid-ossp", "pg_trgm") if not names.get(n)]
        if not missing:
            print(f"PASS extensions installed: {names}")
        else:
            print(f"FAIL extensions missing/uninstalled: {missing} (available rows={names})")
            fails.append("#3 extensions")

    section("Summary")
    return _summary(fails)


def _list_schemas(url: str) -> list[str]:
    import psycopg

    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema' "
            "ORDER BY schema_name;"
        ).fetchall()
    return [r[0] for r in rows]


def _list_realtime_pub(url: str) -> list[str]:
    import psycopg

    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT schemaname || '.' || tablename FROM pg_publication_tables "
            "WHERE pubname = 'supabase_realtime' ORDER BY 1;"
        ).fetchall()
    return [r[0] for r in rows]


def _pg_major(version_str: str) -> int:
    import re

    m = re.search(r"PostgreSQL (\d+)", version_str)
    return int(m.group(1)) if m else 0


def _summary(fails: list[str]) -> int:
    if fails:
        print("FAIL items:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS IN THIS SUBSET PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
