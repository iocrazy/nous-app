# Team Chat — Agent DM + Agent Broadcast Plan (Phase E)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.
> **STATUS: PLANNED — has an open design decision (E1) and a security-critical architecture decision (E2). Confirm direction before executing.**

This phase has two independent sub-features. They are deliberately NOT bundled: E1 is frontend/UX with a stale-spec-premise to resolve; E2 is a security-critical backend feature. Ship them as separate branches/PRs.

---

## E1 — Agent DM (CHAT-AGENT-06, CHAT-SEC-AGENT-04)

**Goal:** Let a user have a 1:1 conversation with a chat-enabled agent, surfaced in the Team Chat sidebar's Direct Messages section.

### ⚠️ Open design decision (resolve before building)
The spec (CHAT-AGENT-06) assumed "reuse the existing `AIChatPanel` + `ai_sessions` (already 1:1 with an agent), just surface it in the DM list." **Grounding found this premise is partly stale:** `AIChatPanel` (`frontend/components/AIChatPanel.tsx:46`) is **project-scoped** (`projectId: string` required, sessions tagged by project) and takes **no `agentId`/`agentSlug` prop** — it is not agent-scoped today. So "drop AIChatPanel into the DM list scoped to agent X" is not a clean reuse. Three options:

- **Option A (recommended): Adapt AIChatPanel to accept an optional `agentSlug` + make `projectId` optional.** When opened from a chat agent-DM, it lists/creates sessions for that agent without a project. Reuses all the rich rendering (tool-call traces, plan-mode approval, streaming). Medium effort; touches a heavily-used component (regression risk — guard with the existing AIChatDrawer callers unchanged).
- **Option B: Build agent-DM on the chat `channels` infra** (type='dm' + the agent in `agent_channels`, agent replies to every message). Requires: relax `run_channel_agent_turn`'s `team_id IS NOT NULL` gate for DMs (personal scope, CHAT-SEC-AGENT-04), make `channels.team_id` nullable OR introduce a personal-team, dedup user+agent DMs. Larger blast radius + security gate changes. NOT recommended.
- **Option C: A new lightweight agent-DM view** in ChatPage that calls `aiLibraryService` session APIs directly (list/create/select/stream) for the selected agent — a thin agent chat without AIChatPanel's project coupling. Reuses the backend `ai_sessions` + `/agents/{slug}/sessions` + `/sessions/{id}/chat-stream` (all exist) but reimplements the message view. Medium effort, no AIChatPanel regression risk, but duplicates rendering.

**Recommendation: Option A** (adapt AIChatPanel) for maximum reuse — but it modifies a shared component, so it needs careful review of existing AIChatDrawer/project callers. If that regression risk is unacceptable, Option C.

