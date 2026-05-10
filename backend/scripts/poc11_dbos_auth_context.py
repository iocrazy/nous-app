"""PoC #11 — DBOS step 内 supabase-py auth 上下文工作模式验证。

⚠ ONE-SHOT DEV SCRIPT (committed for reference, not for prod use)
   - Creates and drops `_poc11_issues` on NAS dev.
   - Demonstrates the canonical "handler validates user → workflow uses
     service_role to bypass RLS" pattern that production DBOS workflows
     should follow. See design doc PoC #11 section.

设计文档 P10/P11 工作模式：
    1) FastAPI handler 拿到 user JWT → 校验 user 有权创建该资源 → 调 DBOS.start_workflow(...)
    2) DBOS workflow 内 step 用 service_role HTTP 客户端调 PostgREST → bypass RLS
    3) DBOS 自身的 PG 连接（mediahub_dbos）NOT BYPASSRLS — 隔离正确

本脚本演示 + 断言这条路径：

    Phase A: 模拟"恶意 handler" — 以 USER_A 身份直连 PostgREST（用 user JWT），
             试图插一条 created_by_user_id=USER_B 的 issue → 应该被 RLS 拒
    Phase B: 模拟"合法 handler" — 校验通过后调 DBOS workflow，workflow step 用
             service_role 客户端写入 → 成功 + 数据正确
    Phase C: 验证 DBOS 自身的 mediahub_dbos role 不会绕过 RLS（防御性）

Run from backend/:
    uv run python scripts/poc11_dbos_auth_context.py
"""

from __future__ import annotations

import os
import sys
import time

import psycopg

USER_A = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
USER_B = "61f15833-2b2b-4a53-b126-16897f9184a8"


def _admin_dsn() -> str:
    pwd = os.environ.get("DEV_PG_PASSWORD")
    if not pwd:
        raise SystemExit(
            "DEV_PG_PASSWORD env var required. Export it from 1Password / NAS .env."
        )
    return (
        f"host=127.0.0.1 port=55433 dbname=postgres "
        f"user=postgres.heygo-dev password={pwd} "
        f"sslmode=disable connect_timeout=8"
    )


def _dbos_dsn() -> str:
    """DBOS runtime connection (mediahub_dbos role)."""
    from pathlib import Path

    env = Path(__file__).resolve().parent.parent / ".env.local"
    for line in env.read_text().splitlines():
        if line.startswith("DBOS_DATABASE_URL="):
            return line.split("=", 1)[1] + "?sslmode=disable"
    raise SystemExit("DBOS_DATABASE_URL missing")


SCHEMA = """
DROP TABLE IF EXISTS public._poc11_issues CASCADE;
CREATE TABLE public._poc11_issues (
  id BIGSERIAL PRIMARY KEY,
  identifier TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'todo',
  created_by_user_id UUID NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE public._poc11_issues ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON public._poc11_issues TO authenticated, anon, service_role;
GRANT SELECT, INSERT, UPDATE ON public._poc11_issues TO mediahub_dbos;
GRANT USAGE, SELECT ON SEQUENCE public._poc11_issues_id_seq TO authenticated, anon, service_role, mediahub_dbos;

-- Same INSERT policy as PoC #10
CREATE POLICY p_insert ON public._poc11_issues
  FOR INSERT WITH CHECK (created_by_user_id = auth.uid());

CREATE POLICY p_select ON public._poc11_issues
  FOR SELECT USING (created_by_user_id = auth.uid());
"""


def assert_eq(label, actual, expected, fails):
    ok = actual == expected
    print(f"  {'PASS' if ok else 'FAIL'} {label}: actual={actual} expected={expected}")
    if not ok:
        fails.append(label)


