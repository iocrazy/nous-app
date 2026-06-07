"""Active REST-vs-ORM parity check for Tier-0 admin domains (READ-ONLY).

Runs each repo's read method via BOTH the legacy supabase-py REST impl and the
SQLAlchemy ORM impl against the SAME database, and diffs the outputs with the
shadow-compare differ. Real-data parity proof without needing live traffic.

Usage (point both REST + ORM at the same DB):
    source /tmp/prod_parity.env   # or dev_parity.env
    uv run python scripts/orm_parity_check.py

ONLY read methods are invoked — no create/update/delete. Safe against prod.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.db.shadow_compare import diff_results

_SINCE = datetime.now(timezone.utc) - timedelta(days=30)


def _cases():
    """(domain, rest_repo, orm_repo, method, args) — read methods only."""
    from app.repositories.admin.audit_logs_repository import AuditLogsRepository
    from app.repositories.admin.audit_logs_repository_orm import (
        AuditLogsRepositoryOrm,
    )
    from app.repositories.admin.stats_repository import AdminStatsRepository
    from app.repositories.admin.stats_repository_orm import (
        AdminStatsRepositoryOrm,
    )
    from app.repositories.admin.system_settings_repository import (
        SystemSettingsRepository,
    )
    from app.repositories.admin.system_settings_repository_orm import (
        SystemSettingsRepositoryOrm,
    )
    from app.repositories.admin.tasks_repository import AdminTasksRepository
    from app.repositories.admin.tasks_repository_orm import (
        AdminTasksRepositoryOrm,
    )
    from app.repositories.admin.videos_repository import AdminVideosRepository
    from app.repositories.admin.videos_repository_orm import (
        AdminVideosRepositoryOrm,
    )

    return [
        (
            "audit_logs",
            AuditLogsRepository(),
            AuditLogsRepositoryOrm(),
            "list_distinct_actions",
            (),
        ),
        (
            "audit_logs",
            AuditLogsRepository(),
            AuditLogsRepositoryOrm(),
            "list_since",
            (_SINCE,),
        ),
        (
            "stats",
            AdminStatsRepository(),
            AdminStatsRepositoryOrm(),
            "count_user_profiles",
            (),
        ),
        ("stats", AdminStatsRepository(), AdminStatsRepositoryOrm(), "count_teams", ()),
        (
            "stats",
            AdminStatsRepository(),
            AdminStatsRepositoryOrm(),
            "count_parsed_media",
            (),
        ),
        (
            "system_settings",
            SystemSettingsRepository(),
            SystemSettingsRepositoryOrm(),
            "list_non_transcode",
            (),
        ),
        # NOTE: ADMIN_ALERT_RULES is excluded — the `alert_rules` table does not
        # exist on the self-hosted prod (a dead/never-deployed feature; both REST
        # and ORM error identically). Not a parity concern.
        ("tasks", AdminTasksRepository(), AdminTasksRepositoryOrm(), "count_total", ()),
        (
            "tasks",
            AdminTasksRepository(),
            AdminTasksRepositoryOrm(),
            "count_by_status",
            ("completed",),
        ),
        (
            "videos",
            AdminVideosRepository(),
            AdminVideosRepositoryOrm(),
            "count_total",
            (),
        ),
        (
            "videos",
            AdminVideosRepository(),
            AdminVideosRepositoryOrm(),
            "count_by_status",
            ("completed",),
        ),
    ]


async def main() -> int:
    passed = failed = errored = 0
    for domain, rest, orm, method, args in _cases():
        label = f"{domain}.{method}{args}"
        try:
            rest_out = await getattr(rest, method)(*args)
            orm_out = await getattr(orm, method)(*args)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR  {label}: {type(exc).__name__}: {exc}")
            errored += 1
            continue
        diff = diff_results(rest_out, orm_out)
        if diff is None:
            n = len(rest_out) if isinstance(rest_out, (list, dict)) else rest_out
            print(f"  PASS   {label}  (= {n!r})")
            passed += 1
        else:
            print(f"  DIFF   {label}: {diff}")
            failed += 1
    print(f"\nparity: {passed} pass / {failed} diff / {errored} error")
    return 1 if (failed or errored) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
