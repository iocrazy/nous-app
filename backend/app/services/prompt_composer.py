"""System message + tools composer for AI agents.

Assembles the system prompt + function-calling tools schema from a
DB-backed agent row (``ai_agents``) and its bound skills (``skills``,
linked via ``agent_skills``). Produces a :class:`ComposedSystemPrompt`
consumed by the LLM adapter layer.

Layout:

    # Identity            ← agent.identity_md (若非空)
    # Soul                ← agent.soul_md (若非空) + persona instruction
    # Agent Instructions  ← agent.agent_md (or persona fallback)

    ## Available Skills + <available_skills> XML manifest

    <!-- CACHE_BOUNDARY -->

    # Request Instructions ← per-request injection (mutable)
    # Runtime              ← model + UTC time

The sections BEFORE the cache boundary are stable across turns — this
lets upstream prompt-cache layers (Anthropic cache, vLLM KV-cache, etc.)
reuse prefix tokens. Everything mutable goes AFTER the boundary.

All I/O (agent & skill reads) happens in :meth:`compose`. The rendering
helpers (:meth:`_assemble_system_message`, :meth:`_build_tools`,
:meth:`_fingerprint`) are pure functions of the fetched dicts, so they
are unit-testable without touching Supabase.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.schemas.ai_library import ComposedSystemPrompt

CACHE_BOUNDARY_MARKER = "<!-- CACHE_BOUNDARY -->"


class AgentNotFoundError(Exception):
    """Raised when a requested agent slug does not exist in ``ai_agents``."""

    pass


@dataclass(frozen=True)
class ComposerInput:
    """Immutable input to :meth:`PromptComposer.compose`."""

    agent_slug: str
    request_instructions: Optional[str] = None
    session_id: Optional[str] = None
    model_override: Optional[str] = None


class PromptComposer:
    """Assemble system message + tools for a single agent call."""

    def __init__(
        self,
        agent_repo: Optional[AgentRepository],
        skill_repo: Optional[SkillRepository],
    ) -> None:
        self.agent_repo = agent_repo
        self.skill_repo = skill_repo

    async def compose(self, inp: ComposerInput) -> ComposedSystemPrompt:
        """Fetch agent + skills and render a ``ComposedSystemPrompt``.

        Raises :class:`AgentNotFoundError` if the slug is unknown.
        """
        assert self.agent_repo is not None and self.skill_repo is not None
        agent = await self.agent_repo.get_by_slug(inp.agent_slug)
        if not agent:
            raise AgentNotFoundError(f"agent slug not found: {inp.agent_slug}")

        skill_ids = await self.agent_repo.get_skill_ids(UUID(agent["id"]))
        skills = await self.skill_repo.list_by_ids(skill_ids)

        system_message = self._assemble_system_message(
            agent=agent,
            skills=skills,
            request_instructions=inp.request_instructions,
        )
        tools = self._build_tools(skills)
        manifest = [
            {
                "slug": s.get("slug"),
                "name": s.get("name"),
                "description": s.get("description"),
            }
            for s in skills
        ]

        return ComposedSystemPrompt(
            agent_id=UUID(agent["id"]),
            agent_slug=agent["slug"],
            model=inp.model_override or agent.get("model") or "qwen-max",
            temperature=float(agent.get("temperature", 0.7)),
            max_tokens=int(agent.get("max_tokens", 4096)),
            system_message=system_message,
            tools=tools,
            skill_manifest=manifest,
            cache_fingerprint=self._fingerprint(agent, skills),
        )

    # ------------------------------------------------------------------
    # Internals — pure, no I/O, unit-testable without repos
    # ------------------------------------------------------------------

    def _assemble_system_message(
        self,
        agent: dict[str, Any],
        skills: list[dict[str, Any]],
        request_instructions: Optional[str],
    ) -> str:
        """Render the full system message string, sections joined by \\n\\n."""
        parts: list[str] = []

        identity = (agent.get("identity_md") or "").strip()
        if identity:
            parts.append(f"# Identity\n{identity}")

        soul = (agent.get("soul_md") or "").strip()
        if soul:
            parts.append(
                f"# Soul\n{soul}\n\n"
                "Embody the persona and tone described above. Avoid generic "
                "or stiff replies unless higher-priority instructions "
                "override it."
            )

        instruction = (agent.get("agent_md") or agent.get("persona") or "").strip()
        if instruction:
            parts.append(f"# Agent Instructions\n{instruction}")

        if skills:
            parts.append(self._render_skills_section(skills))

        parts.append(CACHE_BOUNDARY_MARKER)

        if request_instructions and request_instructions.strip():
            parts.append(f"# Request Instructions\n{request_instructions.strip()}")

        parts.append(self._render_runtime_line(agent))

        return "\n\n".join(parts)

    def _render_skills_section(self, skills: list[dict[str, Any]]) -> str:
        """Render the ``## Available Skills`` block + XML manifest."""
        header = (
            "## Available Skills\n"
            "Before replying: scan <available_skills> entries.\n"
            '- If one clearly applies: call Skill(skill="<slug>") first, '
            "then follow the returned instructions.\n"
            "- If none apply: do not call Skill.\n"
            "Never call Skill more than once per turn unless the task "
            "clearly requires chaining.\n"
        )
        xml: list[str] = ["<available_skills>"]
        for s in skills:
            xml.append("  <skill>")
            xml.append(f"    <name>{s.get('slug') or s.get('name')}</name>")
            desc = (
                (s.get("description") or "").replace("<", "&lt;").replace(">", "&gt;")
            )
            xml.append(f"    <description>{desc}</description>")
            xml.append("  </skill>")
        xml.append("</available_skills>")
        return header + "\n" + "\n".join(xml)

    def _render_runtime_line(self, agent: dict[str, Any]) -> str:
        """Render the trailing ``# Runtime`` line with model + UTC time."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"# Runtime\nModel: {agent.get('model')} | Time: {now}"

    def _build_tools(self, skills: list[dict[str, Any]]) -> list[dict]:
        """Build the function-calling tools list.

        Phase 1 injects exactly one ``Skill`` tool; other caller-provided
        tools merge upstream (adapter layer). Returns an empty list when
        the agent has no bound skills, so the adapter can omit the
        ``tools`` parameter entirely.
        """
        if not skills:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "Skill",
                    "description": (
                        "Load a local skill definition and its instructions. "
                        "Returns the SKILL body (and optional sub-file "
                        "content)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill": {
                                "type": "string",
                                "description": ("Skill slug from <available_skills>."),
                            },
                            "file": {
                                "type": "string",
                                "description": (
                                    "Optional sub-file path like "
                                    "'references/examples.md'. Omit to "
                                    "return the SKILL.md body."
                                ),
                            },
                        },
                        "required": ["skill"],
                    },
                },
            }
        ]

    def _fingerprint(
        self,
        agent: dict[str, Any],
        skills: list[dict[str, Any]],
    ) -> str:
        """Stable SHA-1 over the prefix inputs (for cache keying).

        Excludes request-scoped fields (no ``request_instructions``, no
        timestamps) so two composes that would produce the same prefix
        get the same fingerprint. Skills are sorted by id so binding
        order doesn't destabilise the hash.
        """
        h = hashlib.sha1()  # noqa: S324 — not used for security
        h.update(str(agent.get("id", "")).encode())
        h.update(str(agent.get("updated_at", "")).encode())
        h.update((agent.get("identity_md") or "").encode())
        h.update((agent.get("soul_md") or "").encode())
        h.update((agent.get("agent_md") or agent.get("persona") or "").encode())
        for s in sorted(skills, key=lambda x: str(x.get("id"))):
            h.update(str(s.get("id", "")).encode())
            h.update(str(s.get("updated_at", "")).encode())
        return h.hexdigest()