def main() -> int:
    fails: list[str] = []

    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(SCHEMA)
        print("schema + 2 RLS policies ready")

    # ---- Phase A: simulate malicious handler that didn't validate user identity ----
    print("\n=== Phase A: malicious handler (user JWT, spoof created_by) ===")
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE authenticated")
            conn.execute(
                f'SET LOCAL request.jwt.claims = \'{{"sub":"{USER_A}","role":"authenticated"}}\''
            )
            try:
                conn.execute(
                    "INSERT INTO public._poc11_issues (identifier, title, created_by_user_id) VALUES (%s, %s, %s)",
                    ("MH-spoof", "A pretending to be B", USER_B),
                )
                fails.append("RLS did not block created_by spoof (security regression)")
                print("  FAIL spoof insert was permitted")
            except psycopg.errors.InsufficientPrivilege:
                print("  PASS RLS WITH CHECK blocks created_by_user_id spoof")

    # ---- Phase B: legitimate path — handler validates, then workflow uses service_role ----
    print(
        "\n=== Phase B: legitimate path — handler validates, workflow uses service_role ==="
    )

    # 1) Handler step: validate user identity (here: just use auth.uid() == USER_A — pretend
    #    real handler decoded JWT)
    handler_validated_user = USER_A
    print(f"  handler validated user: {handler_validated_user}")

    # 2) Workflow step: use service_role client to write — bypasses RLS
    from dbos import DBOS, DBOSConfig, SetWorkflowID

    cfg: DBOSConfig = {"name": "mediahub-poc11", "database_url": _dbos_dsn()}
    DBOS(config=cfg)

    @DBOS.step()
    def write_via_service_role(creator: str, title: str) -> int:
        # Simulates `supabase.from('issues').insert({...})` with service_role JWT.
        # Use autocommit (matches the simpler repro and DBOS recommended pattern).
        conn = psycopg.connect(_admin_dsn(), autocommit=True)
        try:
            conn.execute("SET ROLE service_role")
            cur = conn.execute(
                "INSERT INTO public._poc11_issues (identifier, title, created_by_user_id) "
                "VALUES (%s, %s, %s) RETURNING id, current_user, txid_current()",
                (f"MH-poc11-{creator[:8]}", title, creator),
            )
            row = cur.fetchone()
            print(f"  [step] INSERT returned id={row[0]} role={row[1]} txid={row[2]}")
            return row[0]
        finally:
            conn.close()

    @DBOS.workflow()
    def create_issue_wf(creator: str, title: str) -> dict:
        new_id = write_via_service_role(creator, title)
        return {"id": new_id, "creator": creator}

    DBOS.launch()
    # Use unique workflow_id per run so DBOS memoization does NOT short-circuit
    # the actual step execution (we want to test the live INSERT each run).
    workflow_id = f"poc11-{handler_validated_user[:8]}-{int(time.time())}"
    with SetWorkflowID(workflow_id):
        result = create_issue_wf(handler_validated_user, "Issue from validated handler")
    print(f"  workflow result: {result}")

    # Debug: inspect table state immediately
    with psycopg.connect(_admin_dsn(), autocommit=True) as dbg:
        dbg.execute("SET ROLE service_role")
        cur = dbg.execute(
            "SELECT id, identifier, created_by_user_id::text FROM public._poc11_issues ORDER BY id"
        )
        print(f"  DEBUG table contents post-workflow: {cur.fetchall()}")

    # Verify the row landed correctly. Note: postgres role (Supavisor connect target)
    # is NOT superuser, so RLS applies. Use SET ROLE service_role to bypass.
    with psycopg.connect(_admin_dsn()) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE service_role")
            cur = conn.execute(
                "SELECT created_by_user_id, title FROM public._poc11_issues WHERE id=%s",
                (result["id"],),
            )
            row = cur.fetchone()
        if row is None:
            fails.append(
                f"workflow-written row id={result['id']} not found via service_role SELECT"
            )
            print(f"  FAIL row id={result['id']} not found")
        else:
            assert_eq(
                "workflow-written row creator matches handler-validated user",
                str(row[0]),
                USER_A,
                fails,
            )

    # ---- Phase C: defensive — DBOS connection role does NOT bypass RLS ----
    print("\n=== Phase C: mediahub_dbos role is NOT BYPASSRLS (defensive check) ===")
    with psycopg.connect(_dbos_dsn()) as conn:
        with conn.transaction():
            try:
                # As mediahub_dbos (no auth.uid() set, no role override) — RLS should block
                conn.execute(
                    "INSERT INTO public._poc11_issues (identifier, title, created_by_user_id) VALUES (%s, %s, %s)",
                    ("MH-direct", "DBOS direct write should fail", USER_A),
                )
                fails.append(
                    "mediahub_dbos was able to bypass RLS — role hardening broken"
                )
                print("  FAIL mediahub_dbos bypassed RLS (should not!)")
            except psycopg.errors.InsufficientPrivilege:
                print(
                    "  PASS mediahub_dbos blocked by RLS — must use service_role to write public.*"
                )

    # Verify role attributes
    with psycopg.connect(_admin_dsn()) as conn:
        cur = conn.execute(
            "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname IN ('mediahub_dbos','service_role') ORDER BY rolname"
        )
        for r in cur.fetchall():
            expected = r[0] == "service_role"  # only service_role should have BYPASSRLS
            assert_eq(f"{r[0]}.rolbypassrls", r[1], expected, fails)

    # Teardown
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute("DROP TABLE public._poc11_issues CASCADE")
        print("\nteardown: dropped _poc11_issues")

    if fails:
        print("\nFAIL items:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nPASS — DBOS auth-context work mode validated:")
    print("  1) RLS catches malicious spoof at PG layer (defense in depth)")
    print("  2) Workflow step explicitly elevates to service_role to bypass RLS")
    print("  3) DBOS runtime role (mediahub_dbos) does NOT bypass RLS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
