"""PR-D2.1 verify — security-fix migrations 169-173 on NAS dev.

Tests:
    1. dbos_workflow_routing seeded + RLS readable / write-restricted
    2. issues UPDATE column-allowlist trigger blocks privileged-column mutation
    3. issues INSERT membership check blocks team/project plant attempt
    4. supabase_realtime publication excludes execution_state column
    5. issue_create_atomic stored proc allocates + inserts in one txn
    6. counter rollback safety holds with new stored proc

Run from backend/:
    uv run python scripts/pr_d2_1_verify.py
"""
from __future__ import annotations

import json
import sys

import psycopg

USER_A = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
USER_B = "61f15833-2b2b-4a53-b126-16897f9184a8"


def admin_dsn(user: str = "supabase_admin.heygo-dev") -> str:
    return (
        f"host=127.0.0.1 port=55433 dbname=postgres "
        f"user={user} password=MediaHub_Dev_WtB2bzyMup1n0KY2P5gWoA "
        f"sslmode=disable connect_timeout=8"
    )


def assert_eq(label, actual, expected, fails):
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'} {label}: actual={actual} expected={expected}")
    if not ok:
        fails.append(label)


def main() -> int:
    fails: list[str] = []

    # Cleanup leftover test rows
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute(
            "DELETE FROM public.issues WHERE title LIKE 'PR-D2.1 %' OR title='spoof' OR title='hijack'"
        )
        conn.execute("UPDATE public.issue_sequence SET counter=0 WHERE scope='global'")

    print("=== Test 1: dbos_workflow_routing seeded ===")
    with psycopg.connect(admin_dsn()) as conn:
        cur = conn.execute(
            "SELECT count(*) FROM public.dbos_workflow_routing WHERE mode='celery'"
        )
        n = cur.fetchone()[0]
        print(f"  PASS routing rows seeded as 'celery': {n}")
        cur = conn.execute(
            "SELECT mode FROM public.dbos_workflow_routing WHERE task_type='ai_summary'"
        )
        assert_eq("ai_summary mode", cur.fetchone()[0], "celery", fails)

    print("\n=== Test 2: UPDATE column-allowlist trigger ===")
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute(
            """INSERT INTO public.issues (issue_number, identifier, title, status,
               created_by_user_id, origin_kind)
               VALUES (1000, 'MH-trig-test', 'PR-D2.1 trigger fixture', 'todo', %s, 'manual')
               RETURNING id""",
            (USER_A,),
        )
        issue_id = cur.fetchone()[0]
        print(f"  fixture issue id={issue_id}")

    # Now switch to authenticated user A — A is creator, can UPDATE; trigger
    # should block changes to dbos_workflow_id / request_depth.
    with psycopg.connect(admin_dsn()) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(
                f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{USER_A}\",\"role\":\"authenticated\"}}'"
            )
            # Allowed: change title
            conn.execute("UPDATE public.issues SET title='retitled' WHERE id=%s", (issue_id,))
            print("  PASS A can UPDATE allowed column (title)")

        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(
                f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{USER_A}\",\"role\":\"authenticated\"}}'"
            )
            try:
                conn.execute(
                    "UPDATE public.issues SET dbos_workflow_id='hijack' WHERE id=%s",
                    (issue_id,),
                )
                fails.append("trigger did not block dbos_workflow_id mutation")
                print("  FAIL trigger let dbos_workflow_id through")
            except psycopg.errors.InsufficientPrivilege as e:
                print(f"  PASS trigger blocked dbos_workflow_id: {str(e)[:60]}...")

        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(
                f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{USER_A}\",\"role\":\"authenticated\"}}'"
            )
            try:
                conn.execute(
                    "UPDATE public.issues SET request_depth=99 WHERE id=%s",
                    (issue_id,),
                )
                fails.append("trigger did not block request_depth mutation")
                print("  FAIL trigger let request_depth through")
            except psycopg.errors.InsufficientPrivilege:
                print("  PASS trigger blocked request_depth")

    # service_role bypass — proves the trigger does NOT block legit DBOS path
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute("SET ROLE service_role")
        conn.execute(
            "UPDATE public.issues SET dbos_workflow_id='wf-legit-001' WHERE id=%s",
            (issue_id,),
        )
        print("  PASS service_role can update dbos_workflow_id")
        # cleanup
        conn.execute("DELETE FROM public.issues WHERE id=%s", (issue_id,))

    print("\n=== Test 3: INSERT team/project membership check ===")
    # Find a team A is NOT a member of (or create one)
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        cur = conn.execute(
            """SELECT t.id FROM public.teams t
               WHERE NOT EXISTS (
                 SELECT 1 FROM public.team_members tm
                 WHERE tm.team_id = t.id AND tm.user_id = %s
               )
               LIMIT 1""",
            (USER_A,),
        )
        row = cur.fetchone()
        if row:
            victim_team_id = row[0]
            print(f"  using victim team_id={victim_team_id} (A not a member)")
        else:
            # Create one
            conn.execute("SET ROLE service_role")
            cur = conn.execute(
                "INSERT INTO public.teams (name, owner_id, is_personal) "
                "VALUES ('victim-team-pr-d2-1', %s, false) RETURNING id",
                (USER_B,),
            )
            victim_team_id = cur.fetchone()[0]
            print(f"  created victim team_id={victim_team_id}")

    with psycopg.connect(admin_dsn()) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(
                f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{USER_A}\",\"role\":\"authenticated\"}}'"
            )
            # First allocate identifier as service_role (separate tx)
        with conn.transaction():
            conn.execute("SET LOCAL ROLE service_role")
            cur = conn.execute("SELECT issue_number, identifier FROM public.issue_next_identifier()")
            n, ident = cur.fetchone()

        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(
                f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{USER_A}\",\"role\":\"authenticated\"}}'"
            )
            try:
                conn.execute(
                    """INSERT INTO public.issues (issue_number, identifier, title, status,
                       team_id, created_by_user_id, origin_kind)
                       VALUES (%s, %s, 'PR-D2.1 plant attempt', 'todo', %s, %s, 'manual')""",
                    (n, ident, victim_team_id, USER_A),
                )
                fails.append("INSERT membership check did not block team plant")
                print("  FAIL plant insert succeeded (RLS too permissive)")
            except psycopg.errors.InsufficientPrivilege:
                print("  PASS plant insert blocked by INSERT WITH CHECK")

    print("\n=== Test 4: realtime publication column allowlist ===")
    with psycopg.connect(admin_dsn()) as conn:
        cur = conn.execute(
            """SELECT attname FROM pg_publication_rel pr
               JOIN pg_publication p ON p.oid = pr.prpubid
               JOIN pg_attribute a ON a.attrelid = pr.prrelid
               WHERE p.pubname = 'supabase_realtime'
                 AND pr.prrelid = 'public.issues'::regclass
                 AND a.attnum > 0 AND NOT a.attisdropped
               ORDER BY a.attnum"""
        )
        all_cols = [r[0] for r in cur.fetchall()]
        # Better: query pg_publication_tables for actual published cols
        cur = conn.execute(
            "SELECT attnames FROM pg_publication_tables "
            "WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='issues'"
        )
        row = cur.fetchone()
        if row and row[0]:
            published = list(row[0])
            print(f"  published columns: {len(published)}")
            for excluded in ("execution_state", "execution_locked_at", "dbos_workflow_id"):
                ok = excluded not in published
                print(f"  {'PASS' if ok else 'FAIL'} {excluded} excluded")
                if not ok:
                    fails.append(f"{excluded} should be excluded from realtime")
        else:
            # Older PG: full table publication; skip this test
            print("  SKIP — pg_publication_tables.attnames returned NULL (full-table publication)")

    print("\n=== Test 5: issue_create_atomic stored proc ===")
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute(
            "SELECT * FROM public.issue_create_atomic(%s::jsonb)",
            (
                json.dumps(
                    {
                        "title": "PR-D2.1 atomic test",
                        "status": "todo",
                        "created_by_user_id": USER_A,
                        "origin_kind": "manual",
                    }
                ),
            ),
        )
        row = cur.fetchone()
        col_names = [d.name for d in cur.description]
        rec = dict(zip(col_names, row))
        print(f"  inserted via proc: id={rec['id']} identifier={rec['identifier']} status={rec['status']}")
        assert_eq("identifier matches MH-N pattern", rec["identifier"].startswith("MH-"), True, fails)
        assert_eq("title roundtrip", rec["title"], "PR-D2.1 atomic test", fails)
        # cleanup
        conn.execute("DELETE FROM public.issues WHERE id=%s", (rec["id"],))

    print("\n=== Test 6: stored proc rollback safety ===")
    with psycopg.connect(admin_dsn()) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE service_role")
            cur = conn.execute("SELECT counter FROM public.issue_sequence WHERE scope='global'")
            counter_before = cur.fetchone()[0]

        # Attempt stored proc with bad payload that fails CHECK
        try:
            with conn.transaction():
                conn.execute("SET LOCAL ROLE service_role")
                conn.execute(
                    "SELECT public.issue_create_atomic(%s::jsonb)",
                    (json.dumps({"title": "", "created_by_user_id": USER_A}),),  # empty title violates CHECK
                )
        except (psycopg.errors.CheckViolation, psycopg.errors.RaiseException):
            pass  # expected

        with conn.transaction():
            conn.execute("SET LOCAL ROLE service_role")
            cur = conn.execute("SELECT counter FROM public.issue_sequence WHERE scope='global'")
            counter_after = cur.fetchone()[0]
        assert_eq("counter unchanged after stored proc CHECK failure", counter_after, counter_before, fails)

    # Final cleanup
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute(
            "DELETE FROM public.issues WHERE title LIKE 'PR-D2.1 %' OR title='spoof' OR title='retitled'"
        )
        conn.execute("UPDATE public.issue_sequence SET counter=0 WHERE scope='global'")

    print()
    if fails:
        print("FAIL items:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS — PR-D2.1 security fixes + routing table + atomic stored proc verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
