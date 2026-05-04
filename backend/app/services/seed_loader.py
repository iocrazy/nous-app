"""Load agent & skill seed content from backend/seeds/ into DB (idempotent).

Idempotency
-----------
Each row stores a ``seed_hash`` (sha256 of canonical content; see
mig 199). On startup the loader recomputes the hash for what's on disk
and skips the PATCH when the DB hash matches. Steady-state cost is
~5 GETs (no writes), so cold start is sub-second after the first run.

A NULL hash on the DB side (or our side) falls through to the existing
upsert path, so the migration is safe to apply before deploying this
loader change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import frontmatter
from loguru import logger

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository

SCRIPT_EXTS = {".py", ".sh", ".js", ".ts"}
TEXT_ASSET_EXTS = {".json", ".yaml", ".yml", ".txt"}
SCRIPT_AI_SKILL_SLUGS = ["script-outline", "script-expand", "script-branch"]


def _sha(*chunks: Any) -> str:
    """sha256 of pipe-joined str(chunk). NULL → empty so identical content
    with absent vs empty fields hashes the same."""
    h = hashlib.sha256()
    for c in chunks:
        if c is None:
            h.update(b"")
        elif isinstance(c, (dict, list)):
            h.update(json.dumps(c, sort_keys=True, ensure_ascii=False).encode())
        else:
            h.update(str(c).encode())
        h.update(b"|")
    return h.hexdigest()


def _format_error(exc: BaseException) -> dict[str, Any]:
    """Extract structured fields from a Supabase/postgrest/httpx exception.

    Loguru `logger.exception` already captures the traceback; this helper
    pulls out the diagnostic bits that a log-grep can match on:
    Postgres SQLSTATE code, HTTP status, hint, details.

    Safe on any Exception subclass — missing attrs are simply omitted.
    """
    info: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
    }
    for attr in ("code", "details", "hint"):
        v = getattr(exc, attr, None)
        if v is not None:
            info[attr] = v
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            info["status"] = status
    return info


class SeedLoader:
    """Idempotently load seed markdown from filesystem into the AI Library tables."""

    def __init__(
        self,
        agent_repo: AgentRepository,
        skill_repo: SkillRepository,
        seeds_root: Path,
    ) -> None:
        self.agent_repo = agent_repo
        self.skill_repo = skill_repo
        self.seeds_root = seeds_root

    def __post_init_counters__(self) -> None:
        # Hash-skip vs real-upsert tally — surfaced in load_all() result so
        # ops can confirm "no changes detected" runs are doing the cheap
        # path, not silently re-PATCHing every entity.
        self._skipped: dict[str, int] = {"agents": 0, "skills": 0, "skill_files": 0}
        self._upserted: dict[str, int] = {"agents": 0, "skills": 0, "skill_files": 0}

    async def load_all(self) -> dict[str, Any]:
        self.__post_init_counters__()
        errors: list[dict[str, Any]] = []
        agents_loaded = await self._load_agents(errors)
        skills_loaded = await self._load_skills(errors)
        bindings_set = await self._bind_script_ai_skills(errors)
        return {
            "agents": agents_loaded,
            "skills": skills_loaded,
            "agent_skill_bindings": bindings_set,
            "skipped": self._skipped,
            "upserted": self._upserted,
            "errors": errors,
        }

    # ---------- agents ----------

    async def _load_agents(self, errors: list[dict[str, Any]]) -> int:
        agents_dir = self.seeds_root / "agents"
        if not agents_dir.exists():
            logger.warning(f"seed_loader: agents dir missing ({agents_dir})")
            return 0
        count = 0
        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            slug = agent_dir.name
            try:
                fields = self._read_agent_fields(agent_dir, slug)
                await self._upsert_agent(slug, fields)
                count += 1
            # Broad catch intentional: one bad seed must not abort the batch.
            except Exception as e:
                err = {"scope": "agent", "slug": slug, "error": _format_error(e)}
                errors.append(err)
                logger.exception(f"seed_loader: agent '{slug}' failed: {err['error']}")
        return count

    def _read_agent_fields(self, agent_dir: Path, slug: str) -> dict[str, Any]:
        def read_if_exists(name: str) -> Optional[str]:
            p = agent_dir / name
            if p.exists():
                txt = p.read_text().strip()
                return txt or None
            return None

        name = slug.replace("_", " ").title()
        return {
            "slug": slug,
            "name": name,
            "identity_md": read_if_exists("IDENTITY.md"),
            "soul_md": read_if_exists("SOUL.md"),
            "agent_md": read_if_exists("AGENT.md"),
            "is_system_preset": True,
        }

    async def _upsert_agent(self, slug: str, fields: dict[str, Any]) -> None:
        seed_hash = _sha(
            fields.get("identity_md"),
            fields.get("soul_md"),
            fields.get("agent_md"),
            fields.get("name"),
        )
        fields_with_hash = {**fields, "seed_hash": seed_hash}

        existing = await self.agent_repo.get_by_slug(slug)
        if existing:
            if existing.get("seed_hash") == seed_hash:
                self._skipped["agents"] += 1
                logger.debug(f"seed_loader: agent {slug} unchanged (skip)")
                return
            update_fields = {k: v for k, v in fields_with_hash.items() if k != "slug"}
            await self.agent_repo.update_fields(UUID(existing["id"]), update_fields)
            self._upserted["agents"] += 1
            logger.info(f"seed_loader: updated agent {slug}")
        else:
            await self._insert_agent_row(fields_with_hash)
            self._upserted["agents"] += 1
            logger.info(f"seed_loader: inserted agent {slug}")

    async def _insert_agent_row(self, fields: dict[str, Any]) -> None:
        client = await self.agent_repo._get_client()
        await client.table("ai_agents").insert(fields).execute()

    # ---------- skills ----------

    async def _load_skills(self, errors: list[dict[str, Any]]) -> int:
        skills_dir = self.seeds_root / "skills"
        if not skills_dir.exists():
            logger.warning(f"seed_loader: skills dir missing ({skills_dir})")
            return 0
        count = 0
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            slug = skill_dir.name
            try:
                skill_md_path = skill_dir / "SKILL.md"
                if not skill_md_path.exists():
                    logger.warning(f"seed_loader: skipping {slug} (no SKILL.md)")
                    continue
                parsed = frontmatter.load(skill_md_path)
                fm = dict(parsed.metadata)
                body = parsed.content

                fields = {
                    "slug": slug,
                    "name": fm.get("name", slug),
                    "description": fm.get("description"),
                    "body_md": body,
                    # Dual-write legacy content_md so storyboard_ai_service and
                    # /api/v1/skills readers see the body on preset inserts too.
                    # Migration 152 dropped NOT NULL; this keeps data in sync.
                    "content_md": body,
                    "category": fm.get("category"),
                    "icon": fm.get("icon", "✨"),
                    "is_public": fm.get("is_public", True),
                    "frontmatter_json": fm,
                    "status": "active",
                }

                skill_id = await self._upsert_skill(slug, fields)
                await self._load_skill_subfiles(skill_id, skill_dir)
                count += 1
            # Broad catch intentional: one bad seed must not abort the batch.
            except Exception as e:
                err = {"scope": "skill", "slug": slug, "error": _format_error(e)}
                errors.append(err)
                logger.exception(f"seed_loader: skill '{slug}' failed: {err['error']}")
        return count

    async def _upsert_skill(self, slug: str, fields: dict[str, Any]) -> int:
        seed_hash = _sha(
            fields.get("body_md"),
            fields.get("frontmatter_json"),
            fields.get("name"),
            fields.get("description"),
        )
        fields_with_hash = {**fields, "seed_hash": seed_hash}

        existing = await self.skill_repo.get_by_slug(slug)
        if existing:
            skill_id = int(existing["id"])
            if existing.get("seed_hash") == seed_hash:
                self._skipped["skills"] += 1
                logger.debug(f"seed_loader: skill {slug} unchanged (skip)")
                return skill_id
            update_fields = {k: v for k, v in fields_with_hash.items() if k != "slug"}
            await self.skill_repo.update_fields(skill_id, update_fields)
            self._upserted["skills"] += 1
            logger.info(f"seed_loader: updated skill {slug} (id={skill_id})")
            return skill_id
        client = await self.skill_repo._get_client()
        resp = await client.table("skills").insert(fields_with_hash).execute()
        skill_id = int(resp.data[0]["id"])
        self._upserted["skills"] += 1
        logger.info(f"seed_loader: inserted skill {slug} (id={skill_id})")
        return skill_id

    async def _load_skill_subfiles(self, skill_id: int, skill_dir: Path) -> None:
        """Upsert every text sub-file from disk, then delete DB rows that
        disappeared from disk.

        Without the reconcile step, deleting a ``references/old.md`` file
        and redeploying leaves a stale ``skill_files`` row forever — the
        agent keeps seeing it in the file tree and can still fetch its
        content. The reconcile pass scans the DB and removes anything not
        present on disk this startup.

        Note we only reconcile the ``references/``, ``scripts/``,
        ``assets/`` subtrees; the top-level ``SKILL.md`` lives on
        ``skills.body_md``, not ``skill_files``.
        """
        # Pre-load existing files keyed by path so we can hash-skip
        # unchanged ones without an extra round-trip per file.
        existing_by_path: dict[str, dict[str, Any]] = {}
        try:
            for row in await self.skill_repo.list_files(skill_id):
                p = row.get("path")
                if isinstance(p, str):
                    existing_by_path[p] = row
        except Exception as e:
            logger.warning(f"seed_loader: prefetch skill_files failed: {e}")

        disk_paths: set[str] = set()
        for sub_path in ("references", "scripts", "assets"):
            sub_dir = skill_dir / sub_path
            if not sub_dir.exists():
                continue
            for f in sorted(sub_dir.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(skill_dir).as_posix()
                ext = f.suffix.lower()
                if ext == ".md":
                    file_type = "markdown"
                elif ext in SCRIPT_EXTS:
                    file_type = "script"
                elif ext in TEXT_ASSET_EXTS:
                    file_type = "text-asset"
                else:
                    logger.debug(f"seed_loader: skipping binary/unknown {rel}")
                    continue
                try:
                    content = f.read_text()
                except UnicodeDecodeError:
                    logger.warning(f"seed_loader: cannot read {rel} as text, skipping")
                    continue

                file_hash = _sha(content, file_type)
                prev = existing_by_path.get(rel)
                if prev is not None and prev.get("seed_hash") == file_hash:
                    self._skipped["skill_files"] += 1
                    disk_paths.add(rel)
                    continue
                await self.skill_repo.upsert_file(
                    skill_id,
                    path=rel,
                    content=content,
                    file_type=file_type,
                    seed_hash=file_hash,
                )
                self._upserted["skill_files"] += 1
                disk_paths.add(rel)

        # Reconcile: remove DB rows whose source file vanished from disk.
        # We only touch paths under the managed subtrees to avoid nuking
        # hand-authored skill files someone may have added via the UI.
        existing = await self.skill_repo.list_files(skill_id)
        managed_prefixes = ("references/", "scripts/", "assets/")
        for row in existing:
            row_path = row.get("path") or ""
            if not row_path.startswith(managed_prefixes):
                continue
            if row_path in disk_paths:
                continue
            try:
                await self.skill_repo.delete_file(skill_id, row_path)
                logger.info(
                    f"seed_loader: removed orphan skill_file (skill_id={skill_id}, "
                    f"path={row_path!r})"
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    f"seed_loader: could not remove orphan {row_path!r} for "
                    f"skill_id={skill_id}: {exc}"
                )

    # ---------- bindings ----------

    async def _bind_script_ai_skills(self, errors: list[dict[str, Any]]) -> int:
        try:
            agent = await self.agent_repo.get_by_slug("script_ai")
            if not agent:
                logger.info("seed_loader: script_ai agent not found, skipping bindings")
                return 0
            skill_ids: list[int] = []
            for slug in SCRIPT_AI_SKILL_SLUGS:
                sk = await self.skill_repo.get_by_slug(slug)
                if sk:
                    skill_ids.append(int(sk["id"]))
            await self.agent_repo.update_skill_bindings(UUID(agent["id"]), skill_ids)
            logger.info(f"seed_loader: bound {len(skill_ids)} skills to script_ai")
            return len(skill_ids)
        # Broad catch intentional: one bad seed must not abort the batch.
        except Exception as e:
            err = {"scope": "binding", "slug": "script_ai", "error": _format_error(e)}
            errors.append(err)
            logger.exception(f"seed_loader: script_ai binding failed: {err['error']}")
            return 0
