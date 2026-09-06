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
from typing import Any, Optional, Sequence
from uuid import UUID

from app.boundary.frame_markers import (
    escape_frame_attr,
    escape_frame_body,
    escape_frame_prose,
)
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.assets.chat_ref import ChatAssetRef
from app.utils.ai_status import ai_status_str

CACHE_BOUNDARY_MARKER = "<!-- CACHE_BOUNDARY -->"


class AgentNotFoundError(Exception):
    """Raised when a requested agent slug does not exist in ``ai_agents``."""


@dataclass(frozen=True)
class ComposerInput:
    """Immutable input to :meth:`PromptComposer.compose`."""

    agent_slug: str
    request_instructions: Optional[str] = None
    session_id: Optional[str] = None
    model_override: Optional[str] = None
    # Phase 4 M3: bi-temporal facts retrieved from the Graphiti graph
    # (plain strings — graph edges have no row identity).
    graph_facts: list[str] = field(default_factory=list)
    # Phase 4 L2: Honcho working representation of the user (markdown
    # observation list). Caller fetches it; composer only renders.
    user_context: Optional[str] = None
    # Phase A: agent-memory recall results (MemoryContext-scoped). Rendered
    # post-boundary as <agent_memory> when non-empty; omitted when [].
    agent_memory_facts: list[str] = field(default_factory=list)
    # Agent-overrides (mig 341): when set, the composed prompt/model reflect
    # the caller's per-user / per-team customization of system presets.
    # Background pipelines leave these None → pristine system agent.
    override_user_id: Optional[Any] = None
    override_team_id: Optional[int] = None


