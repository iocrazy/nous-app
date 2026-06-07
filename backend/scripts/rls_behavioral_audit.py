"""Behavioral RLS correctness audit (READ-ONLY, dev-only).

Static RLS lint (reading ``pg_policy`` text) is unreliable: PostgreSQL reuses a
policy's ``USING`` clause as its ``WITH CHECK`` when the latter is omitted, and a
``USING(true)`` granted to ``service_role`` is intentional. So policy *text* can't
tell you whether tenant isolation actually holds.

This proves isolation *behaviorally* by impersonating a user at the DB level and
observing what rows are actually visible — immune to every static-lint subtlety:

    BEGIN;                                          -- per table, always ROLLBACK
      SELECT set_config('request.jwt.claims',       -- inject the JWT auth.uid()
                        '{"sub":"<uuid>","role":"authenticated"}', true);
      SET LOCAL ROLE authenticated;                 -- drop bypassrls, RLS applies
      SELECT count(*) FROM <table>;                 -- what can this user really see?
    ROLLBACK;

No rows are written or fabricated — it reads REAL dev data, so it works across
wildly different table schemas without per-table seed fixtures.

WHY THIS ANSWERS "is my RLS correct" (not just "does ORM match RLS"):
The CORRECT permission model is defined INDEPENDENTLY here (tenant isolation: a
fresh user must see ~0 rows of someone else's tenant data). We test the RLS path
against that independent spec — so a wrong RLS policy FAILS the audit instead of
silently becoming the "spec" the ORM copies. Run the SAME spec against the ORM
read path (scope-injected repo call) to prove the two are equivalently CORRECT,
not merely equal.

Connection: a SESSION-mode SUPERUSER/``postgres`` DSN to **dev** (must be able to
``SET ROLE authenticated`` + ``set_config('request.jwt.claims', ...)``). The ORM's
own Supavisor pooler user is ``bypassrls`` and transaction-pooled — wrong for this.
Pass it via env (never hardcoded, never committed):

    AUDIT_DB_DSN='postgresql://postgres:***@<dev-host>:<port>/postgres' \
        uv run python scripts/rls_behavioral_audit.py

Safe against the data (read-only + ROLLBACK), but point it at DEV, not prod.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass

import asyncpg

# A fresh random uuid that, by construction, owns NOTHING in any table. A
# correctly-isolated tenant table must show this user ~0 rows.
_NOBODY = str(uuid.uuid4())


@dataclass(frozen=True)
class Target:
    """A table to audit. ``owner_col`` enables the finer foreign-row leak check;
    ``None`` means the table has no per-user owner column (pure system data) and
    we can only assert the coarse 'a nobody-user sees 0 of N rows' isolation."""

    table: str
    scope: str  # "user" | "team" | "system"
    owner_col: str | None
    # Human intent, so the verdict can flag a *mismatch* (named admin-only but
    # behaviorally all-authenticated == likely RLS bug).
    intent: str


# --- Audit target set (extend by appending rows) -----------------------------
# Covers the full surface locked by migration 265 (so this doubles as that
# migration's regression guard) plus representative correctly-isolated tables.
# After 265, a nobody-user must see 0 rows in EVERY row below.
#
# NOTE: this harness impersonates the ``authenticated`` role (the real leak
# vector — logged-in users). The ``anon`` role is denied identically for the
# REVOKE-ALL group-2 tables (boundary_audit / task_flows / issue_sequence);
# anon denial is asserted directly in the migration's dev dry-run.
_TARGETS: tuple[Target, ...] = (
    # ── Locked by mig 265, group 1 (RLS-on, dropped over-permissive SELECT) ──
    Target("application_logs", "system", None, "admin-only (system logs)"),
    Target("api_request_logs", "system", "user_id", "admin-only (request logs)"),
    Target("frontend_error_logs", "system", "user_id", "admin-only (error logs)"),
    Target("system_settings", "system", None, "admin-only (config)"),
    Target("dbos_workflow_routing", "system", None, "backend-only (routing)"),
    # ── Locked by mig 265, group 2 (RLS enabled + REVOKE ALL) ───────────────
    Target("boundary_audit", "system", None, "backend-only (audit)"),
    Target("task_flows", "system", None, "backend-only (task aggregate)"),
    Target("issue_sequence", "system", None, "backend-only (sequence)"),
    # ── Controls: correctly-isolated tenant tables (must stay 0 for nobody) ──
    Target("resources", "user", "creator_id", "owner-only"),
    Target("user_settings", "user", "user_id", "owner-only"),
    Target("teams", "team", None, "members-only"),
    Target("team_members", "team", None, "members-only"),
)


def _claims(sub: str) -> str:
    return json.dumps({"sub": sub, "role": "authenticated"})


async def _as_authenticated(conn: asyncpg.Connection, sub: str, sql: str, *args):
    """Run ``sql`` impersonating ``sub`` as the ``authenticated`` role, then
    ROLLBACK so neither the role, the claims, nor any (hypothetical) write leak
    out. SET LOCAL is transaction-scoped; the explicit rollback is belt-and-braces."""
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("SELECT set_config('request.jwt.claims', $1, true)", _claims(sub))
        await conn.execute("SET LOCAL ROLE authenticated")
        return await conn.fetchval(sql, *args)
    finally:
        await tx.rollback()


async def _audit_one(conn: asyncpg.Connection, t: Target) -> tuple[str, str]:
    """Return (verdict, detail). verdict in {ISOLATED, LEAK, EMPTY, NO_GRANT, ERROR}."""
    total = await conn.fetchval(f"SELECT count(*) FROM public.{t.table}")
    if total == 0:
        return "EMPTY", "table has 0 rows on dev — cannot prove isolation"

    # 1. A user who owns nothing must see ~0 rows. Seeing all N == USING(true) leak.
    try:
        nobody_sees = await _as_authenticated(
            conn, _NOBODY, f"SELECT count(*) FROM public.{t.table}"
        )
    except asyncpg.InsufficientPrivilegeError:
        return "NO_GRANT", "authenticated has no SELECT grant (deny-all — safe)"
    except Exception as exc:  # noqa: BLE001
        return "ERROR", f"{type(exc).__name__}: {exc}"

    if nobody_sees == total:
        return (
            "LEAK",
            f"a no-data user sees ALL {total} rows (USING(true)-style). "
            f"Policy intent={t.intent!r} → "
            + ("MISMATCH (bug)" if "only" in t.intent else "by design?"),
        )
    if nobody_sees > 0:
        return "LEAK", f"a no-data user sees {nobody_sees}/{total} foreign rows"

    # 2. nobody sees 0 — isolation is active. If there's an owner column, confirm a
    #    REAL owner sees only their own rows (no foreign-row leak via a weak policy).
    if t.owner_col is None:
        return "ISOLATED", f"no-data user sees 0/{total} rows (membership/owner gated)"

    real_owner = await conn.fetchval(
        f"SELECT {t.owner_col} FROM public.{t.table} "
        f"WHERE {t.owner_col} IS NOT NULL LIMIT 1"
    )
    if real_owner is None:
        return "ISOLATED", f"no-data user sees 0/{total}; no non-null owner to cross-check"

    foreign = await _as_authenticated(
        conn,
        str(real_owner),
        f"SELECT count(*) FROM public.{t.table} "
        f"WHERE {t.owner_col} IS DISTINCT FROM $1::uuid",
        str(real_owner),
    )
    if foreign and foreign > 0:
        return "LEAK", f"owner {real_owner} also sees {foreign} rows they don't own"
    return "ISOLATED", f"no-data user sees 0/{total}; owner sees only own rows"


async def main() -> int:
    dsn = os.environ.get("AUDIT_DB_DSN")
    if not dsn:
        print("ERROR: set AUDIT_DB_DSN to a session-mode superuser DSN for DEV.")
        return 2

    conn = await asyncpg.connect(dsn)
    leaks = 0
    try:
        whoami = await conn.fetchval("SELECT current_user")
        can_setrole = await conn.fetchval(
            "SELECT rolsuper OR pg_has_role(current_user, 'authenticated', 'MEMBER') "
            "FROM pg_roles WHERE rolname = current_user"
        )
        print(f"connected as {whoami!r} (can impersonate authenticated: {can_setrole})\n")
        for t in _TARGETS:
            verdict, detail = await _audit_one(conn, t)
            mark = {"ISOLATED": "PASS ", "LEAK": "LEAK ", "EMPTY": "skip ",
                    "NO_GRANT": "PASS ", "ERROR": "err  "}.get(verdict, "?    ")
            if verdict == "LEAK":
                leaks += 1
            print(f"  {mark} [{t.scope:6}] {t.table:24} {verdict:9} {detail}")
    finally:
        await conn.close()

    print(f"\naudit: {leaks} leak(s) across {len(_TARGETS)} tables")
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
