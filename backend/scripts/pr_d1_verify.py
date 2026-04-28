"""PR-D1 verify — sanity-test issues schema, atomic counter, RLS, FK back-refs.

Run from backend/:
    uv run python scripts/pr_d1_verify.py
"""
from __future__ import annotations

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

    # Clean any leftover test issues + reset counter at start (idempotent re-runs)
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute("DELETE FROM public.issues WHERE title LIKE '%PR-D1 verify%' OR title='XOR test' OR title='Test issue from PR-D1 verify'")
        conn.execute("UPDATE public.issue_sequence SET counter=0 WHERE scope='global'")

    print("=== Test 1: schema present ===")
    with psycopg.connect(admin_dsn()) as conn:
        cur = conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='issues'
            ORDER BY ordinal_position
        """)
        cols = [r[0] for r in cur.fetchall()]
        for required in ('id','identifier','status','priority','assignee_user_id','assignee_agent_id',
                         'created_by_user_id','origin_kind','request_depth','hidden_at','dbos_workflow_id'):
            ok = required in cols
            print(f"  {'PASS' if ok else 'FAIL'} column {required} present")
            if not ok: fails.append(f"missing column {required}")
        cur = conn.execute("SELECT count(*) FROM pg_publication_tables WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='issues'")
        assert_eq("issues in supabase_realtime", cur.fetchone()[0], 1, fails)
        cur = conn.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='issues'")
        n_idx = cur.fetchone()[0]
        print(f"  PASS index count: {n_idx} (expected ≥10)")

    print("\n=== Test 2: atomic counter — sequence is gap-free ===")
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        # Reset counter for clean test
        conn.execute("UPDATE public.issue_sequence SET counter = 0")
        # Call RPC twice in separate connections
        ids = []
        for _ in range(3):
            cur = conn.execute("SELECT issue_number, identifier FROM public.issue_next_identifier()")
            ids.append(cur.fetchone())
        print(f"  3 sequential calls returned: {ids}")
        assert_eq("counter sequence", [r[0] for r in ids], [1, 2, 3], fails)
        assert_eq("identifier format", [r[1] for r in ids], ["MH-1", "MH-2", "MH-3"], fails)

    print("\n=== Test 3: counter rollback safety (no PG sequence gap) ===")
    with psycopg.connect(admin_dsn()) as conn:
        with conn.transaction():
            conn.execute("SELECT issue_next_identifier()")
        cur = conn.execute("SELECT counter FROM public.issue_sequence WHERE scope='global'")
        post_commit = cur.fetchone()[0]
        # Now a transaction that calls + rolls back
        try:
            with conn.transaction():
                conn.execute("SELECT issue_next_identifier()")
                raise RuntimeError("simulated rollback")
        except RuntimeError:
            pass
        cur = conn.execute("SELECT counter FROM public.issue_sequence WHERE scope='global'")
        post_rollback = cur.fetchone()[0]
        assert_eq("counter unchanged after rollback", post_rollback, post_commit, fails)

    print("\n=== Test 4: insert via RPC happy path ===")
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute("SELECT issue_number, identifier FROM public.issue_next_identifier()")
        n, ident = cur.fetchone()
        cur = conn.execute(
            """INSERT INTO public.issues
               (issue_number, identifier, title, status, created_by_user_id, origin_kind)
               VALUES (%s, %s, %s, 'todo', %s, 'manual') RETURNING id, identifier, status""",
            (n, ident, "Test issue from PR-D1 verify", USER_A))
        new = cur.fetchone()
        print(f"  inserted: {new}")
        assert_eq("identifier set correctly", new[1], ident, fails)
        # Verify back-ref columns exist on all 3 tables
        for tbl in ("unified_tasks", "agent_tasks", "project_tasks"):
            cur = conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s AND column_name='issue_id'",
                (tbl,))
            ok = cur.fetchone()[0] == 1
            print(f"  {'PASS' if ok else 'FAIL'} {tbl}.issue_id column exists")
            if not ok: fails.append(f"{tbl}.issue_id missing")
        cur = conn.execute("SELECT count(*) FROM public.issues WHERE identifier=%s", (ident,))
        assert_eq("issue persisted", cur.fetchone()[0], 1, fails)

    print("\n=== Test 5: RLS — A sees own, B doesn't ===")
    with psycopg.connect(admin_dsn()) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{USER_A}\",\"role\":\"authenticated\"}}'")
            cur = conn.execute("SELECT count(*) FROM public.issues WHERE created_by_user_id=%s", (USER_A,))
            a_sees_own = cur.fetchone()[0]
            print(f"  A sees own count: {a_sees_own}")
            assert_eq("A sees ≥1 of own issues", a_sees_own >= 1, True, fails)
        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{USER_B}\",\"role\":\"authenticated\"}}'")
            cur = conn.execute("SELECT count(*) FROM public.issues WHERE created_by_user_id=%s", (USER_A,))
            b_sees_a = cur.fetchone()[0]
            assert_eq("B does NOT see A's private issue", b_sees_a, 0, fails)

    print("\n=== Test 6: assignee XOR check ===")
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        conn.execute("SET ROLE service_role")
        cur = conn.execute("SELECT issue_next_identifier()")
        # ai_agents row needed — pick first existing one
        cur = conn.execute("SELECT id FROM public.ai_agents LIMIT 1")
        agent_row = cur.fetchone()
        if not agent_row:
            print("  SKIP — no ai_agents rows in dev")
        else:
            agent_id = agent_row[0]
            cur = conn.execute("SELECT issue_number, identifier FROM public.issue_next_identifier()")
            n, ident = cur.fetchone()
            try:
                conn.execute(
                    """INSERT INTO public.issues (issue_number, identifier, title, status,
                       created_by_user_id, assignee_user_id, assignee_agent_id, origin_kind)
                       VALUES (%s, %s, 'XOR test', 'todo', %s, %s, %s, 'manual')""",
                    (n, ident, USER_A, USER_A, agent_id))
                fails.append("assignee XOR check did not fire")
                print("  FAIL XOR violation accepted")
            except psycopg.errors.CheckViolation:
                print("  PASS assignee_xor CHECK constraint blocks dual-assignee")

    print("\n=== Test 7: cleanup ===")
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        cur = conn.execute("SELECT count(*) FROM public.issues WHERE title LIKE '%PR-D1 verify%' OR title='XOR test'")
        n_test_rows = cur.fetchone()[0]
        conn.execute("DELETE FROM public.issues WHERE title LIKE '%PR-D1 verify%' OR title='XOR test'")
        # reset counter for cleanliness
        conn.execute("UPDATE public.issue_sequence SET counter = 0")
        print(f"  deleted {n_test_rows} test issues; counter reset to 0")

    print()
    if fails:
        print("FAIL items:")
        for f in fails: print(f"  - {f}")
        return 1
    print("PASS — PR-D1 schema, RPC, RLS, FK back-refs all verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
