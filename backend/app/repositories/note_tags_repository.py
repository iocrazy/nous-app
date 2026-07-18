"""Note ↔ tag junction access (mirror of resource_tags usage)."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models.inspiration import InspirationNotes, NoteTags


class NoteTagsRepository:
    async def sync_for_note(self, note_id: int, tag_ids: list[int]) -> None:
        """Diff-sync the junction to exactly ``tag_ids`` (spec §4.1)."""
        nid = int(note_id)
        wanted = {int(t) for t in tag_ids}
        async with write_scope() as session:
            existing = set(
                (
                    await session.execute(
                        select(NoteTags.tag_id).where(NoteTags.note_id == nid)
                    )
                )
                .scalars()
                .all()
            )
            to_add = sorted(wanted - existing)
            to_del = sorted(existing - wanted)
            if to_add:
                await session.execute(
                    pg_insert(NoteTags)
                    .values([{"note_id": nid, "tag_id": t} for t in to_add])
                    .on_conflict_do_nothing()
                )
            if to_del:
                await session.execute(
                    delete(NoteTags).where(
                        NoteTags.note_id == nid, NoteTags.tag_id.in_(to_del)
                    )
                )

    async def counts_for_user(self, user_id: str) -> dict[int, int]:
        """tag_id → count of live notes for this user (statistics, spec §4.3)."""
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(NoteTags.tag_id, func.count().label("cnt"))
                    .join(InspirationNotes, InspirationNotes.id == NoteTags.note_id)
                    .where(
                        InspirationNotes.user_id == user_id,
                        InspirationNotes.deleted_at.is_(None),
                    )
                    .group_by(NoteTags.tag_id)
                )
            ).all()
        return {int(r[0]): int(r[1]) for r in rows}


_repo: NoteTagsRepository | None = None


def get_note_tags_repository() -> NoteTagsRepository:
    global _repo
    if _repo is None:
        _repo = NoteTagsRepository()
    return _repo