def background_composer_input(
    *,
    agent_slug: str,
    request_instructions: str,
    resolved_model: str,
) -> ComposerInput:
    """Build the ``ComposerInput`` for a BACKGROUND task (口径 A, 2026-08-21).

    The five background modules (summarization / caption / visual analysis /
    classification / translation) all go through this constructor so their
    two halves of the口径 are stated once instead of five times:

    - ``model_override=resolved_model`` — ``resolve_task_ai_config`` is the
      SINGLE source of the model. It has already applied governance, the
      ``nous:`` direct pick, the user's ``agent_overrides.model`` and the
      platform-catalog ``name → actual_model`` step, and the credentials it
      returned alongside match THAT string. A service that let the composer
      supply its own model would dial one provider's id with another's key —
      the #622/#623 defect. Empty (bare / smoke paths that resolved nothing)
      leaves the composer on the agent row's value, as before.
    - NO ``override_user_id`` / ``override_team_id`` — the prompt stays at
      factory. These modules parse the agent's output against a fixed
      contract; a user's ``identity_md`` / ``soul_md`` / ``agent_md`` edit
      would break the parser rather than restyle the persona. Only the model
      follows the user's customization.

    Chat is deliberately NOT a caller: it *does* want prompt overrides and
    builds its ``ComposerInput`` with the override ids set.
    """
    return ComposerInput(
        agent_slug=agent_slug,
        request_instructions=request_instructions,
        model_override=resolved_model or None,
    )


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
        # Pass override kwargs only when a caller context exists — keeps
        # duck-typed repo fakes (tests) and any legacy repo signature working.
        if inp.override_user_id is not None or inp.override_team_id is not None:
            agent = await self.agent_repo.get_by_slug(
                inp.agent_slug,
                override_user_id=inp.override_user_id,
                override_team_id=inp.override_team_id,
            )
        else:
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
            graph_facts=inp.graph_facts,
            user_context=inp.user_context,
            agent_memory_facts=inp.agent_memory_facts,
        )
        tools = self._build_tools(skills, agent)
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
            inp.graph_facts,
            user_context=inp.user_context,
            agent_memory_facts=inp.agent_memory_facts,
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
            recalled_memory_ids=[],
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
        workers: list[dict[str, Any]] = None,
        graph_facts: list[str] = None,
        user_context: Optional[str] = None,
        agent_memory_facts: list[str] = None,
    ) -> str:
        """Render the full system message string, sections joined by \\n\\n.

        Layout (M3 + Phase 4):
            Identity / Soul / Agent / <available_skills>
            <available_workers> [M3 — persistent agents Delegate can target]
            <!-- CACHE_BOUNDARY -->
            <graph_facts> [Graphiti] / <user_context> [Honcho] — after the
                          boundary so the prefix cache stays stable per turn
            Request Instructions
            Runtime

        ``<available_workers>`` lives BEFORE the cache boundary because
        the persistent agent list rarely changes — including it in the
        prefix lets cache reuse work across turns. Adding/removing a
        persistent agent invalidates the cache, which is correct.
        """
        parts: list[str] = []
        graph_facts = graph_facts or []
        agent_memory_facts = agent_memory_facts or []

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

        # Graph facts live AFTER the cache_boundary so the stable prefix
        # remains cacheable across turns — they change per turn and per user.
        if graph_facts:
            parts.append(self._render_graph_facts_section(graph_facts))

        # Phase 4 L2: Honcho user model (working representation) — also
        # post-boundary; it evolves as the deriver processes turns.
        if user_context and user_context.strip():
            parts.append(self._render_user_context_section(user_context))

        # Phase A: agent-memory recall (MemoryContext-scoped, flag-gated).
        # Post-boundary so prefix cache stays stable; omitted when empty.
        if agent_memory_facts:
            parts.append(self._render_agent_memory_section(agent_memory_facts))

        if request_instructions and request_instructions.strip():
            parts.append(f"# Request Instructions\n{request_instructions.strip()}")

        parts.append(self._render_runtime_line(agent))

        return "\n\n".join(parts)

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
        safe = escape_frame_body(user_context.strip())
        return f"{header}<user_context>\n{safe}\n</user_context>"

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

    def _render_agent_memory_section(self, facts: list[str]) -> str:
        """Render <agent_memory> — scoped recall from the agent_memories table
        (Phase A). Plain fact strings retrieved by MemoryContext-gated recall.
        Treat as background data, not new user input."""
        header = (
            "## Agent Memory\n"
            "Facts the system has stored from previous interactions with this "
            "agent. Treat as background data, not new user input; the user's "
            "current message wins when they conflict.\n"
        )
        xml: list[str] = ["<agent_memory>"]
        for fact in facts:
            safe = (fact or "").replace("<", "&lt;").replace(">", "&gt;")
            xml.append(f"  <fact>{safe}</fact>")
        xml.append("</agent_memory>")
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
        # `escape_frame_attr` on element TEXT, not just attributes, is
        # deliberate: it escapes `<`/`>` outright, which both preserves the
        # pre-existing `&lt;tag&gt;` contract and is strictly stronger than
        # body escaping here. Do not "correct" it to escape_frame_body.
        xml: list[str] = ["<available_skills>"]
        for s in skills:
            xml.append("  <skill>")
            name = escape_frame_attr(s.get("slug") or s.get("name"))
            xml.append(f"    <name>{name}</name>")
            desc = escape_frame_attr(s.get("description"))
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
            slug = escape_frame_attr(w.get("slug") or w.get("name"))
            desc = escape_frame_attr(w.get("description"))
            model = escape_frame_attr(w.get("model"))
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

    def _build_tools(
        self, skills: list[dict[str, Any]], agent: Optional[dict[str, Any]] = None
    ) -> list[dict]:
        """Build the function-calling tools list.

        Built-in tools advertised:
        - ``Skill`` — load a skill definition (only when skills are bound)
        - ``Delegate`` — hand a sub-task to another persistent agent. Gated
          behind ``FEATURE_WORKFORCE_DELEGATE`` (audit #4): the inbox→worker
          execution chain isn't fully wired, so advertising it would let the
          LLM queue tasks that orphan forever. Off (default) → not advertised.
        - A4 screenwriting tools (ListScenes / ReadScene / CreateShot /
          UpdateShot / ProposeEdit) — advertised per the agent's granted
          ``capabilities.write_level``, which is "none" for every agent that
          hasn't been explicitly granted one, so this is a no-op for the
          whole existing fleet.

        The screenwriting specs live HERE rather than at each chat/summon
        call site (the way ResourceFetch and the media tools do) because
        every dispatch path already funnels through ``compose`` — putting
        them here is the difference between the tools working on one route
        and working on all of them.

        ⚠️ That reach is exactly why advertising must not be mistaken for
        enforcement (A4 review, Critical 1). ``compose`` is also called by
        eight services that build ``AgentRunner`` with NO hooks, where A1's
        gate does not exist to re-check anything. Enforcement is therefore
        anchored at the dispatcher: ``AgentRunner._dispatch_screenwriting``
        refuses outright when ``HighRiskCapabilityGateHook`` isn't installed
        on the runner. The ``write_level`` filter below only decides what to
        SHOW the model.

        Returns ``[]`` when no skills are bound, Delegate is gated off, and
        the agent has no write grade.
        """
        from app.services.workforce.delegate_feature import (
            delegate_feature_enabled,
        )

        tools: list[dict] = []
        if skills:
            tools.append(self._skill_tool_spec())
        if delegate_feature_enabled():
            tools.append(self._delegate_tool_spec())
        if agent is not None:
            from app.services.ai.permissions.high_risk_caps import (
                high_risk_caps,
                media_kill_switch_engaged,
            )
            from app.services.ai.tools.screenwriting_specs import (
                screenwriting_tool_specs,
            )

            caps = high_risk_caps(agent)
            # GenerateShotImage (A6) rides the write_level-filtered list but is
            # gated on a different capability (media.image) — see
            # screenwriting_tool_specs' docstring. Both the grant AND the
            # install-wide kill switch are checked here, mirroring the
            # media-tool registration block in ai_library_chat_service.py, so
            # a killed-switch or ungranted agent never even sees the tool
            # advertised (enforcement still lives in HighRiskCapabilityGateHook
            # regardless of what this UX filter decides).
            media_image_allowed = caps.media.image and not media_kill_switch_engaged()
            tools.extend(
                screenwriting_tool_specs(
                    caps.write_level, media_image_allowed=media_image_allowed
                )
            )
        return tools

    def _skill_tool_spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "Skill",
                "description": (
                    "Load a local skill definition and its instructions. "
                    "Returns the SKILL body (and optional sub-file "
                    'content). Built-in skill="todo" keeps your '
                    "multi-step plan for this turn: op=replace with "
                    "items to set the steps, then op=complete with id "
                    "as you finish each."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": (
                                "Skill slug from <available_skills>, or the "
                                "built-in 'todo'."
                            ),
                        },
                        "file": {
                            "type": "string",
                            "description": (
                                "Optional sub-file path like "
                                "'references/examples.md'. Omit to "
                                "return the SKILL.md body."
                            ),
                        },
                        # skill="todo" only. Declared here because a model
                        # cannot use arguments it was never shown: on
                        # 2026-09-06 doubao lite stuffed "?op=replace&items="
                        # into `file` four times in a row and every call
                        # failed, so the run never produced a todo snapshot
                        # and the task card never showed n/m.
                        "op": {
                            "type": "string",
                            "enum": [
                                "replace",
                                "complete",
                                "in_progress",
                                "pending",
                                "show",
                            ],
                            "description": (
                                "skill='todo' only: replace the whole list, "
                                "change one item's status, or show it."
                            ),
                        },
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "active_form": {
                                        "type": "string",
                                        "description": (
                                            "Present-continuous label shown "
                                            "while in progress, e.g. "
                                            "'Writing scene 2'."
                                        ),
                                    },
                                },
                                "required": ["content"],
                            },
                            "description": (
                                "skill='todo', op='replace' only: the full "
                                "step list (max 30)."
                            ),
                        },
                        "id": {
                            "type": "integer",
                            "description": (
                                "skill='todo', op=complete/in_progress/"
                                "pending: the item id from the list."
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
        graph_facts: list[str] | None = None,
        user_context: Optional[str] = None,
        agent_memory_facts: list[str] | None = None,
    ) -> str:
        """Prefix fingerprint extended with per-turn memory content hashes.

        Critical for cache safety (plan-eng-review Issue 2.2): when the
        injected memory (graph facts / user model / agent memory) changes,
        downstream cache providers must see a different fingerprint and not
        serve a stale prefix that could leak another user's facts.
        """
        h = hashlib.sha1()  # noqa: S324
        h.update(prefix_fp.encode())
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
        # Phase A: agent-memory facts — content-hashed so a changed recall
        # set produces a different key (cache isolation per user/agent).
        for fact in sorted(agent_memory_facts or []):
            h.update(b"|amem|")
            h.update(fact.encode())
            h.update(b"#")
        return h.hexdigest()


def render_available_resources(
    refs: list[dict] | None,
    assets: Sequence[ChatAssetRef] | None = None,
) -> str:
    """Render the ``<available_resources>`` block for this turn's @-mentions.

    Two kinds of entry share one frame: ``<resource … />`` (a file the model
    can fetch) and ``<asset …>consistency prompt</asset>`` (a library entity —
    a character, a location — whose picture is one of the resources). They are
    siblings on purpose (P5 ruling A): ``<asset>`` is an ELEMENT INSIDE the
    frame we own, not a frame of its own, so it is deliberately absent from
    ``OWNED_FRAMES`` — registering it there would mangle every legitimate
    mention of the word in a prompt for no authority gained.

    The consistency prompt is nonetheless the only user-written, unbounded,
    newline-bearing value this frame carries, and the frame is read LINE BY
    LINE. So it goes through ``escape_frame_prose``, not ``escape_frame_body``
    (final review I1): flattened to one line so it cannot emit a forged
    ``  <resource … />`` row, and entity-escaped so it cannot forge one
    in-line either. A user-authored ``</asset>`` consequently does not even
    truncate its own entry any more.

    Assets render AFTER every resource so the primary images they point at are
    already on the page when the model reads ``primary_resource_id``.

    Returns empty string when there is nothing to render — refs AND assets
    both empty — so the system message cache key stays stable for turns
    without any @-mention.
    """
    if not refs and not assets:
        return ""

    def _fmt_size(n: int | None) -> str:
        if not n:
            return ""
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n // 1024}KB"
        return f"{n // (1024 * 1024)}MB"

    def _status_attr(r: dict) -> str | None:
        """``status="transcript:X summary:Y"`` for media that can carry AI
        text, or None.

        Gated to video/audio on purpose: transcript/summary are meaningless
        for a markdown file, and emitting ``transcript:none`` on every doc
        mention would be noise the model has to read past — plus needless
        churn in the cache fingerprint. Refs that predate the resolver
        change carry neither key and render exactly as before.
        """
        if r.get("kind") not in ("video", "audio"):
            return None
        # Coerce here, at the f-string boundary where a raw AiTaskStatus
        # member would render as its repr — not just upstream in the
        # resolver (any future caller feeding refs directly must be safe).
        transcript = ai_status_str(r.get("transcript_status"))
        summary = ai_status_str(r.get("summary_status"))
        if transcript is None and summary is None:
            return None
        return f'status="transcript:{transcript or "none"} summary:{summary or "none"}"'

    lines = ["<available_resources>"]
    any_status = False
    for r in refs or []:
        # Every value is escaped, not just the obviously user-owned ones:
        # picking per-attribute is how the next attribute added here ends up
        # raw. `name` is the live vector — users rename resources freely.
        attrs = [
            f'id="{escape_frame_attr(r["id"])}"',
            f'kind="{escape_frame_attr(r["kind"])}"',
            f'mime="{escape_frame_attr(r.get("mime"))}"',
            f'scope="{escape_frame_attr(r.get("scope"))}"',
            f'size="{_fmt_size(r.get("size"))}"',
            f'updated="{escape_frame_attr(r.get("updated_at"))}"',
            f'name="{escape_frame_attr(r["name"])}"',
        ]
        status_attr = _status_attr(r)
        if status_attr:
            any_status = True
            attrs.append(status_attr)
        if r.get("brief"):
            # Was `"` → `'`, which stops the quote breakout but leaves `<`/`>`
            # free to open a frame the model trusts.
            attrs.append(f'brief="{escape_frame_attr(r["brief"])}"')
        lines.append(f"  <resource {' '.join(attrs)} />")
    for a in assets or []:
        # Same posture as the resource attributes above: every value goes
        # through `escape_frame_attr`, including the ones that look
        # machine-generated. `name` is user-typed, and an id that arrived as a
        # string from a wire payload is only as trustworthy as its source.
        asset_attrs = [
            f'id="{escape_frame_attr(a.asset_id)}"',
            f'type="{escape_frame_attr(a.asset_type)}"',
            f'name="{escape_frame_attr(a.name)}"',
            f'scope="{escape_frame_attr(a.scope_id)}"',
        ]
        # Absent, not empty: `primary_resource_id=""` reads as an id the model
        # may pass to ResourceFetch, and it would fail there with nothing
        # explaining why. A missing attribute plus has_image="false" says
        # "there is no picture to fetch" without inviting the call. `loadout`
        # follows the same rule for symmetry — a v1 client never picks one.
        if a.primary_resource_id is not None:
            asset_attrs.append(
                f'primary_resource_id="{escape_frame_attr(a.primary_resource_id)}"'
            )
        # Spelled here, not in the dataclass: `has_image` is a real bool and
        # Python would render it "True"/"False", which is not what an XML-ish
        # attribute means to the model.
        asset_attrs.append(f'has_image="{"true" if a.has_image else "false"}"')
        if a.loadout_id is not None:
            asset_attrs.append(f'loadout="{escape_frame_attr(a.loadout_id)}"')
        # `escape_frame_prose`, NOT `escape_frame_body`: this frame is a
        # line-oriented catalogue and the consistency prompt is the only
        # user-written, newline-bearing, model-visible value in it. Body
        # escaping alone leaves the newlines and the `<`, which is enough to
        # emit a `  <resource … />` line indistinguishable from one we wrote
        # (final review I1). Flattening kills the forged row, entity-escaping
        # kills a forged element that stays on this line.
        body = escape_frame_prose(a.consistency_prompt)
        lines.append(f"  <asset {' '.join(asset_attrs)}>{body}</asset>")
    lines.append("</available_resources>")
    # Only describe ResourceFetch when this turn actually has something to
    # fetch. The chat service registers the tool on RESOURCE refs, so an
    # assets-only turn (every asset a `prompt` type, or none with a readable
    # primary) has no tool at all — printing six lines of video/doc/pdf modes
    # there tells the model about a tool it does not have, and about kinds
    # nothing on the page even is.
    fetchable_assets = [a for a in (assets or []) if a.primary_resource_id]
    if not refs and not fetchable_assets:
        return "\n".join(lines)
    lines.append("")
    lines.append("Use the ResourceFetch tool to load any of these on demand:")
    lines.append("  ResourceFetch(resource_id, mode?, args?)")
    lines.append("  - mode for video: summary (default) | transcript | frames")
    # frames returns still images sampled evenly across the video, so a
    # vision model can answer questions about what is on screen. Say the
    # count arg out loud: it lives in `args`, which the tool schema
    # describes only with a PDF example, so a model that is not told about
    # it here has no way to ask for more than the default.
    lines.append(
        "    frames returns evenly sampled still images from the video; "
        "args.frames sets how many (default 6, max 12)"
    )
    lines.append("  - mode for doc: excerpt (default) | full")
    lines.append("  - mode for pdf: excerpt (default) | page (args.page)")
    lines.append("  - mode for image: omit (returns image part)")
    lines.append("  - mode for audio: transcript (default)")
    if fetchable_assets:
        # The <asset> body is the consistency text; the picture is NOT inlined.
        # Without this line the model has an id attribute and no stated way to
        # turn it into an image, which reads as "the asset has no picture".
        # Gated on an asset that HAS a primary: when none does, the sentence
        # explains how to use an attribute that appears nowhere on the page.
        lines.append(
            "  - an <asset> entry carries its consistency prompt as the body; "
            "fetch its picture with ResourceFetch(primary_resource_id, "
            "mode=image) when has_image is true"
        )
    if any_status:
        # Without this the model sees an opaque attribute and still relays a
        # bare failure — the exact complaint that motivated the change.
        lines.append("")
        lines.append(
            "The status attribute is the AI processing state of that media. "
            "pending/processing means the text is being generated right now: "
            "say so and ask the user to retry in a moment — do NOT report it "
            "as unavailable. none/failed means nothing has been generated yet."
        )
    return "\n".join(lines)
