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

# Roster grouping for the AI Library gallery (mig 400, spec 2026-08-02 §B1).
# Keys are seed DIRECTORY names — note the mixed hyphen/underscore style is
# the real on-disk state, not a typo. tests/test_agent_group_seed.py asserts
# this map and backend/seeds/agents/ stay in lockstep.
DEFAULT_AGENT_GROUP = "tools"
AGENT_GROUP_BY_SLUG = {
    "script_ai": "writing",
    "storyboard": "writing",
    "summarize": "writing",
    "character-expression": "art",
    "character-persona": "art",
    "character-portrait": "art",
    "character-turnaround": "art",
    "location-design": "art",
    "location-visual": "art",
    "prop-design": "art",
    "prop-visual": "art",
    "analyze": "tools",
    "caption": "tools",
    "classify": "tools",
    "coordinator": "tools",
    "topic-scorer": "tools",
    "translate": "tools",
}


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


def _extract_description_from_identity(identity_md: Optional[str]) -> Optional[str]:
    """Derive a UI description from an agent's IDENTITY.md first line.

    Convention used by all preset IDENTITY files: the opening sentence
    reads ``I am the MediaHub <Name> AI — <one-line role>.`` We take the
    text after the em-dash (or en-dash / hyphen fallback) and strip a
    trailing period. Returns None when no separator is present so we
    surface a missing description rather than dumping the full first line.
    """
    if not identity_md:
        return None
    first_line = identity_md.strip().split("\n", 1)[0].strip()
    for sep in (" — ", " – ", " - "):
        if sep in first_line:
            return first_line.split(sep, 1)[1].strip().rstrip(".")
    return None


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
        # Hash-skip vs real-upsert tally — surfaced in load_all() result so
        # ops can confirm "no changes detected" runs are doing the cheap
        # path, not silently re-PATCHing every entity. Initialised in
        # __init__ (not just load_all) so direct callers of _load_skills /
        # _load_skill_subfiles in unit tests don't trip AttributeError.
        self._skipped: dict[str, int] = {"agents": 0, "skills": 0, "skill_files": 0}
        self._upserted: dict[str, int] = {"agents": 0, "skills": 0, "skill_files": 0}

    def _reset_counters(self) -> None:
        self._skipped = {"agents": 0, "skills": 0, "skill_files": 0}
        self._upserted = {"agents": 0, "skills": 0, "skill_files": 0}

    async def load_all(self) -> dict[str, Any]:
        self._reset_counters()
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

        identity_md = read_if_exists("IDENTITY.md")
        agent_md, agent_fm = self._read_agent_md(agent_dir)
        name = slug.replace("_", " ").title()
        return {
            "slug": slug,
            "name": name,
            "description": _extract_description_from_identity(identity_md),
            "identity_md": identity_md,
            "soul_md": read_if_exists("SOUL.md"),
            "agent_md": agent_md,
            "is_system_preset": True,
            "agent_group": AGENT_GROUP_BY_SLUG.get(slug, DEFAULT_AGENT_GROUP),
            # Workforce worker (M3 Delegate target). Declared by the seed, not
            # by a migration: migrations 162/163 promoted summarize / analyze /
            # coordinator, then sank below the schema baseline watermark and
            # never ran on the live database — so production sat at ZERO
            # persistent agents and every Delegate call was refused (Task 7a
            # defect 4). Upserting it on every startup is what makes a fresh
            # deploy converge without a new migration.
            "persistent": bool(agent_fm.get("persistent", False)),
        }

    @staticmethod
    def _read_agent_md(agent_dir: Path) -> tuple[Optional[str], dict[str, Any]]:
        """``(body, frontmatter)`` for AGENT.md — the body WITHOUT the
        frontmatter block.

        ``agent_md`` is model-visible: it is pasted into the system message, so
        a raw YAML header there would be a prompt change wearing a
        configuration hat. Seeds that declare nothing parse to an empty dict
        and a byte-identical body (verified across all 17 seeds), so this is
        the same content the loader wrote before.
        """
        path = agent_dir / "AGENT.md"
        if not path.exists():
            return (None, {})
        post = frontmatter.load(path)
        body = (post.content or "").strip()
        return (body or None, dict(post.metadata))

    @staticmethod
    def _agent_seed_hash(fields: dict[str, Any]) -> str:
        """Hash of everything the seed OWNS on an agent row.

        Anything the loader writes must be in here. A field that is written
        but not hashed lands only on rows that changed for some other reason:
        flipping it alone hits the unchanged-skip branch in ``_upsert_agent``
        and never reaches the database.
        """
        return _sha(
            fields.get("identity_md"),
            fields.get("soul_md"),
            fields.get("agent_md"),
            fields.get("name"),
            fields.get("description"),
            # Must be hashed: without it, re-grouping an agent hits the skip
            # branch and the new group never reaches the DB.
            fields.get("agent_group"),
            # Same reason, and it is the whole point of defect 4: production
            # rows already carry the right prose, so ONLY this flag differs.
            fields.get("persistent"),
        )

    async def _upsert_agent(self, slug: str, fields: dict[str, Any]) -> None:
        seed_hash = self._agent_seed_hash(fields)
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
        # ORM insert via the repo — AgentRepository lost `_get_client` in
        # #959 (ORM-only collapse); the old supabase-py reach-in silently
        # broke every NEW preset seed (existing agents take the update
        # path, so nothing failed until the 8 CC presets landed).
        await self.agent_repo.insert(fields)

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
                    # Dual-write legacy content_md so the /api/v1/skills readers
                    # see the body on preset inserts too. Migration 152 dropped
                    # NOT NULL; this keeps data in sync.
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
        # ORM insert via the repo (same class of drift as _insert_agent_row —
        # never reach into a repo's private client for writes).
        row = await self.skill_repo.insert(fields_with_hash)
        skill_id = int(row["id"])
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
