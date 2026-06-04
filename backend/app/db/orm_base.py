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
