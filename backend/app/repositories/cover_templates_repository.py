"""Data access for cover_templates (封面工作室的样图模板库, migration 435).

ORM-model style (read_scope/write_scope + select on ``CoverTemplates``), same
shape as generated_media_repository: every returned dict has its snowflake /
UUID columns stringified so JSON serialisation is lossless on the JS side.

列表只有一种形状：某 scope 下的模板，用得多的排前面。索引
``idx_cover_templates_scope_usage`` 就是照这个形状建的。

删除是硬删除 —— 软删除会让归档行继续握着 ``ON DELETE RESTRICT`` 外键，于是用户
将来删那张图时被一条他明明已经删掉、界面上也看不见的模板挡住。全文见迁移 435。
"""

from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import CoverTemplates

_CT_COLS = (
    CoverTemplates.id,
    CoverTemplates.scope_id,
    CoverTemplates.creator_id,
    CoverTemplates.name,
    CoverTemplates.generated_media_id,
    CoverTemplates.source_kind,
    CoverTemplates.source_resource_id,
    CoverTemplates.usage_count,
    CoverTemplates.last_used_at,
    CoverTemplates.created_at,
    CoverTemplates.updated_at,
)

# Snowflake BIGINT columns: str() before they reach the frontend or JS loses
# precision above 2^53 (same guard as generated_media_repository).
_BIGINT_COLS = ("id", "scope_id", "generated_media_id", "source_resource_id")
_UUID_COLS = ("creator_id",)


def _normalize(row: Optional[dict]) -> Optional[dict]:
    """Stringify bigint and UUID fields so JSON serialisation is lossless."""
    if not row:
        return row
    out = dict(row)
    for c in _BIGINT_COLS + _UUID_COLS:
        if out.get(c) is not None:
            out[c] = str(out[c])
    return out


class CoverTemplatesRepository:
    async def list_for_scope(self, scope_id: int) -> list[dict]:
        """Templates for a scope, most-used first.

        Ordering is usage_count DESC then created_at DESC — not created_at
        alone. Sorting purely by age buries the first templates a user ever
        saved, which are exactly the ones they kept because they work.
        """
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(*_CT_COLS)
                        .where(CoverTemplates.scope_id == scope_id)
                        .order_by(
                            CoverTemplates.usage_count.desc(),
                            CoverTemplates.created_at.desc(),
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [_normalize(dict(r)) for r in rows]

    async def get(self, template_id: int, scope_id: int) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_CT_COLS).where(
                            CoverTemplates.id == template_id,
                            CoverTemplates.scope_id == scope_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def find_by_media(
        self, scope_id: int, generated_media_id: int
    ) -> Optional[dict]:
        """The template citing this image in this scope, if any.

        Backs the idempotent add: re-adding the same picture returns the row
        that already exists rather than tripping ``ux_cover_templates_scope_media``
        or accumulating identical cards under different names.
        """
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_CT_COLS).where(
                            CoverTemplates.scope_id == scope_id,
                            CoverTemplates.generated_media_id == generated_media_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def create(
        self,
        *,
        scope_id: int,
        creator_id: str,
        name: str,
        generated_media_id: int,
        source_kind: str,
        source_resource_id: Optional[int] = None,
    ) -> dict:
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        CoverTemplates.__table__.insert()
                        .values(
                            scope_id=scope_id,
                            creator_id=creator_id,
                            name=name,
                            generated_media_id=generated_media_id,
                            source_kind=source_kind,
                            source_resource_id=source_resource_id,
                        )
                        .returning(*_CT_COLS)
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row))

    async def rename(
        self, template_id: int, scope_id: int, name: str
    ) -> Optional[dict]:
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        sa_update(CoverTemplates)
                        .where(
                            CoverTemplates.id == template_id,
                            CoverTemplates.scope_id == scope_id,
                        )
                        .values(
                            name=name, updated_at=datetime.datetime.now(datetime.UTC)
                        )
                        .returning(*_CT_COLS)
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def delete(self, template_id: int, scope_id: int) -> bool:
        """Hard-delete the template row.

        The picture itself is untouched — the row only cited it. Removing a
        template must also release the RESTRICT foreign key, otherwise the
        image becomes undeletable forever and the thing blocking it is
        invisible in every UI the user has.
        """
        async with write_scope() as session:
            result = await session.execute(
                sa_delete(CoverTemplates).where(
                    CoverTemplates.id == template_id,
                    CoverTemplates.scope_id == scope_id,
                )
            )
            return bool(result.rowcount)

    async def bump_usage(self, template_ids: list[int], scope_id: int) -> None:
        """Record that these templates were just handed to the model.

        Called once per generation, not once per rendered draft: the count is
        "how often did I reach for this", and a two-stage run that reuses the
        same references twice did not make the template twice as useful.
        """
        if not template_ids:
            return
        now = datetime.datetime.now(datetime.UTC)
        async with write_scope() as session:
            await session.execute(
                sa_update(CoverTemplates)
                .where(
                    CoverTemplates.id.in_(template_ids),
                    CoverTemplates.scope_id == scope_id,
                )
                .values(
                    usage_count=CoverTemplates.usage_count + 1,
                    last_used_at=now,
                    updated_at=now,
                )
            )

    async def names_blocking_media(self, generated_media_id: int) -> list[str]:
        """Template names citing this image, across every scope.

        Deliberately NOT scope-filtered: this answers "may this generated_media
        row be deleted", and the RESTRICT foreign key does not care whose scope
        the citing row is in. Filtering by scope here would report "no
        templates" and then let the DELETE fail anyway with a 500 — the exact
        mismatch this lookup exists to prevent.
        """
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(CoverTemplates.name).where(
                            CoverTemplates.generated_media_id == generated_media_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [str(r) for r in rows]


def get_cover_templates_repository() -> CoverTemplatesRepository:
    return CoverTemplatesRepository()
