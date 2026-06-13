"""System message + tools composer for AI agents.

Assembles the system prompt + function-calling tools schema from a
DB-backed agent row (``ai_agents``) and its bound skills (``skills``,
linked via ``agent_skills``). Produces a :class:`ComposedSystemPrompt`
consumed by the LLM adapter layer.

Layout:

    # Identity            ← agent.identity_md (若非空)
    # Soul                ← agent.soul_md (若非空) + persona instruction
    # Agent Instructions  ← agent.agent_md

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.schemas.ai_library import ComposedSystemPrompt

CACHE_BOUNDARY_MARKER = "<!-- CACHE_BOUNDARY -->"


class AgentNotFoundError(Exception):
    """Raised when a requested agent slug does not exist in ``ai_agents``."""


@dataclass(frozen=True)
class RecalledMemory:
    """One memory the retriever decided to inject. Identity by id; UI int
    label is assigned by the composer when rendering."""

    id: UUID
    summary: str
    when_to_use: str


@dataclass(frozen=True)
class ComposerInput:
    """Immutable input to :meth:`PromptComposer.compose`."""

    agent_slug: str
    request_instructions: Optional[str] = None
    session_id: Optional[str] = None
    model_override: Optional[str] = None
    # M1.B: caller (chat service) recalls memories first then passes them
    # in. PromptComposer doesn't do retrieval — separation of concerns.
    recalled_memories: list[RecalledMemory] = field(default_factory=list)
    # Phase 4 M3: bi-temporal facts retrieved from the Graphiti graph
    # (plain strings — graph edges have no agent_memories identity).
    graph_facts: list[str] = field(default_factory=list)
    # Phase 4 L2: Honcho working representation of the user (markdown
    # observation list). Caller fetches it; composer only renders.
    user_context: Optional[str] = None


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

        # M3: list of persistent agents available as Delegate targets.
        # Independent of whether THIS agent has skills — a coordinator
        # agent that just delegates to others doesn't need a skill of
        # its own. Self is excluded so the LLM doesn't try to delegate
        # to itself.
        workers: list[dict[str, Any]] = []
        try:
            workers_raw = await self.agent_repo.list_persistent()
            workers = [w for w in workers_raw if w.get("id") != agent.get("id")]
        except Exception:
            # Best-effort. If the listing fails, render without
            # workers — Delegate will get "unknown agent slug" from
            # the tool layer if the LLM tries it.
            workers = []

        system_message = self._assemble_system_message(
            agent=agent,
            skills=skills,
            workers=workers,
            request_instructions=inp.request_instructions,
            recalled_memories=inp.recalled_memories,
            graph_facts=inp.graph_facts,
            user_context=inp.user_context,
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

        prefix_fp = self._prefix_fingerprint(agent, skills, workers)
        dynamic_fp = self._dynamic_fingerprint(
            prefix_fp,
            inp.recalled_memories,
            inp.graph_facts,
            user_context=inp.user_context,
        )

        return ComposedSystemPrompt(
            agent_id=UUID(agent["id"]),
            agent_slug=agent["slug"],
            model=inp.model_override or agent.get("model") or "qwen-max",
            temperature=float(agent.get("temperature", 0.7)),
            max_tokens=int(agent.get("max_tokens", 4096)),
            system_message=system_message,
            tools=tools,
            skill_manifest=manifest,
            cache_fingerprint=prefix_fp,  # back-compat alias
            prefix_fingerprint=prefix_fp,
            dynamic_fingerprint=dynamic_fp,
            recalled_memory_ids=[m.id for m in inp.recalled_memories],
            # mig 286: per-run wall-clock cap, enforced by AgentRunner
            # between LLM iterations.
            timeout_sec=agent.get("timeout_sec"),
        )

    # ------------------------------------------------------------------
    # Internals — pure, no I/O, unit-testable without repos
    # ------------------------------------------------------------------

    def _assemble_system_message(
        self,
        agent: dict[str, Any],
        skills: list[dict[str, Any]],
        request_instructions: Optional[str],
        recalled_memories: list["RecalledMemory"] = None,
        workers: list[dict[str, Any]] = None,
        graph_facts: list[str] = None,
        user_context: Optional[str] = None,
    ) -> str:
        """Render the full system message string, sections joined by \\n\\n.

        Layout (M1.B + M3):
            Identity / Soul / Agent / <available_skills>
            <available_workers> [M3 — persistent agents Delegate can target]
            <!-- CACHE_BOUNDARY -->
            <recalled_memories> [M1.B injection — after boundary so the
                                 prefix cache stays stable across turns]
            Request Instructions
            Runtime

        ``<available_workers>`` lives BEFORE the cache boundary because
        the persistent agent list rarely changes — including it in the
        prefix lets cache reuse work across turns. Adding/removing a
        persistent agent invalidates the cache, which is correct.
        """
        parts: list[str] = []
        recalled_memories = recalled_memories or []
        graph_facts = graph_facts or []

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

        instruction = (agent.get("agent_md") or "").strip()
        if instruction:
            parts.append(f"# Agent Instructions\n{instruction}")

        if skills:
            parts.append(self._render_skills_section(skills))

        if workers:
            parts.append(self._render_workers_section(workers))

        parts.append(CACHE_BOUNDARY_MARKER)

        # Memory section MUST be AFTER cache_boundary so the stable prefix
        # remains cacheable across turns. Recall results change every turn.
        if recalled_memories:
            parts.append(self._render_memory_section(recalled_memories))

        # Graph facts share the post-boundary zone for the same cache-
        # safety reason — they change per turn and per user.
        if graph_facts:
            parts.append(self._render_graph_facts_section(graph_facts))

        # Phase 4 L2: Honcho user model (working representation) — also
        # post-boundary; it evolves as the deriver processes turns.
        if user_context and user_context.strip():
            parts.append(self._render_user_context_section(user_context))

        if request_instructions and request_instructions.strip():
            parts.append(f"# Request Instructions\n{request_instructions.strip()}")

        parts.append(self._render_runtime_line(agent))

        return "\n\n".join(parts)

    def _render_memory_section(self, memories: list["RecalledMemory"]) -> str:
        """Render <recalled_memories> XML manifest with int-mapped refs.

        LLM sees [0]/[1]/[2] not raw UUIDs (Mem Zero pattern).
        """
        header = (
            "## Recalled Memories\n"
            "These are facts the system remembers about this user from past "
            "conversations. Use them to personalise your reply when relevant. "
            "Reference by [N] if you cite one.\n"
        )
        xml: list[str] = ["<recalled_memories>"]
        for i, mem in enumerate(memories):
            summary = (mem.summary or "").replace("<", "&lt;").replace(">", "&gt;")
            when = (mem.when_to_use or "").replace("<", "&lt;").replace(">", "&gt;")
            xml.append("  <memory>")
            xml.append(f"    <ref>[{i}]</ref>")
            xml.append(f"    <when_to_use>{when}</when_to_use>")
            xml.append(f"    <fact>{summary}</fact>")
            xml.append("  </memory>")
        xml.append("</recalled_memories>")
        return header + "\n" + "\n".join(xml)

    def _render_user_context_section(self, user_context: str) -> str:
        """Render <user_context> — the Honcho working representation of
        this user (Phase 4 L2). Observations the deriver has extracted
        from past conversations across sessions."""
        header = (
            "## User Model\n"
            "What the system has learned about this user from past "
            "conversations. Treat as background context; the user's "
            "current message wins when they conflict.\n"
        )
        return f"{header}<user_context>\n{user_context.strip()}\n</user_context>"

    def _render_graph_facts_section(self, facts: list[str]) -> str:
        """Render <graph_facts> — relationship facts from the knowledge
        graph (Phase 4 M3). Plain strings; no ref ids needed."""
        header = (
            "## Knowledge Graph Facts\n"
            "Relationship facts the system has extracted about this user's "
            "world from past conversations. Treat them as background "
            "context; prefer the user's current message when they conflict.\n"
        )
        xml: list[str] = ["<graph_facts>"]
        for fact in facts:
            safe = (fact or "").replace("<", "&lt;").replace(">", "&gt;")
            xml.append(f"  <fact>{safe}</fact>")
        xml.append("</graph_facts>")
        return header + "\n" + "\n".join(xml)

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

    def _render_workers_section(self, workers: list[dict[str, Any]]) -> str:
        """Render the ``## Available Workers`` block + XML manifest (M3).

        Lists every other persistent agent the caller can dispatch via
        ``Delegate(agent_slug=...)``. Empty list → caller still has the
        Delegate tool registered, but no targets to choose from.
        """
        header = (
            "## Available Workers\n"
            "Other persistent agents you can dispatch sub-tasks to via "
            "Delegate(agent_slug=..., prompt=...). They run in parallel "
            "and reply via your inbox. Pick one only when their description "
            "matches the sub-task — otherwise just answer directly.\n"
        )
        xml: list[str] = ["<available_workers>"]
        for w in workers:
            slug = w.get("slug") or w.get("name") or ""
            desc = (
                (w.get("description") or "").replace("<", "&lt;").replace(">", "&gt;")
            )
            model = w.get("model") or ""
            xml.append("  <worker>")
            xml.append(f"    <slug>{slug}</slug>")
            xml.append(f"    <description>{desc}</description>")
            xml.append(f"    <model>{model}</model>")
            xml.append("  </worker>")
        xml.append("</available_workers>")
        return header + "\n" + "\n".join(xml)

    def _render_runtime_line(self, agent: dict[str, Any]) -> str:
        """Render the trailing ``# Runtime`` line with model + UTC time."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"# Runtime\nModel: {agent.get('model')} | Time: {now}"

    def _build_tools(self, skills: list[dict[str, Any]]) -> list[dict]:
        """Build the function-calling tools list.

        Two built-in tools are advertised:
        - ``Skill`` — load a skill definition (only when skills are bound)
        - ``Delegate`` — hand a sub-task to another persistent agent
          (always, even when the agent has no skills of its own — a
          coordinator pattern)

        Returns empty only when both conditions disable both tools (no
        skills AND ``Delegate`` is somehow undesired — currently we
        always emit Delegate, so this list is always non-empty in
        practice).
        """
        tools: list[dict] = []
        if skills:
            tools.append(self._skill_tool_spec())
        tools.append(self._delegate_tool_spec())
        return tools

    def _skill_tool_spec(self) -> dict:
        return {
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
                            "description": "Skill slug from <available_skills>.",
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

    def _delegate_tool_spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "Delegate",
                "description": (
                    "Hand off a sub-task to another persistent agent. "
                    "The target picks the task from its inbox on the "
                    "next dispatch tick. Fire-and-forget by default; "
                    "use status_query to check progress later. Use this "
                    "for parallel work, specialised expertise, or when "
                    "you need a different agent's persona/skills."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_slug": {
                            "type": "string",
                            "description": (
                                "Slug of the target persistent agent "
                                "(see <available_workers>). Target "
                                "must have ai_agents.persistent=true."
                            ),
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "The task description / instruction "
                                "for the target agent. Be specific."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": (
                                "Optional short title for the task "
                                "(shown in worker UI)."
                            ),
                        },
                        "priority": {
                            "type": "integer",
                            "description": (
                                "Inbox priority 1-10 (higher = sooner). " "Default 5."
                            ),
                        },
                        "dedup_key": {
                            "type": "string",
                            "description": (
                                "Optional dedup key — repeated calls "
                                "with the same key are folded while "
                                "the message is unread."
                            ),
                        },
                        "await": {
                            "type": "boolean",
                            "description": (
                                "If true, block this turn until the target "
                                "finishes and embed the result content in "
                                "the response. Default false (fire-and-"
                                "forget). Use sparingly: holds the caller's "
                                "agent for up to await_timeout_seconds."
                            ),
                        },
                        "await_timeout_seconds": {
                            "type": "number",
                            "description": (
                                "Max seconds to block when await=true. "
                                "Default 60, capped at 180. Ignored when "
                                "await=false."
                            ),
                        },
                    },
                    "required": ["agent_slug", "prompt"],
                },
            },
        }

    def _prefix_fingerprint(
        self,
        agent: dict[str, Any],
        skills: list[dict[str, Any]],
        workers: list[dict[str, Any]] = None,
    ) -> str:
        """Stable SHA-1 over prefix inputs.

        Inputs: agent identity/soul/agent_md, bound skills, and (M3) the
        list of persistent worker agents available as Delegate targets.
        Adding/removing a persistent worker invalidates this fingerprint
        — required for prompt-cache safety since the rendered prompt
        changes.

        Workers sorted by id for stability. Skills sorted by id for stability.
        """
        h = hashlib.sha1()  # noqa: S324 — not used for security
        h.update(str(agent.get("id", "")).encode())
        h.update(str(agent.get("updated_at", "")).encode())
        h.update((agent.get("identity_md") or "").encode())
        h.update((agent.get("soul_md") or "").encode())
        h.update((agent.get("agent_md") or "").encode())
        for s in sorted(skills, key=lambda x: str(x.get("id"))):
            h.update(str(s.get("id", "")).encode())
            h.update(str(s.get("updated_at", "")).encode())
        if workers:
            for w in sorted(workers, key=lambda x: str(x.get("id"))):
                h.update(b"|worker|")
                h.update(str(w.get("id", "")).encode())
                h.update((w.get("slug") or "").encode())
        return h.hexdigest()

    def _dynamic_fingerprint(
        self,
        prefix_fp: str,
        recalled_memories: list["RecalledMemory"],
        graph_facts: list[str] | None = None,
        user_context: Optional[str] = None,
    ) -> str:
        """Prefix fingerprint extended with recalled memory id set hash.

        Critical for cache safety (plan-eng-review Issue 2.2): when memory
        recall changes, downstream cache providers must see a different
        fingerprint and not serve a stale prefix that could leak another
        user's facts.
        """
        h = hashlib.sha1()  # noqa: S324
        h.update(prefix_fp.encode())
        # Order doesn't matter — recall set is what we hash, not order.
        for mid in sorted(str(m.id) for m in recalled_memories):
            h.update(mid.encode())
            h.update(b"|")
        # Phase 4 M3: graph facts are content-hashed (no row ids) — a
        # changed fact set must change the fingerprint too.
        for fact in sorted(graph_facts or []):
            h.update(fact.encode())
            h.update(b"#")
        # Phase 4 L2: Honcho user representation is content-hashed for
        # the same reason — a changed user model must change the key.
        if user_context:
            h.update(b"|user_context|")
            h.update(user_context.encode())
        return h.hexdigest()


