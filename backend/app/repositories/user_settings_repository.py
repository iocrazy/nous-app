# backend/app/repositories/user_settings_repository.py

"""用户设置数据访问层

处理用户个人设置的 CRUD 操作。

Backed by the SQLAlchemy 2.0 ORM session layer (Task 5.4). Reads run on
``read_scope()`` + ``select(UserSettings)``; the canonical ``settings_json``
merge runs inside the committing ``write_scope()`` session. ``delete`` still
goes through the supabase-py async client.

THE #1 INVARIANT — MERGE-NOT-REPLACE
====================================
``settings_json`` is a SHARED column: General settings, ``ai_settings``,
``parse_mode`` and more all live under one JSON blob. A bare column REPLACE
wiped the AI provider config when a General setting was saved — real data loss
on 2026-06-02 (bug ``user_settings_json_clobber`` / PR #485; the keys had to be
re-entered by hand). Every settings_json write goes through the single
canonical merge path (``patch_settings_json`` → ``_atomic_merge_settings_json``)
which runs ``settings_json = COALESCE(existing,'{}'::jsonb) || CAST(:patch AS
jsonb)`` under ON CONFLICT — an atomic, race-free top-level merge that PRESERVES
every untouched key. It runs inside a committing ``write_scope()`` session so
the write actually persists.

VALUE-TYPE PARITY (REST → ORM)
==============================
The historical baseline was PostgREST (JSON): it rendered ``user_id`` / ``id``
(uuid) as STRINGS and ``created_at`` / ``updated_at`` (timestamptz) as ISO
STRINGS. The type-sensitive consumer is ``user_settings_router`` — it feeds
these into a Pydantic ``UserSettingsResponse`` whose ``user_id`` / ``id`` /
timestamps are typed ``str``, and Pydantic v2 REJECTS a bare ``uuid.UUID`` /
``datetime`` for a ``str`` field (it does not coerce). So the ORM read dict
coerces uuid→str and datetime→ISO-str for exactly those columns
(``_row_to_rest_dict``). ``settings_json`` (jsonb) already deserializes to a
Python ``dict`` via the ORM — matching what PostgREST returned — so no
``json.loads`` is needed on the read path. The other consumers only read
``settings_json`` (native dict is fine).
"""

from typing import Any, Dict, Optional

from loguru import logger

from app.core.cache import user_settings_cache


