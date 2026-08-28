"""Cover template library = a system folder in the resource library (mig 441).

Three responsibilities, nothing else:

1. ``ensure_folder`` — find the scope's cover-template folder by ``system_key``;
   failing that, ADOPT a top-level folder the user already named for the job;
   failing that, create one. Adoption exists because users build this folder
   before the feature does (a "封面" folder full of samples showed up in prod
   the day before this shipped), and silently creating a second one beside it
   would be the "two libraries" problem all over again.
2. ``list_images`` — the folder's image resources joined with usage, most-used
   first. Membership is the folder; this repo never stores membership.
3. ``bump_usage`` — the "used N×" counter, keyed by (scope, resource).

Every returned dict has its snowflake / UUID columns stringified so JSON
serialisation is lossless on the JS side.
"""

from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import CoverTemplateUsage, Folders, ResourceItems, Resources

COVER_TEMPLATE_SYSTEM_KEY = "cover_templates"

# Names a user is likely to have given the folder before the feature existed.
# Matched exactly (after strip), top level of the personal library only, so a
# nested "封面" under some project cannot be captured by accident.
ADOPTABLE_NAMES = ("封面", "封面模板", "Covers", "Cover Templates", "covers")

DEFAULT_FOLDER_NAME = "Covers"


def _s(v):
    return None if v is None else str(v)


class CoverTemplatesRepository:
    async def find_folder(self, scope_id: int) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(Folders.id, Folders.name, Folders.is_system).where(
                            Folders.scope_id == scope_id,
                            Folders.system_key == COVER_TEMPLATE_SYSTEM_KEY,
                            Folders.is_trashed.is_(False),
                        )
                    )
                )
                .mappings()
                .first()
            )
        return (
            {"id": _s(row["id"]), "name": row["name"], "adopted": False}
            if row
            else None
        )

    async def ensure_folder(self, scope_id: int, user_id: str) -> dict:
        """The scope's cover-template folder, creating or adopting as needed.

        Returns ``{id, name, adopted}`` — ``adopted`` is True on the one call
        that claimed a pre-existing user folder, so the caller can say so.
        """
        found = await self.find_folder(scope_id)
        if found:
            return found

        async with write_scope() as session:
            # Adopt: a live, top-level, personal-library folder with one of the
            # expected names. Ordered oldest-first so a user who somehow has two
            # gets the one they made first, deterministically.
            cand = (
                (
                    await session.execute(
                        select(Folders.id, Folders.name)
                        .where(
                            Folders.scope_id == scope_id,
                            Folders.is_trashed.is_(False),
                            Folders.parent_id.is_(None),
                            Folders.library_id.is_(None),
                            Folders.system_key.is_(None),
                            func.btrim(Folders.name).in_(ADOPTABLE_NAMES),
                        )
                        .order_by(Folders.created_at.asc())
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            now = datetime.datetime.now(datetime.UTC)
            if cand:
                await session.execute(
                    Folders.__table__.update()
                    .where(Folders.id == cand["id"])
                    .values(
                        is_system=True,
                        system_key=COVER_TEMPLATE_SYSTEM_KEY,
                        updated_at=now,
                    )
                )
                return {"id": _s(cand["id"]), "name": cand["name"], "adopted": True}

            created = (
                (
                    await session.execute(
                        Folders.__table__.insert()
                        .values(
                            name=DEFAULT_FOLDER_NAME,
                            scope_id=scope_id,
                            created_by=user_id,
                            is_system=True,
                            system_key=COVER_TEMPLATE_SYSTEM_KEY,
                        )
                        .returning(Folders.id, Folders.name)
                    )
                )
                .mappings()
                .first()
            )
            return {"id": _s(created["id"]), "name": created["name"], "adopted": False}

    async def list_images(
        self,
        scope_id: int,
        folder_id: int,
        *,
        q: str = "",
        limit: int = 48,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Image resources filed in the folder, most-used first, then newest.

        Paged and searchable: the library is meant to grow into the hundreds,
        so the studio opens it as a picker (search + load more), never as a
        wall of every picture. Returns ``(page, total)``.
        """
        needle = (q or "").strip()
        where = [
            ResourceItems.scope_id == scope_id,
            ResourceItems.folder_id == folder_id,
            Resources.is_trashed.is_(False),
            Resources.mime_type.ilike("image/%"),
        ]
        if needle:
            where.append(Resources.filename.ilike(f"%{needle}%"))
        async with read_scope() as session:
            total = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(ResourceItems)
                        .join(Resources, Resources.id == ResourceItems.resource_id)
                        .where(*where)
                    )
                ).scalar_one()
                or 0
            )
            rows = (
                (
                    await session.execute(
                        select(
                            Resources.id,
                            Resources.filename,
                            Resources.mime_type,
                            Resources.updated_at,
                            Resources.created_at,
                            func.coalesce(CoverTemplateUsage.usage_count, 0).label(
                                "usage_count"
                            ),
                            CoverTemplateUsage.last_used_at,
                        )
                        .select_from(ResourceItems)
                        .join(Resources, Resources.id == ResourceItems.resource_id)
                        .outerjoin(
                            CoverTemplateUsage,
                            (CoverTemplateUsage.resource_id == Resources.id)
                            & (CoverTemplateUsage.scope_id == scope_id),
                        )
                        .where(*where)
                        .order_by(
                            func.coalesce(CoverTemplateUsage.usage_count, 0).desc(),
                            Resources.created_at.desc(),
                        )
                        .limit(max(1, min(int(limit), 200)))
                        .offset(max(0, int(offset)))
                    )
                )
                .mappings()
                .all()
            )
        return [
            {
                "resource_id": _s(r["id"]),
                "name": r["filename"] or "",
                "mime_type": r["mime_type"],
                "usage_count": int(r["usage_count"] or 0),
                "last_used_at": r["last_used_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ], total

    async def bump_usage(self, resource_ids: list[int], scope_id: int) -> None:
        """Once per generation, not once per rendered draft."""
        if not resource_ids:
            return
        now = datetime.datetime.now(datetime.UTC)
        async with write_scope() as session:
            stmt = pg_insert(CoverTemplateUsage).values(
                [
                    {
                        "scope_id": scope_id,
                        "resource_id": rid,
                        "usage_count": 1,
                        "last_used_at": now,
                    }
                    for rid in resource_ids
                ]
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[
                        CoverTemplateUsage.scope_id,
                        CoverTemplateUsage.resource_id,
                    ],
                    set_={
                        "usage_count": CoverTemplateUsage.usage_count + 1,
                        "last_used_at": now,
                        "updated_at": now,
                    },
                )
            )


def get_cover_templates_repository() -> CoverTemplatesRepository:
    return CoverTemplatesRepository()
