# Team Chat — Agent DM Implementation Plan (E1)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let a user open a 1:1 chat with a chat-enabled agent from the Team Chat sidebar's Direct Messages section, reusing the existing `AIChatPanel` (which already renders agent sessions, tool-call traces, plan-mode, attachments). Closes CHAT-AGENT-06; CHAT-SEC-AGENT-04 (DM = personal scope) holds for free because this path reuses `ai_sessions` (the same path as standalone agent chat — the agent acts as the user calling it directly, never a team output boundary).

**Architecture (Option A — confirmed by user):** Adapt `AIChatPanel` to accept an optional `agentSlug` and make `projectId` optional. When `agentSlug` is set, the panel locks to that agent (no agent selector), lists/creates agent-scoped sessions with no project filter, and omits `project_id` from the create payload. Existing project-scoped callers (`AIChatDrawer` → ScriptEditor / CanvasEditor) pass `projectId` and are unchanged. The Team Chat sidebar lists chat-enabled agents as agent-DM rows; selecting one renders `<AIChatPanel agentSlug=… onClose=… />` in the main pane instead of the channel message view. Frontend-only — `ai_sessions` + `/agents/{slug}/sessions` APIs already support agent-only sessions; no backend, no migration.

**Tech Stack:** React 19 + existing `aiLibraryService` session APIs + `AIChatPanel` + i18next.

