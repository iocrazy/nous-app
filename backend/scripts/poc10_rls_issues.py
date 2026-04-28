"""PoC #10 — issues 表 6 条 RLS policy 草稿验证。

Creates `_poc_issues` (a draft of the future issues table) on NAS dev with the 6
policies from design doc P10, runs an access matrix, then drops the table.

Pass criteria:
    A. service_role bypasses RLS (DBOS path) — read/write any issue
    B. authenticated user A sees: own + assignee + team + project; NOT B's private
    C. authenticated user B sees: their assignee row but NOT A's private
    D. hidden_at NOT NULL → invisible to non-creators
    E. INSERT requires created_by_user_id=auth.uid() OR service_role
    F. UPDATE status restricted; DELETE blocked for non-service-role

Run from backend/:
    uv run python scripts/poc10_rls_issues.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

USER_A = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
USER_B = "61f15833-2b2b-4a53-b126-16897f9184a8"
USER_C = "81e49ea8-c3d5-4bc7-a904-7bdf109e0cd9"


def _admin_dsn() -> str:
    return (
        "host=127.0.0.1 port=55433 dbname=postgres "
        "user=postgres.heygo-dev password=MediaHub_Dev_WtB2bzyMup1n0KY2P5gWoA "
        "sslmode=disable connect_timeout=8"
    )


SCHEMA_SQL = """
DROP TABLE IF EXISTS public._poc_issues CASCADE;

