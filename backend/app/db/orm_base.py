"""SQLAlchemy 2.0 ORM declarative root.

The ORM layer sits ON TOP of the existing asyncpg engine (app/db/engine.py).
SQLAlchemy is not a driver — `postgresql+asyncpg://` means Core/ORM over the
same asyncpg driver + Supavisor transaction-mode pooler already used by the
asyncpg repos. We are not introducing a new transport.

Models map EXISTING tables owned by supabase/migrations/*.sql. They are
generated from the live DB via sqlacodegen (then flattened — no relationship()
to avoid async lazy-load) and live under app/models/.

INVARIANTS (see docs/superpowers/plans/2026-06-04-orm-2-migration.md §2.3):
  * NEVER call Base.metadata.create_all against a real database — the SQL
    migration system owns the schema.
  * Drift between models and the live schema is guarded by
    tests/db/test_schema_drift.py (reflection diff), NOT Alembic autogenerate.
  * Columns the DB owns (snowflake-bigint PKs, created_at/updated_at triggers)
    use server_default=text('...') — never a Python-side default=/onupdate= that
    would fight the DB trigger.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative root for all mapped models. Reference metadata only."""

    pass


# ── Tenant-scope marker mixins (see decisions doc §7.1) ──────────────────
#
# These are *markers*: a model inherits the mixin(s) matching its tenancy so
# the do_orm_execute choke point (app/db/scope.py) can recognise it and inject
# the per-axis tenant filter. They declare NO columns — the column already
# exists on the concrete model (owned by supabase/migrations/*.sql); the mixin
# only names which column carries the tenant key.
#
# SAFETY: applying a mixin to a real model is a per-repo migration step, NOT
# done here. The instant a production model becomes scoped, every existing
# read_scope()/write_scope() call that has NOT established a scope would
# fail-closed with UnscopedQueryError. Mix in only once the owning repo sets a
# scope. (The choke-point mechanism itself is inert until a model opts in.)


class UserScoped:
    """Per-user owner column. Subclasses override ``__tenant_user_col__`` to
    the actual owner column name (``user_id`` / ``creator_id`` / ``created_by``
    / ``owner_id``). The choke point injects ``<col> == scope.user_id`` and
    ``before_insert`` stamps the column from the active scope."""

    __tenant_user_col__: str = "user_id"


class TeamScoped:
    """Team-shared rows keyed by a ``team_id`` column. The choke point injects
    ``team_id IN scope.team_ids``."""

    __tenant_team_col__: str = "team_id"


class ProjectScoped:
    """Project-shared rows keyed by a ``project_id`` column. The choke point
    injects ``project_id IN scope.project_ids``."""

    __tenant_project_col__: str = "project_id"