## Global Constraints
- **Branch:** `feature/chat-agent-dm` (off origin/master, which has A+B+C+D).
- **Do NOT break existing AIChatPanel callers.** `AIChatDrawer` (and via it ScriptEditorPage `contextType="script"`, CanvasEditorPage `contextType="storyboard"`) pass a required `projectId`. After the change, with `agentSlug` ABSENT the panel must behave EXACTLY as today (agent selector visible, project-scoped session list/create). Verify by reading those two callers; their call sites must compile and behave unchanged.
- **Agent-DM does NOT use the chat `channels`/`channel_messages`/`dispatch_summons` path.** It reuses `ai_sessions`. The team-scope iron-law machinery (run_channel_agent_turn, agent_channels) is untouched. This keeps CHAT-SEC-AGENT-04 trivially correct: a 1:1 agent DM is the user talking to the agent in their own personal scope, exactly like the standalone agent chat — no team output boundary involved.
- **Only chat-enabled agents appear as DMs:** reuse `aiLibraryService.listAgents()` filtered to `chat_permissions?.enabled === true` (same filter ChatPage already uses for composer mentions).
- **When `agentSlug` is set:** the in-panel AgentSelector is hidden (the agent is fixed); session list = `listChatSessions(agentSlug)` (no projectId); session create payload omits `project_id` (and team_id) — agent-only session.
- **Island UI:** zero emoji, no `zinc-*`; match existing chat + AIChatPanel styling. Agent-DM rows use the amber agent treatment (consistent with MessageBubble's agent tag).
- **i18n** for any new visible text (en+zh parity, valid JSON).
- **Verification:** `npx tsc --noEmit` (no NEW errors) + `npm run build` per task. AIChatPanel has a test file? If so keep it green.

## File Structure
- `frontend/components/AIChatPanel.tsx` — add optional `agentSlug?`, make `projectId?` optional, branch session list/create + hide selector when locked.
- `frontend/components/chat/ChatSidebar.tsx` — render chat-enabled agents as agent-DM rows in the DM section; `onSelectAgentDm(slug)` callback.
- `frontend/pages/ChatPage.tsx` — track selected agent-DM; render AIChatPanel in the main pane for an agent-DM; reuse the chat-enabled agents it already fetches.
- `frontend/public/locales/{en,zh}.json` — `chat.agentDm.*` keys if any new text.

---

## Task 1: Adapt AIChatPanel for optional agent-scoped mode

**Files:** Modify `frontend/components/AIChatPanel.tsx`. (Keep `AIChatDrawer.tsx` callers working — read them, don't change unless required for types.)

**Interfaces (Produces):** `AIChatPanelProps` gains `agentSlug?: string`; `projectId?: string` becomes optional. New behavior when `agentSlug` is provided (locked agent, agent-scoped sessions, no project filter). Default (no `agentSlug`) behavior is byte-for-byte the current behavior.

- [ ] **Step 1:** Read AIChatPanel fully (esp. lines ~46-53 props, ~225 `numericProjectId` memo, ~228-251 agents-load+auto-select effect, ~256-293 session-list effect, ~271-279 + ~317-324 create-session, ~416 post-send refresh, the AgentSelector render ~468). Read `AIChatDrawer.tsx` to confirm it always passes `projectId` (so it's unaffected).
- [ ] **Step 2: Props.** `projectId?: string` (optional); add `agentSlug?: string`. Compute `const lockedAgent = agentSlug ?? null;`. Where the panel resolves which agent to use, prefer `lockedAgent` over the user-selected `selectedAgentSlug`: e.g. `const effectiveAgentSlug = lockedAgent ?? selectedAgentSlug;` and use `effectiveAgentSlug` in the session list/create/refresh calls + `composerDisabled`.
- [ ] **Step 3: Agent auto-select + selector.** When `lockedAgent` is set: skip the auto-select-first-agent logic (or set `selectedAgentSlug = lockedAgent`), and DO NOT render the AgentSelector (hide it). When absent: unchanged.
- [ ] **Step 4: Session list.** In the session-list effect, call `aiLibraryService.listChatSessions(effectiveAgentSlug, lockedAgent ? undefined : numericProjectId)`. Update the effect dep array to use `effectiveAgentSlug` (and keep `numericProjectId`). When `lockedAgent` set, projectId is not used for filtering.
- [ ] **Step 5: Session create.** In BOTH create paths, build the payload omitting `project_id`/`context_*` when `lockedAgent` is set:
  ```ts
  const payload = lockedAgent
    ? { title: t('chat.newConversation', 'New conversation') }
    : { title: t('chat.newConversation', 'New conversation'), project_id: numericProjectId, context_type: contextType, context_id: contextId };
  ```
  (Agent-only session: no project/team/context.)
- [ ] **Step 6: Post-send refresh** (line ~416): use `effectiveAgentSlug` + the same conditional projectId.
- [ ] **Step 7:** Confirm nothing else hard-requires `projectId` (the grounding says `composerDisabled` does not depend on it; `numericProjectId` is `undefined`-safe). `cd frontend && npx tsc --noEmit` (no new errors; AIChatDrawer callers still compile) + `npm run build`. Commit — `feat(chat): AIChatPanel optional agentSlug + optional projectId (agent-scoped mode)`.

---

## Task 2: ChatSidebar — agent-DM rows

**Files:** Modify `frontend/components/chat/ChatSidebar.tsx`.

**Interfaces (Consumes/Produces):** Sidebar gains `agents: { slug: string; label: string }[]`, `activeAgentDm: string | null`, and `onSelectAgentDm: (slug: string) => void`. Render the agents as DM rows in the Direct Messages section.

- [ ] **Step 1:** Read ChatSidebar — the DM section (it already splits `dms = channels.filter(type==='dm')` and renders a "Direct Messages" section). Add the three new props (default `agents=[]`, `activeAgentDm=null`).
- [ ] **Step 2:** In the Direct Messages section, ALSO render `agents` as agent-DM rows: an amber agent glyph/avatar + the agent label, highlighted when `activeAgentDm === slug`, click → `onSelectAgentDm(slug)`. Keep human DM channels rendering as-is. Ensure selecting an agent-DM visually deselects any active channel (the parent owns that state). Island styling, zero emoji.
- [ ] **Step 3:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): agent-DM rows in chat sidebar`.

---

## Task 3: ChatPage — route agent-DM selection to AIChatPanel + i18n

**Files:** Modify `frontend/pages/ChatPage.tsx`, `frontend/public/locales/{en,zh}.json`.

**Interfaces (Consumes):** AIChatPanel `agentSlug` (Task 1); ChatSidebar `agents`/`activeAgentDm`/`onSelectAgentDm` (Task 2).

- [ ] **Step 1:** ChatPage already fetches chat-enabled agents for composer mentions (`agentsForComposer`: `{slug,label}`). Reuse that list for the sidebar `agents` prop.
- [ ] **Step 2: State.** Add `const [activeAgentDm, setActiveAgentDm] = useState<string | null>(null);`. Selecting an agent-DM: `setActiveAgentDm(slug); setActiveId(null);` (deselect any channel). Selecting a channel: `setActiveId(id); setActiveAgentDm(null);` (clear agent-DM) — update the existing channel-select handler to clear `activeAgentDm`.
- [ ] **Step 3: Wire sidebar.** Pass `agents={agentsForComposer}`, `activeAgentDm={activeAgentDm}`, `onSelectAgentDm={(slug)=>{ setActiveAgentDm(slug); setActiveId(null); }}` to `<ChatSidebar>`.
- [ ] **Step 4: Main pane.** When `activeAgentDm` is set, render `<AIChatPanel agentSlug={activeAgentDm} onClose={() => setActiveAgentDm(null)} />` in the main content area INSTEAD of the channel view (MessageList + Composer + TypingIndicator). When `activeAgentDm` is null, render the existing channel view. (AIChatPanel is imported from `../components/AIChatPanel`.) Ensure the channel-only hooks (realtime, presence, gapFill) are not driven while an agent-DM is open — they key on `activeId`, which is null in agent-DM mode, so they no-op; confirm no crash.
- [ ] **Step 5: i18n.** Add any new visible strings (e.g. a DM-section subheading for agents if you add one) under `chat.agentDm.*` to en+zh. If no new strings are introduced (labels come from agent names), skip. Keep parity + valid JSON.
- [ ] **Step 6:** `npx tsc --noEmit` + `npm run build` + JSON parse check. Commit — `feat(chat): open agent DM (AIChatPanel) from sidebar`.

---

## Self-Review
**Spec coverage:** CHAT-AGENT-06 (agent DM surfaced in chat, reusing AIChatPanel/ai_sessions, not rebuilt) + CHAT-SEC-AGENT-04 (DM = summoner personal scope — inherent because it reuses the standalone agent-chat `ai_sessions` path, no team channel/output boundary).
**Deferrals:** persisting the last-open agent-DM across reloads; unread/badges for agent DMs (ai_sessions has no unread model — out of scope); agent broadcast (E2, deferred to a fresh session per user). 
**No-break guarantee:** with `agentSlug` absent, AIChatPanel is unchanged → AIChatDrawer/ScriptEditor/CanvasEditor unaffected. This is the single highest risk; the final review must confirm the existing callers' behavior is byte-for-byte preserved.
**Verification:** build-level + the no-break check; true E2E after deploy (open an agent DM from the sidebar, send a message, get a reply, switch back to a channel and confirm channel chat still works).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review (focus: existing-caller regression + agent-scoped session correctness); then ship (frontend-only → Vercel + merge).
