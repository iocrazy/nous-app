# Team Chat — Create Group / Add-Agent UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Close the #1 usability gap: users can create a group, add teammates, and add chat-enabled agents — all from the UI. Today Team Chat is an empty dead-end (channels exist only via API). All backend endpoints already exist; this is frontend wiring.

**Architecture:** A `CreateGroupModal` (name + visibility + member multi-select + agent multi-select) opened from the ChatSidebar "+" button. On submit it calls `chatService.createChannel` → then `addMembers` / `addAgent` for the selected people/agents → then the parent refreshes the channel list and selects the new channel. Member candidates come from the current team's members; agent candidates are AI-Library agents filtered to `chat_permissions.enabled === true`.

**Tech Stack:** React 19 + TS + Tailwind (island tokens), existing `chatService` (createChannel/addMembers/addAgent), `aiLibraryService` (list agents), team members source, i18next.

## Global Constraints
- **Branch:** `feature/team-chat-ui` (off origin/master, has PHASE-0/1/2).
- **Visual source:** mockup `docs/superpowers/specs/2026-06-25-team-chat-mockup-states.html` screen D (create-group modal: name, Private group / Public segmented, member+agent pills, Cancel / Create). Island tokens, zero emoji, no `zinc-*`.
- **Backend is ready (do not change):** `POST /api/v1/chat/channels` (create, body `{type,team_id,name,history_mode?,member_ids?}`), `POST /api/v1/chat/channels/{id}/members` (`{user_ids}`), `POST /api/v1/chat/channels/{id}/agents` (`{agent_slug}`). `chatService` already wraps all three.
- **Agent candidates:** `aiLibraryService` list agents → filter `agent.chat_permissions?.enabled === true` (the backend serves `chat_permissions` on AgentOut). Only chat-enabled agents are addable (matches PERM-08 backend gate; the modal just shouldn't offer non-enabled ones).
- **Member candidates:** the current team's members (find the existing team-members service/hook — teamService or a members hook used by MembersPage).
- **Team scoping:** the modal creates in `useTeamContext().selectedTeamId`. `member_ids` can be passed to createChannel directly (it accepts member_ids); agents added via addAgent after create.
- **Verification:** no FE unit-test infra — `npx tsc --noEmit` (no NEW errors) + `npm run build` per task; visual check via the mockup. Live E2E after deploy.
- **Lint/build before commit.** i18n keys for all visible text.

## File Structure
- `frontend/services/aiLibraryService.ts` — confirm/add `listAgents()` (likely exists); used to source chat-enabled agents.
- `frontend/components/chat/CreateGroupModal.tsx` — the modal (new).
- `frontend/components/chat/ChatSidebar.tsx` — wire the "+" button to open the modal (it currently has a "+" icon-btn; add an `onNew` prop or local state).
- `frontend/pages/ChatPage.tsx` — host the modal; on create, refresh channels + select the new one.
- `frontend/public/locales/{en,zh}.json` — `chat.createGroup.*` keys.

---

## Task 1: Data sources — chat-enabled agents + team members

**Files:** Modify `frontend/services/aiLibraryService.ts` (if no list method) ; Test: `npx tsc`.

**Interfaces (Produces):** a way for the modal to fetch (a) chat-enabled agents `[{slug,name}]` and (b) current team members `[{user_id,name/email}]`.

- [ ] **Step 1:** Inspect `frontend/services/aiLibraryService.ts` — it almost certainly has a `listAgents()` / `getAgents()` returning `AILibraryAgent[]` (each now has `chat_permissions`). If present, no change. If absent, add `listAgents(): Promise<AILibraryAgent[]>` calling `GET /ai-library/agents`. Inspect how `MembersPage`/`teamService` fetches team members (the member picker reuses that). Document the two call sites for Task 2. No new component yet.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` (clean for any change). Commit (only if changed) — `chore(chat): ensure agent/member list sources for create-group`. If nothing changed, skip the commit and note the existing methods in the report.

---

## Task 2: CreateGroupModal component

**Files:** Create `frontend/components/chat/CreateGroupModal.tsx`.

**Interfaces (Produces):** `<CreateGroupModal teamId open onClose onCreated />` where `onCreated(channel: Channel)` fires after successful creation.

- [ ] **Step 1: Build the modal** translating mockup-states.html screen D into React:
  - Fields: group name (text), visibility segmented (`group` = Private group / `public` = Public — default `group`), member multi-select (team members as toggle-able pills/checkboxes), agent multi-select (only `chat_permissions.enabled` agents).
  - On mount (when `open`): fetch team members + chat-enabled agents (Task 1 sources); show a loading state.
  - Submit ("Create group"): `const ch = await chatService.createChannel({ type, team_id: teamId, name, member_ids: selectedMemberIds })`; then for each selected agent `await chatService.addAgent(ch.id, agent.slug)` (wrap each in try/catch + toast on per-agent failure so one bad agent doesn't lose the group); then `onCreated(ch)` + `onClose()`. Disable Create while submitting; validate non-empty name.
  - Errors → `addToast(..., 'error')`. Island tokens, zero emoji, no zinc. All text via `t('chat.createGroup.*')`.
- [ ] **Step 2:** `npx tsc --noEmit` + `npm run build` succeed. Commit — `feat(chat): CreateGroupModal (name + visibility + member/agent pickers)`.

---

## Task 3: Wire modal into sidebar + page + i18n

**Files:** Modify `frontend/components/chat/ChatSidebar.tsx`, `frontend/pages/ChatPage.tsx`, `frontend/public/locales/{en,zh}.json`.

- [ ] **Step 1: Sidebar "+" opens the modal.** ChatSidebar has a header "+" icon-btn (the New group/DM affordance). Add an `onNew?: () => void` prop and call it on click (keep the component presentational — the page owns modal state). 
- [ ] **Step 2: ChatPage hosts the modal.** Add `const [showCreate, setShowCreate] = useState(false)`. Pass `onNew={() => setShowCreate(true)}` to ChatSidebar. Render `<CreateGroupModal teamId={selectedTeamId} open={showCreate} onClose={() => setShowCreate(false)} onCreated={(ch) => { setShowCreate(false); /* refresh channels + select */ reloadChannels(); setActiveId(ch.id); }} />`. Reuse the existing channel-load function (the one that runs on mount) for `reloadChannels`; if it's inline, extract it to a callable.
- [ ] **Step 3: i18n.** Add `chat.createGroup` keys to en.json + zh.json: `title` ("New group"/"新建群组"), `nameLabel`, `namePlaceholder`, `visibility`, `private`, `privateDesc`, `public`, `publicDesc`, `members`, `agents`, `add`, `cancel`, `create`, `creating`, `noAgents`, `created` (toast). Verify both parse.
- [ ] **Step 4:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): wire CreateGroupModal into sidebar + chat page + i18n`.

---

## Self-Review
**Spec coverage:** closes the create-group UI gap (part of CHAT-OPEN-02 / PERM-05-adjacent / the PHASE-1 deferred "create-group modal"). Agent picker only offers `chat_permissions.enabled` agents (aligns with the PERM-08 backend gate, which still enforces server-side). Member picker scoped to current team.
**Deferrals:** DM creation (1:1) — this slice does group/public; DM-create can reuse the same modal later. Agent @-autocomplete in composer — separate. Removing members/agents — separate.
**Verification:** build-level + visual; true E2E after deploy (create a group in prod, add an agent, @mention it).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final review; then /ship.