def render_available_resources(refs: list[dict] | None) -> str:
    """Render the ``<available_resources>`` block for resource_ref refs.

    Returns empty string when there are no refs so the system message
    cache key stays stable for turns without any @-mention.
    """
    if not refs:
        return ""

    def _fmt_size(n: int | None) -> str:
        if not n:
            return ""
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n // 1024}KB"
        return f"{n // (1024 * 1024)}MB"

    lines = ["<available_resources>"]
    for r in refs:
        attrs = [
            f'id="{r["id"]}"',
            f'kind="{r["kind"]}"',
            f'mime="{r.get("mime") or ""}"',
            f'scope="{r.get("scope") or ""}"',
            f'size="{_fmt_size(r.get("size"))}"',
            f'updated="{r.get("updated_at") or ""}"',
            f'name="{r["name"]}"',
        ]
        if r.get("brief"):
            brief = r["brief"].replace('"', "'")
            attrs.append(f'brief="{brief}"')
        lines.append(f"  <resource {' '.join(attrs)} />")
    lines.append("</available_resources>")
    lines.append("")
    lines.append("Use the ResourceFetch tool to load any of these on demand:")
    lines.append("  ResourceFetch(resource_id, mode?, args?)")
    lines.append("  - mode for video: summary (default) | transcript | frames")
    lines.append("  - mode for doc: excerpt (default) | full")
    lines.append("  - mode for pdf: excerpt (default) | page (args.page)")
    lines.append("  - mode for image: omit (returns image part)")
    lines.append("  - mode for audio: transcript (default)")
    return "\n".join(lines)
