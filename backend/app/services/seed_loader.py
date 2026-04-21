"""Load agent & skill seed content from backend/seeds/ into DB (idempotent)."""

from __future__ import annotations

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

    async def load_all(self) -> dict[str, int]:
        agents_loaded = await self._load_agents()
        skills_loaded = await self._load_skills()
        bindings_set = await self._bind_script_ai_skills()
        return {
            "agents": agents_loaded,
            "skills": skills_loaded,
            "agent_skill_bindings": bindings_set,
        }

    # ---------- agents ----------

    async def _load_agents(self) -> int:
        agents_dir = self.seeds_root / "agents"
        if not agents_dir.exists():
            return 0
        count = 0
        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            slug = agent_dir.name
            fields = self._read_agent_fields(agent_dir, slug)
            await self._upsert_agent(slug, fields)
            count += 1
        return count

    def _read_agent_fields(self, agent_dir: Path, slug: str) -> dict[str, Any]:
        def read_if_exists(name: str) -> Optional[str]:
            p = agent_dir / name
            if p.exists():
                txt = p.read_text().strip()
                return txt or None
            return None

        return {
            "slug": slug,
            "name": slug.replace("_", " ").title(),
            "identity_md": read_if_exists("IDENTITY.md"),
            "soul_md": read_if_exists("SOUL.md"),
            "agent_md": read_if_exists("AGENT.md"),
            "is_system_preset": True,
        }

    async def _upsert_agent(self, slug: str, fields: dict[str, Any]) -> None:
        existing = await self.agent_repo.get_by_slug(slug)
        if existing:
            # Strip immutable identity fields before updating
            update_fields = {k: v for k, v in fields.items() if k != "slug"}
            await self.agent_repo.update_fields(UUID(existing["id"]), update_fields)
            logger.info(f"seed_loader: updated agent {slug}")
        else:
            # Insert via raw client (repo has no insert method — rely on client directly)
            await self._insert_agent_row(fields)
            logger.info(f"seed_loader: inserted agent {slug}")

    async def _insert_agent_row(self, fields: dict[str, Any]) -> None:
        client = await self.agent_repo._get_client()
        await client.table("ai_agents").insert(fields).execute()

    # ---------- skills ----------

    async def _load_skills(self) -> int:
        skills_dir = self.seeds_root / "skills"
        if not skills_dir.exists():
            return 0
        count = 0
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            slug = skill_dir.name
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
                "category": fm.get("category"),
                "icon": fm.get("icon", "✨"),
                "is_public": fm.get("is_public", True),
                "frontmatter_json": fm,
                "status": "active",
            }

            skill_id = await self._upsert_skill(slug, fields)
            await self._load_skill_subfiles(skill_id, skill_dir)
            count += 1
        return count

    async def _upsert_skill(self, slug: str, fields: dict[str, Any]) -> int:
        existing = await self.skill_repo.get_by_slug(slug)
        if existing:
            skill_id = int(existing["id"])
            update_fields = {k: v for k, v in fields.items() if k != "slug"}
            await self.skill_repo.update_fields(skill_id, update_fields)
            logger.info(f"seed_loader: updated skill {slug} (id={skill_id})")
            return skill_id
        client = await self.skill_repo._get_client()
        resp = await client.table("skills").insert(fields).execute()
        skill_id = int(resp.data[0]["id"])
        logger.info(f"seed_loader: inserted skill {slug} (id={skill_id})")
        return skill_id

    async def _load_skill_subfiles(self, skill_id: int, skill_dir: Path) -> None:
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
                await self.skill_repo.upsert_file(
                    skill_id, path=rel, content=content, file_type=file_type
                )

    # ---------- bindings ----------

    async def _bind_script_ai_skills(self) -> int:
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