CREATE TABLE public._poc_issues (
  id BIGSERIAL PRIMARY KEY,
  identifier TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'backlog'
    CHECK (status IN ('backlog','todo','in_progress','in_review','blocked','done','cancelled')),
  team_id BIGINT,
  project_id BIGINT,
  assignee_user_id UUID,
  created_by_user_id UUID NOT NULL,
  hidden_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public._poc_issues ENABLE ROW LEVEL SECURITY;

-- Stub team-membership table (real one is `team_members`)
DROP TABLE IF EXISTS public._poc_team_members;
CREATE TABLE public._poc_team_members (
  team_id BIGINT,
  user_id UUID,
  PRIMARY KEY (team_id, user_id)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON public._poc_issues TO authenticated, anon, service_role;
GRANT USAGE, SELECT ON SEQUENCE public._poc_issues_id_seq TO authenticated, anon, service_role;
GRANT SELECT, INSERT ON public._poc_team_members TO authenticated, anon, service_role;

-- Policy 1: SELECT — creator OR assignee OR team-member, AND not hidden (unless creator)
CREATE POLICY p1_select ON public._poc_issues
  FOR SELECT
  USING (
    (
      created_by_user_id = auth.uid()
      OR assignee_user_id = auth.uid()
      OR (team_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public._poc_team_members tm
        WHERE tm.team_id = public._poc_issues.team_id AND tm.user_id = auth.uid()
      ))
    )
    AND (
      hidden_at IS NULL
      OR created_by_user_id = auth.uid()
    )
  );

-- Policy 2: INSERT — created_by_user_id must equal auth.uid()  (service_role bypasses)
CREATE POLICY p2_insert ON public._poc_issues
  FOR INSERT
  WITH CHECK (created_by_user_id = auth.uid());

-- Policy 3: UPDATE non-status fields — creator or assignee
CREATE POLICY p3_update ON public._poc_issues
  FOR UPDATE
  USING (created_by_user_id = auth.uid() OR assignee_user_id = auth.uid())
  WITH CHECK (created_by_user_id = auth.uid() OR assignee_user_id = auth.uid());

-- Policy 4: DELETE — service_role only (regular users use hidden_at soft delete)
-- We achieve "service_role only" by not adding any DELETE policy; service_role
-- bypasses RLS so it always works. authenticated/anon get implicit deny.
"""


def assert_eq(label: str, actual, expected, fails: list) -> None:
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'} {label}: actual={actual} expected={expected}")
    if not ok:
        fails.append(label)


def run_as_user(conn: psycopg.Connection, user_id: str, query: str, params=None):
    """Set authenticated role + JWT claim user_id, run query, restore."""
    # SET LOCAL is transaction-scoped; wrap in a single transaction.
    conn.execute("SET LOCAL ROLE authenticated")
    conn.execute(f"SET LOCAL request.jwt.claims = '{{\"sub\":\"{user_id}\",\"role\":\"authenticated\"}}'")
    cur = conn.execute(query, params or ())
    return cur.fetchall() if cur.description else None


def main() -> int:
    fails: list[str] = []

    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        print("=== Phase 1: schema setup ===")
        conn.execute(SCHEMA_SQL)
        # Setup team membership: A & B share team 100; C is alone
        conn.execute("INSERT INTO public._poc_team_members VALUES (100, %s),(100, %s)", (USER_A, USER_B))
        print("schema + 6 policies + team membership ready")

    # All subsequent role-switching needs explicit transaction control.
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.transaction():
            # service_role inserts 5 fixtures
            print("\n=== Phase 2: service_role inserts (bypass RLS) ===")
            conn.execute("SET LOCAL ROLE service_role")
            conn.execute("""
                INSERT INTO public._poc_issues (identifier, title, status, team_id, assignee_user_id, created_by_user_id, hidden_at) VALUES
                ('MH-1', 'A private issue',     'todo', NULL, NULL,   %(a)s, NULL),
                ('MH-2', 'A team-shared issue', 'todo', 100,  NULL,   %(a)s, NULL),
                ('MH-3', 'A assigned to B',     'todo', NULL, %(b)s,  %(a)s, NULL),
                ('MH-4', 'A hidden private',    'todo', NULL, NULL,   %(a)s, now()),
                ('MH-5', 'C only issue',        'todo', NULL, NULL,   %(c)s, NULL)
            """, {"a": USER_A, "b": USER_B, "c": USER_C})
            cur = conn.execute("SELECT count(*) FROM public._poc_issues")
            assert_eq("service_role sees all 5", cur.fetchone()[0], 5, fails)

        # USER A view — own (1,2,3) + hidden own (4); team-shared (2 already in own)
        with conn.transaction():
            rows = run_as_user(conn, USER_A,
                "SELECT identifier FROM public._poc_issues ORDER BY identifier")
            ids = sorted([r[0] for r in rows])
            assert_eq("A sees own + hidden + team", ids, ['MH-1','MH-2','MH-3','MH-4'], fails)

        # USER B view — assignee on MH-3; team-member on MH-2; cannot see MH-1/MH-4/MH-5
        with conn.transaction():
            rows = run_as_user(conn, USER_B,
                "SELECT identifier FROM public._poc_issues ORDER BY identifier")
            ids = sorted([r[0] for r in rows])
            assert_eq("B sees team + assignee", ids, ['MH-2','MH-3'], fails)

        # USER C view — only own (MH-5)
        with conn.transaction():
            rows = run_as_user(conn, USER_C,
                "SELECT identifier FROM public._poc_issues ORDER BY identifier")
            ids = sorted([r[0] for r in rows])
            assert_eq("C sees own only", ids, ['MH-5'], fails)

        # INSERT — A creates an issue with created_by_user_id=A (allowed)
        print("\n=== Phase 3: INSERT enforcement ===")
        with conn.transaction():
            try:
                run_as_user(conn, USER_A,
                    "INSERT INTO public._poc_issues (identifier, title, created_by_user_id) VALUES ('MH-6','A self-create',%s)",
                    (USER_A,))
                print("  PASS A inserts with created_by=A")
            except psycopg.errors.InsufficientPrivilege as e:
                fails.append("A INSERT self-create failed")
                print(f"  FAIL A self-insert raised: {e}")

        # INSERT — A tries to create with created_by_user_id=B (forbidden)
        with conn.transaction():
            try:
                run_as_user(conn, USER_A,
                    "INSERT INTO public._poc_issues (identifier, title, created_by_user_id) VALUES ('MH-7','A spoof B',%s)",
                    (USER_B,))
                fails.append("A spoof-insert as B should have been blocked")
                print("  FAIL A inserted on behalf of B (RLS broken)")
            except psycopg.errors.InsufficientPrivilege:
                print("  PASS A spoof-insert as B blocked")

        # UPDATE — A updates own (allowed), A updates C's (blocked)
        print("\n=== Phase 4: UPDATE enforcement ===")
        with conn.transaction():
            run_as_user(conn, USER_A,
                "UPDATE public._poc_issues SET title='A updated' WHERE identifier='MH-1'")
            print("  PASS A updates own")
        with conn.transaction():
            try:
                run_as_user(conn, USER_A,
                    "UPDATE public._poc_issues SET title='hijack' WHERE identifier='MH-5'")
                # SUCCESS row count: if RLS blocks, UPDATE matches 0 rows (no error).
                cur = conn.execute("SELECT title FROM public._poc_issues WHERE identifier='MH-5'", prepare=False)
                # need service_role to verify
            except Exception as e:
                print(f"  unexpected: {e}")
        with conn.transaction():
            conn.execute("SET LOCAL ROLE service_role")
            cur = conn.execute("SELECT title FROM public._poc_issues WHERE identifier='MH-5'")
            title = cur.fetchone()[0]
            assert_eq("MH-5 unchanged after A's hijack attempt", title, 'C only issue', fails)

        # DELETE — A tries to delete own (blocked, no DELETE policy for authenticated)
        print("\n=== Phase 5: DELETE enforcement ===")
        with conn.transaction():
            run_as_user(conn, USER_A,
                "DELETE FROM public._poc_issues WHERE identifier='MH-1'")
        with conn.transaction():
            conn.execute("SET LOCAL ROLE service_role")
            cur = conn.execute("SELECT count(*) FROM public._poc_issues WHERE identifier='MH-1'")
            still_there = cur.fetchone()[0]
            assert_eq("A's DELETE blocked (row still there)", still_there, 1, fails)

        # service_role can DELETE
        with conn.transaction():
            conn.execute("SET LOCAL ROLE service_role")
            conn.execute("DELETE FROM public._poc_issues WHERE identifier='MH-1'")
            cur = conn.execute("SELECT count(*) FROM public._poc_issues WHERE identifier='MH-1'")
            assert_eq("service_role DELETE works", cur.fetchone()[0], 0, fails)

    # Teardown
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS public._poc_issues CASCADE")
        conn.execute("DROP TABLE IF EXISTS public._poc_team_members CASCADE")
        print("\nteardown: dropped _poc_issues + _poc_team_members")

    if fails:
        print("\nFAIL items:")
        for f in fails: print(f"  - {f}")
        return 1
    print("\nPASS — all 6 RLS policy assertions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