def merge_settings_json(
    existing: Optional[Dict[str, Any]], incoming: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Shallow-merge ``incoming`` over ``existing``, preserving untouched keys.

    ``settings_json`` is a SHARED column: General settings, ``ai_settings``,
    ``parse_mode`` and more all live under the same JSON blob. A bare column
    replace wiped the AI provider config when a General setting was saved —
    real data loss on 2026-06-02 (the keys had to be re-entered by hand; no
    backup/PITR existed to recover them).

    The canonical write path is the atomic ``||`` merge in
    ``_atomic_merge_settings_json``; this helper documents the same
    merge-not-replace semantics at the Python level (incoming keys win; keys
    absent from ``incoming`` are preserved).
    """
    return {**(existing or {}), **(incoming or {})}


class UserSettingsRepository:
    """用户设置仓库类 (异步)"""

    def __init__(self):
        pass

    @staticmethod
    def _row_to_rest_dict(obj: Any) -> Dict[str, Any]:
        """SELECT *-shaped dict for a ``UserSettings`` ORM row, coerced to the
        REST value types the type-sensitive consumer needs.

        uuid (``id`` / ``user_id``) → str and timestamptz (``created_at`` /
        ``updated_at``) → ISO str, matching PostgREST. ``settings_json`` (jsonb)
        is already a Python dict via the ORM — left as-is. ``download_path`` is
        already str. See the VALUE-TYPE PARITY note in the module docstring."""
        import datetime
        import uuid

        from app.models import UserSettings
        from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

        raw = _orm_obj_to_dict(obj, _name_to_attr(UserSettings))
        out: Dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, uuid.UUID):
                out[key] = str(value)
            elif isinstance(value, datetime.datetime):
                out[key] = value.isoformat()
            else:
                out[key] = value
        return out

    async def _load_user_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        """SELECT * via the ORM read_scope() session. Returns the same dict
        shape (and value types) the historical PostgREST path returned."""
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import UserSettings

        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(UserSettings).where(UserSettings.user_id == user_id).limit(1)
                )
                row = result.scalars().first()
                return self._row_to_rest_dict(row) if row else None
        except Exception as e:
            logger.error(f"获取用户设置失败: {e}")
            return None

    async def get_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return cached user settings (30s TTL) or load from DB on miss.

        Guards against ``user_id=None`` leaking in from Celery contexts
        where the original requester is unknown (e.g. retry of an orphan
        download row, system-initiated parses). Without the guard,
        PostgREST stringifies None to "None" and PG rejects with 22P02
        invalid uuid syntax — quietly turned into ERROR log spam without
        breaking the call. Returning None here is the same observable
        outcome (no settings found) without the noise.
        """
        if not user_id or str(user_id).lower() in ("none", "null"):
            return None
        return await user_settings_cache.get_or_load(
            user_id,
            lambda: self._load_user_settings(user_id),
        )

    async def upsert(
        self, user_id: str, settings: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        创建或更新用户设置

        ``settings_json`` is routed through ``patch_settings_json`` — the single
        merge path — so no caller can wholesale-replace the shared blob and
        clobber keys it did not intend to touch. Plain columns (download_path…)
        are upserted as-is, touching ONLY the named columns on conflict, so a
        ``download_path`` write never disturbs ``settings_json``.

        Args:
            user_id: 用户 ID
            settings: 设置数据

        Returns:
            更新后的设置数据
        """
        try:
            rest = dict(settings)
            settings_json = rest.pop("settings_json", None)

            result_row: Optional[Dict[str, Any]] = None

            # Plain columns (everything except settings_json) — upsert updates
            # only the named columns on conflict, leaving settings_json
            # untouched.
            if rest:
                result_row = await self._upsert_plain(user_id, rest)

            # settings_json — atomic top-level merge (race-free).
            if settings_json is not None:
                merged = await self.patch_settings_json(user_id, settings_json)
                result_row = merged or result_row

            user_settings_cache.invalidate(user_id)
            if result_row is not None:
                logger.info(f"用户设置已保存: user_id={user_id}")
            return result_row
        except Exception as e:
            logger.error(f"保存用户设置失败: {e}")
            return None

    async def _upsert_plain(
        self, user_id: str, plain: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """INSERT … ON CONFLICT (user_id) DO UPDATE SET <only the named plain
        columns> via the committing write_scope() session.

        Critically, the ON CONFLICT SET list contains ONLY the named plain
        columns (+ updated_at) — NOT settings_json — so an existing
        settings_json blob is left untouched (plain-vs-json isolation)."""
        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.session import write_scope
        from app.models import UserSettings

        values = {"user_id": user_id, **plain}
        update_set = {**plain, "updated_at": func.now()}
        stmt = (
            pg_insert(UserSettings)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[UserSettings.user_id],
                set_=update_set,
            )
            .returning(UserSettings)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
            return self._row_to_rest_dict(obj) if obj else None

    async def patch_settings_json(
        self, user_id: str, partial: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Merge ``partial`` top-level keys into ``settings_json``, atomically.

        THE canonical way to write ``user_settings.settings_json``. Pass only
        the keys you own (``{"parse_mode": ...}``, ``{"ai_settings": {...}}``);
        every other key is preserved.

        Uses a single ``INSERT … ON CONFLICT DO UPDATE SET settings_json =
        existing || patch`` so the merge happens inside one statement under a
        row lock — concurrent saves serialize and both patches survive, with no
        read-modify-write window. The statement runs inside the committing ORM
        ``write_scope()`` session.
        """
        partial = partial or {}
        row = await self._atomic_merge_settings_json(user_id, partial)
        user_settings_cache.invalidate(user_id)
        if row is not None:
            logger.info(f"用户设置已保存 (atomic merge): user_id={user_id}")
        return row

    # The merge SQL — runs inside the committing write_scope() session.
    # ``CAST(:patch AS jsonb)`` (not ``:patch::jsonb``) — SQLAlchemy's text()
    # bind parser eats a colon from ``::``. ``COALESCE(existing,'{}') || patch``
    # is the merge-not-replace guarantee (the #485 P0); it runs against the
    # locked, committed row inside ON CONFLICT DO UPDATE so it is race-free.
    _MERGE_SQL = (
        "INSERT INTO public.user_settings (user_id, settings_json) "
        "VALUES (:uid, CAST(:patch AS jsonb)) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "settings_json = COALESCE(public.user_settings.settings_json, '{}'::jsonb) "
        "|| CAST(:patch AS jsonb), "
        "updated_at = NOW() "
        "RETURNING *"
    )

    async def _atomic_merge_settings_json(
        self, user_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Single-statement jsonb merge inside the COMMITTING write_scope()
        session.

        Runs the ``settings_json = existing || patch`` ON CONFLICT statement —
        preserving the merge-not-replace guarantee. write_scope() COMMITs (or
        joins an ambient unit_of_work that commits), so the write actually
        persists."""
        import json

        from sqlalchemy import text

        from app.db.session import write_scope

        async with write_scope() as session:
            result = await session.execute(
                text(self._MERGE_SQL),
                {"uid": user_id, "patch": json.dumps(patch)},
            )
            row = result.mappings().first()
        return self._normalize_row(dict(row)) if row else None

    @staticmethod
    def _normalize_row(
        row: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Normalize a RETURNING * row to the REST-shaped dict callers expect.

        asyncpg returns ``jsonb`` as a JSON string — decode settings_json back
        to a dict. uuid (``id`` / ``user_id``) → str and timestamptz
        (``created_at`` / ``updated_at``) → ISO str so the value types match the
        PostgREST baseline the type-sensitive consumer (user_settings_router →
        Pydantic ``str`` fields) needs."""
        if not row:
            return row
        import datetime
        import json
        import uuid

        out: Dict[str, Any] = {}
        for key, value in row.items():
            if key == "settings_json" and isinstance(value, str):
                try:
                    out[key] = json.loads(value)
                except (ValueError, TypeError):
                    out[key] = value
            elif isinstance(value, uuid.UUID):
                out[key] = str(value)
            elif isinstance(value, datetime.datetime):
                out[key] = value.isoformat()
            else:
                out[key] = value
        return out

    async def delete(self, user_id: str) -> bool:
        """
        删除用户设置

        Core DELETE inside the committing ``write_scope()`` session (the last
        supabase-py ``.table()`` call in this repo — migrated to the ORM path).
        Same contract as the legacy REST delete: True on success (whether or
        not a row existed), False on error, never raises.

        Args:
            user_id: 用户 ID

        Returns:
            是否删除成功
        """
        try:
            from sqlalchemy import delete as sa_delete

            from app.db.session import write_scope
            from app.models import UserSettings

            async with write_scope() as session:
                await session.execute(
                    sa_delete(UserSettings).where(UserSettings.user_id == user_id)
                )
            user_settings_cache.invalidate(user_id)
            logger.info(f"用户设置已删除: user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"删除用户设置失败: {e}")
            return False