### Sketch (assuming Option A)
- `AIChatPanel`: add optional `agentSlug?: string`; make `projectId?` optional; when `agentSlug` set and no project, scope session list/create to that agent (the `/agents/{slug}/sessions` API already exists at `aiLibraryService.ts:381/406`). Keep all current project-callers working (default behavior unchanged when `agentSlug` is absent).
- `ChatSidebar`: in the DM section, list chat-enabled agents (reuse `aiLibraryService.listAgents()` filtered `chat_permissions?.enabled`) as agent-DM rows (amber agent glyph), distinct from human DMs.
- `ChatPage`: when an agent-DM row is selected, render `<AIChatPanel agentSlug={slug} onClose={...} />` in the main pane instead of MessageList/Composer.
- CHAT-SEC-AGENT-04 (personal scope): in a 1:1 agent DM the agent operates as the user calling it directly (personal `scope_type='user'` resources) — which the existing AIChatPanel/agent-runtime path already does (it's the same path as the standalone agent chat). No team-output-boundary applies because there is no team channel. Verify the agent-runtime resource access in this path is the user's own scope.
- i18n for any new labels.

### Deferral note
Agent DM does NOT go through the chat `channels`/`channel_messages`/`dispatch_summons` path at all (that's for team/group channels). It reuses `ai_sessions`. This keeps the team-scope iron-law machinery untouched.

---

## E2 — Agent Broadcast (CHAT-AGENT-05, CHAT-PERM-11, CHAT-SEC-AGENT-05)

**Goal:** An agent with the `auto_broadcast` capability proactively posts a message to a team channel when a relevant event occurs (first version: a `task_tracking` completion → post a status line to the team's `#general`-equivalent channel), WITHOUT a human summoner present.

### ⚠️ Security-critical — the hardest part of the whole epic
With no summoner present there is no per-user authorization subject. CHAT-SEC-AGENT-05 mandates: **broadcast may only emit team-scope, explicitly-shareable content (team public resources / task status) and must NEVER read any `scope_type='user'` private row.** This is a different (stricter) security model than the summon path — it cannot reuse the `(summoner_user_id, team_id)` resource scoping because there is no summoner.

### Open architecture decisions (resolve before building)
1. **Trigger mechanism:** (a) a Postgres trigger on `task_tracking` completion that enqueues a broadcast job; (b) a DBOS scheduled scanner that polls completed tasks since a watermark; (c) emit from the existing task-completion code path in-app. **Recommendation: (c) or (b)** — avoid DB triggers calling app logic. A DBOS scheduled scanner with a watermark is the most observable and matches existing patterns (`backend/app/workflows`).
2. **Channel mapping:** first version = the originating user's team's `#general` (or a designated broadcast channel). Need a way to resolve "the team's broadcast channel" (a `channels` row convention, e.g. the first `type='public'` named 'general', or a per-team setting).
3. **Content safety (the core constraint):** the broadcast composer must run the agent with NO resource-fetch handler that can reach user scope — either disable `resource_fetch` entirely for broadcast, or hard-restrict it to team-public reads. PERM-11 gate: skip any agent without `caps.auto_broadcast`. Anti-loop: broadcast messages stamped `from_bot_agent_id` (the existing `dispatch_summons` anti-loop already ignores bot-authored messages, so a broadcast won't trigger summons).

### Sketch (assuming DBOS scanner + resource-fetch disabled)
- A broadcast service `backend/app/services/chat/agent_broadcast.py`: given a completed task + its team, for each `auto_broadcast` agent mapped to that team's broadcast channel, compose a turn with resource-fetch DISABLED (or team-public-only), gate on `caps.auto_broadcast`, post via `chat_repository.send_message(sender_type='agent', from_bot_agent_id=...)`.
- A DBOS scheduled workflow scanning `task_tracking` for newly-completed broadcastable tasks past a watermark (respect the task-tracking architecture discipline in CLAUDE.md — read-only on task_tracking, never PATCH the trigger-owned columns).
- Admin/AI-Library: surface the `auto_broadcast` capability toggle (the `ChatCaps.auto_broadcast` field + parser already exist; the editor UI for it may need wiring).
- Tests: PERM-11 gate (no auto_broadcast → skip); SEC-AGENT-05 (broadcast turn cannot read user-scope — assert the resource-fetch handler is absent/restricted); anti-loop (broadcast doesn't re-summon).

### Why defer execution
E2 is the single most security-sensitive feature in the chat epic (autonomous agent output with no human authorization subject, hard constraint against touching user-private data). It deserves a dedicated session with fresh context and an explicit sign-off on the trigger mechanism + the content-safety enforcement, not a tail-end implementation. The capability field (`auto_broadcast`) already exists and is fail-closed (default false), so nothing is live until this is built.

---

## Execution Handoff
- E1 and E2 are SEPARATE branches/PRs. Resolve E1's Option A/B/C and E2's trigger/safety decisions first.
- Recommended order: E1 (agent DM, bounded, high value) → E2 (broadcast, security-critical, fresh session).
- Execute via superpowers:subagent-driven-development; final whole-branch review per feature; ship each.
