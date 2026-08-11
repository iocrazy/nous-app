"""Repository for the ``canvases`` table (Phase 1 Day 2-3).

Pure data access. The optimistic-lock decision (409 vs. apply) lives in
``CanvasService`` so the repo can stay a thin wrapper around the queries.

ORM-model style (PR-7, the last repo of the convergence epic): statements run
on the canonical read_scope()/write_scope() sessions (app/db/session.py) with
the ``Canvases`` model. The legacy supabase-py service-role client is gone —
the SQLAlchemy engine connects with the same full-privilege role, which also
retires the historical "RLS via supabase-py + service_role on PUTs silently
returns NULL" hazard class this repo's client choice worked around.

Optimistic-lock semantics preserved EXACTLY (iron rule #3):

  * ``update_with_lock`` keeps the legacy TWO-transaction shape: txn 1 is the
    guarded UPDATE (``base_updated_at`` token compare + ``deleted_at IS
    NULL``); txn 2 echoes the trigger-bumped ``updated_at`` into
    ``base_updated_at``. Collapsing them into one transaction would change
    the token values (``now()`` is transaction-constant in PostgreSQL, so the
    trigger would stamp both steps with the same timestamp) — the legacy
    behaviour where ``updated_at`` ends up slightly ahead of
    ``base_updated_at`` is kept byte-identical instead.
  * 0 rows matched → ``None`` → the service raises ``CanvasConflict`` (409).
  * The timestamp source is ALWAYS the DB trigger's ``now()``
    (``trg_canvases_touch_updated_at``) — never computed in Python.
  * Timestamps serialize to ISO-8601 strings at the repo boundary, so the
    service's ``str(current.get("base_updated_at"))`` token round-trips
    losslessly back through ``datetime.fromisoformat`` at the bind.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, null, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from app.db.session import read_scope, write_scope
from app.models import Canvases, Projects


def _bigint(value: Any) -> int:
    """Coerce a snowflake string ID to an int for asyncpg bindings."""
    if isinstance(value, int):
        return value
    return int(str(value))


_JSONB_COLS = (
    "viewport_json",
    "nodes_json",
    "connections_json",
    "node_ops_json",
    "connection_ops_json",
)

_TS_COLS = ("created_at", "updated_at", "base_updated_at", "deleted_at")


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """Row → the REST-era dict shape consumers expect.

    * timestamptz → ISO-8601 string (the service compares/echoes tokens as
      strings; ``_tokens_match`` is format-tolerant but ``str(datetime)``
      would produce a space-separated non-ISO form — so isoformat here).
    * uuid (created_by) → str.
    * BIGINT ids stay native int (PostgREST returned JSON numbers;
      ``_to_response`` in the router stringifies for the frontend).
    * jsonb → parsed object (defensive json.loads when the driver hands
      back text).
    """
    out = dict(row)
    for key in _TS_COLS:
        value = out.get(key)
        if value is not None and hasattr(value, "isoformat"):
            out[key] = value.isoformat()
    created_by = out.get("created_by")
    if created_by is not None and not isinstance(created_by, str):
        out["created_by"] = str(created_by)
    for key in _JSONB_COLS:
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = json.loads(value)
            except (TypeError, ValueError):
                pass
    return out


def _lock_token(value: str) -> datetime:
    """Client/service-echoed ISO token → native datetime for the asyncpg bind
    (asyncpg strict: a timestamptz column never accepts a str bind)."""
    return datetime.fromisoformat(str(value))


class CanvasRepository:
    """CRUD for the ``canvases`` table (migration 280)."""

    TABLE = "canvases"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_id(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single canvas; returns ``None`` if not found."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Canvases.__table__)
                    .where(Canvases.id == _bigint(canvas_id))
                    .where(Canvases.deleted_at.is_(None))
                )
                row = result.mappings().first()
            return _serialize(dict(row)) if row else None
        except Exception as e:
            logger.error(f"canvas get_by_id({canvas_id}) failed: {e}")
            return None

    async def get_storyboard_canvas(
        self,
        project_id: str,
        episode_id: str,
        *,
        include_trashed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """The episode's system storyboard canvas (kind='storyboard'), or
        None. Backs the get-or-create endpoint's "get" half — matches the
        LIVE half of the partial unique index
        ``uq_canvases_storyboard_per_episode`` (mig 421/422: ``WHERE
        kind='storyboard' AND deleted_at IS NULL``) exactly, so this is the
        single row a concurrent create could race against.

        ``include_trashed=True`` drops the ``deleted_at IS NULL`` filter —
        used by ``create_storyboard_canvas`` to find a SOFT-DELETED
        storyboard row after a 23505 conflict (mig 422 fix: a generic
        ``DELETE /canvases/{id}`` soft-deletes without knowing about
        ``kind``, so the conflict can be against a trashed row, not a live
        one)."""
        try:
            async with read_scope() as session:
                stmt = (
                    select(Canvases.__table__)
                    .where(Canvases.project_id == _bigint(project_id))
                    .where(Canvases.episode_id == _bigint(episode_id))
                    .where(Canvases.kind == "storyboard")
                )
                if not include_trashed:
                    stmt = stmt.where(Canvases.deleted_at.is_(None))
                result = await session.execute(stmt)
                row = result.mappings().first()
            return _serialize(dict(row)) if row else None
        except Exception as e:
            logger.error(
                f"canvas get_storyboard_canvas(project={project_id}, "
                f"episode={episode_id}, include_trashed={include_trashed}) "
                f"failed: {e}"
            )
            return None

    async def list_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """All canvases belonging to a project, newest-edited first.

        SUMMARY columns only — every consumer renders cards, and dragging
        nodes_json/ops back for a list was exactly the payload problem the
        team-tree endpoint was built to kill (G4 review follow-up)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Canvases.id,
                        Canvases.project_id,
                        Canvases.name,
                        Canvases.kind,
                        Canvases.created_at,
                        Canvases.updated_at,
                    )
                    .where(Canvases.project_id == _bigint(project_id))
                    .where(Canvases.deleted_at.is_(None))
                    .order_by(Canvases.updated_at.desc())
                )
                rows = result.mappings().all()
            return [_serialize(dict(r)) for r in rows]
        except Exception as e:
            logger.error(f"canvas list_for_project({project_id}) failed: {e}")
            return []

    async def list_trashed_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """A project's trashed canvases (summary columns), newest-trashed
        first — the project-scoped mirror of list_trashed_for_team, used by
        the workspace Trash module (avoids the team-id sentinel)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Canvases.id,
                        Canvases.project_id,
                        Canvases.name,
                        Canvases.kind,
                        Canvases.updated_at,
                        Canvases.deleted_at,
                    )
                    .where(Canvases.project_id == _bigint(project_id))
                    .where(Canvases.deleted_at.is_not(None))
                    .order_by(Canvases.deleted_at.desc())
                )
                rows = result.mappings().all()
            return [_serialize(dict(r)) for r in rows]
        except Exception as e:
            logger.error(f"canvas list_trashed_for_project({project_id}) failed: {e}")
            return []

    async def list_team_tree(self, team_id: str) -> List[Dict[str, Any]]:
        """Every project in the team with its canvases embedded as SUMMARY
        columns (no nodes_json/connections_json — the landing page renders
        cards, not documents). Projects without canvases are included so
        the canvas list can offer "New Canvas" on them. One query — this
        replaces the frontend's fetchProjects + per-project listCanvases
        N+1 fan-out (G4 review follow-up).

        LEFT JOIN + Python regroup reproduces the PostgREST resource-embed
        shape: ``[{id, name, canvases: [{id, name, kind, updated_at}]}]``,
        projects ordered by created_at desc, each embed ordered by
        updated_at desc, live canvases only."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Projects.id.label("project_id"),
                        Projects.name.label("project_name"),
                        Canvases.id.label("canvas_id"),
                        Canvases.name.label("canvas_name"),
                        Canvases.kind.label("canvas_kind"),
                        Canvases.updated_at.label("canvas_updated_at"),
                    )
                    .select_from(Projects)
                    .outerjoin(
                        Canvases,
                        (Canvases.project_id == Projects.id)
                        & (Canvases.deleted_at.is_(None)),
                    )
                    .where(Projects.team_id == _bigint(team_id))
                    .order_by(
                        Projects.created_at.desc(),
                        Canvases.updated_at.desc(),
                    )
                )
                rows = result.mappings().all()
            tree: List[Dict[str, Any]] = []
            by_project: Dict[Any, Dict[str, Any]] = {}
            for r in rows:
                pid = r["project_id"]
                entry = by_project.get(pid)
                if entry is None:
                    entry = {"id": pid, "name": r["project_name"], "canvases": []}
                    by_project[pid] = entry
                    tree.append(entry)
                if r["canvas_id"] is not None:
                    entry["canvases"].append(
                        _serialize(
                            {
                                "id": r["canvas_id"],
                                "name": r["canvas_name"],
                                "kind": r["canvas_kind"],
                                "updated_at": r["canvas_updated_at"],
                            }
                        )
                    )
            return tree
        except Exception as e:
            logger.error(f"canvas list_team_tree({team_id}) failed: {e}")
            return []

    async def list_recent_for_user(
        self, user_id: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Recently-edited LIVE canvases across every project OWNED by
        ``user_id``, newest-edited first, capped at ``limit``.

        Joins ``projects`` for owner scoping (mirrors the projects-list
        endpoint's ``owner_id == user_id`` visibility), the project name, AND
        the project's ``team_id`` — the Recent view spans EVERY team the
        caller owns projects in, not just the team currently open in the UI,
        so each item must carry its OWN team so the frontend can navigate to
        it directly instead of assuming "current page's team" (cross-team
        recent items were silently unopenable before this field existed).
        One query. Returns the recent-items wire shape
        ``{id, name, project_id, project_name, team_id, updated_at}`` with
        bigint ids stringified (``team_id`` is ``None`` for a personal
        project with no team). Never raises — degrades the Recent view to
        empty."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Canvases.id,
                        Canvases.name,
                        Canvases.project_id,
                        Canvases.updated_at,
                        Projects.name.label("project_name"),
                        Projects.team_id,
                    )
                    .join(Projects, Projects.id == Canvases.project_id)
                    .where(Projects.owner_id == user_id)
                    .where(Canvases.deleted_at.is_(None))
                    .order_by(Canvases.updated_at.desc())
                    .limit(limit)
                )
                rows = result.mappings().all()
            return [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "project_id": str(r["project_id"]),
                    "project_name": r["project_name"],
                    "team_id": (
                        str(r["team_id"]) if r["team_id"] is not None else None
                    ),
                    "updated_at": (
                        r["updated_at"].isoformat() if r["updated_at"] else None
                    ),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"canvas list_recent_for_user({user_id}) failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        project_id: str,
        name: str,
        kind: str,
        created_by: Optional[str],
        viewport_json: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert a new canvas; defaults flow from the column DEFAULTs
        when fields are omitted."""
        payload: Dict[str, Any] = {
            "project_id": _bigint(project_id),
            "name": name,
            "kind": kind,
        }
        if created_by is not None:
            payload["created_by"] = created_by
        if viewport_json is not None:
            payload["viewport_json"] = viewport_json
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Canvases).values(**payload).returning(Canvases.__table__)
                )
                row = result.mappings().first()
            return _serialize(dict(row)) if row else None
        except Exception as e:
            logger.error(f"canvas create for project {project_id} failed: {e}")
            return None

    async def create_storyboard_canvas(
        self,
        *,
        project_id: str,
        episode_id: str,
        name: str,
        created_by: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Insert the episode's system storyboard canvas (kind='storyboard').

        Idempotence is NOT decided here — it is the DB's job via the partial
        unique index ``uq_canvases_storyboard_per_episode`` (mig 421/422) on
        ``(project_id, episode_id) WHERE kind='storyboard' AND deleted_at IS
        NULL``. Two concurrent GET /canvases/storyboard requests can both
        pass the router's get-then-create race window; whichever INSERT
        loses hits a 23505 IntegrityError here, which we catch and resolve
        by re-reading the row the winner just created — never a second row,
        never a 500.

        mig 422 self-heal: the 23505 can ALSO be against a SOFT-DELETED
        storyboard row — a generic ``DELETE /canvases/{id}`` (no ``kind``
        awareness) soft-deletes it, but the index still reserves the
        (project_id, episode_id) slot as long as the row exists. A re-read
        with the live-only filter finds nothing, which used to surface as a
        permanent 500 for that episode. Here we fall back to a
        ``include_trashed=True`` lookup and, if that finds the row, RESTORE
        it (clears ``deleted_at`` — same semantics as the trash-restore
        endpoint) instead of leaving the episode's storyboard canvas dead.
        """
        payload: Dict[str, Any] = {
            "project_id": _bigint(project_id),
            "episode_id": _bigint(episode_id),
            "name": name,
            "kind": "storyboard",
        }
        if created_by is not None:
            payload["created_by"] = created_by
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Canvases).values(**payload).returning(Canvases.__table__)
                )
                row = result.mappings().first()
            return _serialize(dict(row)) if row else None
        except IntegrityError as exc:
            pgcode = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if pgcode == "23505" or "23505" in str(getattr(exc, "orig", exc)):
                live = await self.get_storyboard_canvas(project_id, episode_id)
                if live is not None:
                    return live
                trashed = await self.get_storyboard_canvas(
                    project_id, episode_id, include_trashed=True
                )
                if trashed is not None:
                    await self.restore(trashed["id"])
                    return await self.get_storyboard_canvas(project_id, episode_id)
                # Conflict but neither a live nor a trashed row is visible —
                # can't self-heal past that; let the caller see None like
                # the pre-existing failure path below.
                logger.error(
                    f"canvas create_storyboard_canvas(project={project_id}, "
                    f"episode={episode_id}): 23505 but no row found to "
                    "resolve (live or trashed)"
                )
                return None
            logger.error(
                f"canvas create_storyboard_canvas(project={project_id}, "
                f"episode={episode_id}) failed: {exc}"
            )
            raise
        except Exception as e:
            logger.error(
                f"canvas create_storyboard_canvas(project={project_id}, "
                f"episode={episode_id}) failed: {e}"
            )
            return None

    async def update_with_lock(
        self,
        canvas_id: str,
        *,
        expected_base_updated_at: str,
        fields: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Apply an optimistic-locked update.

        Returns the new row when ``base_updated_at`` matches and the row
        was updated; returns ``None`` when the token didn't match (the
        caller treats that as a conflict).

        Two transactions on purpose (legacy parity — see module docstring):
        the guarded UPDATE commits first (its trigger bumps ``updated_at``
        to that transaction's ``now()``), then a second transaction echoes
        that value into ``base_updated_at`` for the client's next PUT.
        """
        if not fields:
            # No mutations requested → just verify the lock and return
            # the row unchanged.
            current = await self.get_by_id(canvas_id)
            if current is None:
                return None
            if str(current.get("base_updated_at")) != str(expected_base_updated_at):
                return None
            return current

        try:
            # Step 1: the guarded UPDATE — atomic at the SQL layer: only a
            # row still holding the expected token is updated.
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(Canvases)
                    .where(Canvases.id == _bigint(canvas_id))
                    .where(
                        Canvases.base_updated_at
                        == _lock_token(expected_base_updated_at)
                    )
                    # A zombie tab must not autosave into a trashed document —
                    # soft delete doesn't rotate the lock token, so the token
                    # guard alone would still match.
                    .where(Canvases.deleted_at.is_(None))
                    .values(**fields)
                    .returning(Canvases.__table__)
                )
                updated = result.mappings().first()
            if updated is None:
                return None
            updated_row = _serialize(dict(updated))
            # Step 2: bump base_updated_at to the now-fresh updated_at so the
            # client's next PUT can use it.
            new_token = updated.get("updated_at")
            if new_token is not None:
                async with write_scope() as session:
                    bump = await session.execute(
                        sa_update(Canvases)
                        .where(Canvases.id == _bigint(canvas_id))
                        .values(base_updated_at=new_token)
                        .returning(Canvases.__table__)
                    )
                    bumped = bump.mappings().first()
                if bumped is not None:
                    return _serialize(dict(bumped))
            return updated_row
        except Exception as e:
            logger.error(f"canvas update_with_lock({canvas_id}) failed: {e}")
            return None

    async def patch_node_run_results(
        self,
        canvas_id: str,
        results_by_node_id: Dict[str, Dict[str, Any]],
    ) -> bool:
        """Merge run_result/run_status into nodes_json for the given nodes.

        Read-modify-write WITHOUT an optimistic lock, so the DBOS graph
        workflow can persist per-node outputs without holding the user's
        lock token across steps.

        Only the keys supplied in ``results_by_node_id`` (e.g. ``run_result``,
        ``run_status``) are written into ``node.data``; all other node fields
        (position, type, other data keys, connections) are left intact.

        base_updated_at advance (Phase 6a realtime closure):
          ``base_updated_at`` is the optimistic-lock token AND the staleness
          guard the frontend's ``applyRemoteUpdate`` uses to drop self-echo /
          stale realtime events (``if row.base_updated_at <= s.baseUpdatedAt
          return``).  A nodes_json write that does NOT advance the token would
          be seen as STALE by every open tab → the persisted run_result would
          never surface via realtime, defeating the purpose of persisting it.
          So, mirroring ``update_with_lock``, we stamp base_updated_at to the
          freshly-bumped updated_at: the UPDATE writing nodes_json fires the
          ``trg_canvases_touch_updated_at`` trigger (bumps updated_at SQL-side
          via now()), then a second UPDATE echoes that new updated_at into
          base_updated_at.  The timestamp source is the DB trigger's now() — it
          is never computed in Python.

        Clobber-safety note (M4a documented limitation):
          The concurrent user PUT path (``update_with_lock``) replaces
          ``nodes_json`` wholesale.  If a user saves the canvas while the
          workflow is mid-run, the user save and the workflow write race.
          Whichever write lands second wins at the row level, so ``run_result``
          fields written by the workflow may be overwritten by a concurrent user
          save, or vice-versa.  For the M4a scope this is acceptable: the
          frontend relies on Phase 6a realtime broadcasts which will deliver the
          most-recently persisted row.  A user with unsaved local edits gets the
          existing 409 conflict path on their next PUT (their base_updated_at no
          longer matches) — correct last-writer-wins behaviour for v1.  M4b may
          address with a PostgreSQL jsonb path-update to make the write truly
          non-destructive.
        """
        row = await self.get_by_id(canvas_id)
        if row is None:
            logger.warning(
                f"canvas patch_node_run_results: canvas {canvas_id!r} not found"
            )
            return False

        nodes_raw = row.get("nodes_json")
        nodes_list: List[Any] = list(nodes_raw) if isinstance(nodes_raw, list) else []

        patched: List[Any] = []
        for node in nodes_list:
            if not isinstance(node, dict):
                patched.append(node)
                continue
            node_id = node.get("id")
            if node_id in results_by_node_id:
                node_data = dict(node.get("data") or {})
                node_data.update(results_by_node_id[node_id])
                patched.append({**node, "data": node_data})
            else:
                patched.append(node)

        try:
            # Step 1: write nodes_json.  This fires trg_canvases_touch_updated_at
            # which bumps updated_at to now() at the SQL layer.
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(Canvases)
                    .where(Canvases.id == _bigint(canvas_id))
                    .values(nodes_json=patched)
                    .returning(Canvases.updated_at)
                )
                new_token = result.scalar()
            if new_token is None:
                return False
            # Step 2: advance base_updated_at to the freshly-bumped updated_at so
            # the realtime event is recognised as NEWER by open tabs (Phase 6a
            # applyRemoteUpdate staleness guard).  Same SQL-side timestamp the
            # user save path echoes — never a Python-computed value.
            async with write_scope() as session:
                await session.execute(
                    sa_update(Canvases)
                    .where(Canvases.id == _bigint(canvas_id))
                    .values(base_updated_at=new_token)
                )
            return True
        except Exception as e:
            logger.error(f"canvas patch_node_run_results({canvas_id}) failed: {e}")
            return False

    async def soft_delete(self, canvas_id: str) -> bool:
        """Move a live canvas to the trash (G9). Idempotence guard: already-
        trashed rows don't match, so a double DELETE reads as 404."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(Canvases)
                    .where(Canvases.id == _bigint(canvas_id))
                    .where(Canvases.deleted_at.is_(None))
                    .values(deleted_at=datetime.now(timezone.utc))
                )
                return bool(result.rowcount)
        except Exception as e:
            logger.error(f"canvas soft_delete({canvas_id}) failed: {e}")
            return False

    async def restore(self, canvas_id: str) -> bool:
        """Bring a trashed canvas back to life."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(Canvases)
                    .where(Canvases.id == _bigint(canvas_id))
                    .where(Canvases.deleted_at.is_not(None))
                    .values(deleted_at=null())
                )
                return bool(result.rowcount)
        except Exception as e:
            logger.error(f"canvas restore({canvas_id}) failed: {e}")
            return False

    async def purge(self, canvas_id: str) -> bool:
        """Permanently delete — but ONLY from the trash. A live canvas must
        be soft-deleted first; that keeps a single accidental call from
        destroying data."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_delete(Canvases)
                    .where(Canvases.id == _bigint(canvas_id))
                    .where(Canvases.deleted_at.is_not(None))
                )
                return bool(result.rowcount)
        except Exception as e:
            logger.error(f"canvas purge({canvas_id}) failed: {e}")
            return False

    async def list_trashed_for_team(self, team_id: str) -> List[Dict[str, Any]]:
        """A team's trashed canvases (summary columns + owning project),
        newest-trashed first. INNER JOIN reproduces the PostgREST
        ``projects!inner(team_id, name)`` embed shape."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Canvases.id,
                        Canvases.name,
                        Canvases.kind,
                        Canvases.updated_at,
                        Canvases.deleted_at,
                        Canvases.project_id,
                        Projects.team_id.label("_project_team_id"),
                        Projects.name.label("_project_name"),
                    )
                    .join(Projects, Projects.id == Canvases.project_id)
                    .where(Projects.team_id == _bigint(team_id))
                    .where(Canvases.deleted_at.is_not(None))
                    .order_by(Canvases.deleted_at.desc())
                )
                rows = result.mappings().all()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                team = d.pop("_project_team_id")
                pname = d.pop("_project_name")
                shaped = _serialize(d)
                shaped["projects"] = {"team_id": team, "name": pname}
                out.append(shaped)
            return out
        except Exception as e:
            logger.error(f"canvas list_trashed_for_team({team_id}) failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Helpers used by access checks
    # ------------------------------------------------------------------

    async def get_project_id(self, canvas_id: str) -> Optional[str]:
        # MUST NOT filter deleted_at — the restore/purge write gates depend
        # on resolving TRASHED canvases; adding the filter here would turn
        # every restore into a 404.
        """Cheap project lookup for membership checks (skips the JSONB)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Canvases.project_id).where(Canvases.id == _bigint(canvas_id))
                )
                value = result.scalar()
            return str(value) if value is not None else None
        except Exception as e:
            logger.error(f"canvas get_project_id({canvas_id}) failed: {e}")
            return None
