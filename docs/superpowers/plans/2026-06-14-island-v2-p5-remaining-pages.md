# Island Redesign v2 — P5: Remaining Pages (Projects / Issues / AI Library / Settings) + flag flip

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** P2/P3/P4 merged — island infra (`IslandWorkContext`, `IslandShell`, `iconRail`, `islandUI()`) on master. Flag `VITE_FEATURE_ISLAND_UI` is **OFF in prod**. P5 is the LAST migration phase; **after P5 ships AND the user signs off on a real-machine dogfood of P2–P5, the flag is flipped ON in prod** (Task 6).

**Goal:** Make the remaining routed pages — **Projects, Issues, AI Library, Settings** — render correctly inside the island work island and comply with the design system (no layout escapes, semantic tokens / tinted buttons, no `*-zinc-*`/raw palette, no viewport-escaping `fixed` overlays), with zero feature change (D12). These pages already render *inside* the frame (P1b wraps the `<Outlet/>`); P5 fixes their layout gotchas + token compliance. No mockups exist for these pages, so P5 is **frame-fit + design-system compliance, not a redesign** — info-island portaling of detail panes is explicitly OUT of scope (optional future polish).

**Architecture (per-page `islandUI()` branch; classic byte-identical):** Each page gets an island-mode branch that removes the classic layout hacks (negative margins that cancel AppLayout padding, `height:100vh`, `pt-20` top offsets assuming the old fixed TopBar) and instead fills the work island with `h-full min-h-0 flex` + internal scroll. Issues additionally migrates raw Tailwind palette colors → semantic `ink-*`/`btn-tint-*` tokens and makes `NewIssueModal` portal-safe. Settings is verified-first (likely no change). Everything is `islandUI()`-gated; flag-OFF renders today's DOM.

**Tech Stack:** React 19 + TS, Tailwind v4 island tokens, `islandUI()`/`useIslandWork` (reuse), `react-dom` portal (for modal fix), vitest.

**Hard constraints:**
- **D12 zero feature change** — flag-OFF (and mobile) byte-identical; every page's full interaction set preserved.
- New/migrated code uses `ink-*`/semantic tokens + `.btn-tint-*` only — **no `*-zinc-*`** and no raw `gray-*/blue-*/red-*/amber-*/green-*/purple-*` palette for chrome (semantic colors like a status hue may stay only if intentional + not zinc; prefer tokens). CI guard `scripts/check-no-zinc.sh`.
- tsc baseline (currently **126**) must not increase; vitest stays green (currently **961**).
- Commit each task; flag stays OFF until Task 6's gated flip.

**Out of scope:** Info-island portaling of detail panes (Projects detail / Issues detail) — optional future polish, not needed to render correctly. Functional redesign of any page. Backend changes. Mobile redesign (island frame is `hidden sm:flex`; mobile layouts untouched).

---

## D12 Feature-Parity Checklist (per page — re-verify with flag ON)

- [ ] **Projects** (`pages/ProjectsPage.tsx` 266L; `components/project/ProjectFilterSidebar.tsx` 140L, `ProjectNavSidebar.tsx` 345L+): list route `/team/:teamId/projects` + detail `/projects/:projectId`; filter sidebar + nav sidebar (Files/Scripts/Storyboard/Output/Tasks/Shares/Trash) + tab content (`ProjectFilesView`/`ProjectScriptsTab`/`KanbanBoard`/…); `CreateProjectModal`. All filters/tabs/nav/CRUD intact.
- [ ] **Issues** (`pages/IssuesPage.tsx` 486L, monolithic): list `/team/:teamId/issues` + detail `/issues/:identifier`; split-pane (filter pills + `w-96` list | `IssueDetail`); `FilterPill`/`IssueListRow`/`IssueDetail`/`NewIssueModal`. Status filters, list, detail (chat/@-ref/reply), new-issue modal all intact.
- [ ] **AI Library** (`components/AILibrary/AILibraryLayout.tsx` 38L + `AILibrarySidebar.tsx` 350L+ + nested `AgentsPage`/`SkillsPage`/`UsagePage`/`WorkforcePage`/`MemoryViewerPage`): secondary sidebar + `<Outlet/>`; all nested routes + sidebar nav intact.
- [ ] **Settings** (`pages/SettingsPage.tsx` 28L → `components/SettingsView.tsx` 978L): tabs general/api/logs/monitor/tasks/tags/ai/docs/cookies; `embedded` modal mode; account `/settings` + team `/team/:teamId/settings`. All tabs intact in page AND modal mode.

---

## File Structure

- **Modify** `frontend/pages/ProjectsPage.tsx` — `islandUI()` branch: drop `-mx-* -mt-* -mb-*` + `style={{height:'100vh'}}` + `pt-20`; use `h-full min-h-0 flex` filling the work island. Classic branch byte-identical.
- **Modify** `frontend/components/AILibrary/AILibraryLayout.tsx` — same negative-margin/`100vh` removal in island mode (it uses the identical `-mx-4 -mt-14 -mb-20 sm:-mx-8 sm:-mt-20 sm:-mb-8` + `height:100vh` at line ~21).
- **Modify** `frontend/pages/IssuesPage.tsx` — island branch (the split-pane fills the work island; it has no negative margins, so mostly a height/scroll check) + **token migration** (raw palette → semantic `ink-*`/`btn-tint-*`, lines ~32-47 status colors, ~157/246/474 buttons) + make `NewIssueModal` (`fixed inset-0`, line ~450) render via a portal to `document.body` so it covers the viewport correctly in both modes (it already uses `fixed inset-0` which is viewport-relative — verify it isn't clipped by the work island's `overflow-auto`; if clipped, `createPortal` to body).
- **Modify** `frontend/components/SettingsView.tsx` (+ `SettingsPage.tsx` if needed) — **verify-first**; only change if it doesn't render correctly in the work island (it has no negative margins + uses `max-w-5xl mx-auto` + semantic tokens, so likely no change). Ensure any sub-modal that uses `fixed` isn't clipped.
- **Modify** `frontend/index.css` — only if a shared helper is needed (avoid; prefer per-page Tailwind).

---

### Task 1: Projects — island frame fit

**Files:** `frontend/pages/ProjectsPage.tsx` (read it first; gotchas at lines ~193, 207, 233, 244).

- [ ] **Step 1:** Read `ProjectsPage.tsx`. Identify the two layout roots (mobile ~193, desktop ~233) using `-mx-4 -mt-14 -mb-20 sm:-mx-8 sm:-mt-20 sm:-mb-8` + `style={{height:'100vh'}}` and the `pt-20` content offset (~207, 244).
- [ ] **Step 2:** Add `const island = islandUI();` (import from `../utils/featureFlags`). Branch the DESKTOP layout: in island mode render the SAME `[ProjectFilterSidebar | content | ProjectNavSidebar]` structure but with the root as `className="flex h-full min-h-0"` (NO negative margins, NO `height:100vh`) and the content area WITHOUT `pt-20` (the global topbar is outside the work island). Classic desktop + mobile branches **byte-identical** (gate the island root with `island`, keep the existing roots for `!island`). Don't change the sidebars'/tabs' internals — only the outer layout wrapper.
- [ ] **Step 3:** `CreateProjectModal` — it's a portal/overlay; verify it still centers over the viewport in island mode (if it uses `fixed inset-0`, it's viewport-relative and fine; if it's clipped by the work island's `overflow-auto`, portal it to `document.body`). Change only if needed.
- [ ] **Step 4:** tsc 126, vitest green, `npm run build` OFF & ON, `scripts/check-no-zinc.sh` OK.
- [ ] **Step 5:** commit `feat(projects): island frame fit — drop negative-margins/100vh in island mode (v2 P5 Task 1)`

---

### Task 2: AI Library — island frame fit

**Files:** `frontend/components/AILibrary/AILibraryLayout.tsx` (line ~21 has the identical `-mx-4 -mt-14 -mb-20 sm:-mx-8 sm:-mt-20 sm:-mb-8` + `style={{height:'100vh'}}`).

- [ ] **Step 1:** Read `AILibraryLayout.tsx`. It's the same pattern as Projects: `<div className="flex -mx-4 -mt-14 -mb-20 sm:-mx-8 sm:-mt-20 sm:-mb-8" style={{height:'100vh'}}>[AILibrarySidebar][<Outlet/>]</div>`.
- [ ] **Step 2:** `const island = islandUI();`. In island mode render `<div className="flex h-full min-h-0">[AILibrarySidebar][<Outlet/>]</div>` (no negative margins, no `100vh`). Classic byte-identical (`!island` keeps the existing root). `AILibrarySidebar` is already token-compliant — don't touch it.
- [ ] **Step 3:** Verify the nested pages (`AgentsPage`/`SkillsPage`/`UsagePage`/`WorkforcePage`/`MemoryViewerPage`) scroll correctly inside the work island (the `<Outlet/>` content area should be `flex-1 min-w-0 overflow-auto`).
- [ ] **Step 4:** tsc 126, vitest green, build OFF & ON, no-zinc OK.
- [ ] **Step 5:** commit `feat(ai-library): island frame fit — drop negative-margins/100vh in island mode (v2 P5 Task 2)`

---

### Task 3: Issues — island fit + token migration + modal portal

**Files:** `frontend/pages/IssuesPage.tsx` (486L, monolithic — read it first; raw colors ~32-47, buttons ~157/246/474, `NewIssueModal` `fixed inset-0` ~450).

- [ ] **Step 1:** Read `IssuesPage.tsx`. Note: no negative margins (good); split-pane `[filter pills + w-96 list | IssueDetail]`.
- [ ] **Step 2: Island fit.** `const island = islandUI();`. Ensure the split-pane root fills the work island in island mode: `h-full min-h-0 flex` with the list pane `w-96 shrink-0 overflow-auto` and the detail pane `flex-1 min-w-0 overflow-auto`. If the classic root already works inside the frame (no negative margins / no `100vh`), the island delta may be just height/scroll classes — apply island-gated only where the classic differs (keep classic byte-identical). If classic already uses `h-full`/flex correctly, no island branch is needed for layout — then Step 2 is a no-op and you proceed to tokens.
- [ ] **Step 3: Token migration (applies to BOTH themes/modes — this is design-system compliance, not flag-gated).** Replace raw palette chrome colors with semantic tokens:
  - Status chips (`bg-gray-200 text-gray-800`, `bg-blue-100 text-blue-800`, `bg-amber-100`, `bg-red-100`, `bg-purple-100`, `bg-green-100`, etc. ~lines 32-47): map neutral → `ink-*` (e.g. `bg-ink-800 text-ink-200`); keep MEANINGFUL status hues but as non-zinc semantic equivalents (e.g. open=indigo, done=green, blocked=red) using the existing color scale the app uses elsewhere (check `IssueDetailView`/labels for the canonical status colors — reuse them, don't invent). The goal: no `*-zinc-*`, no bare `gray-*` for neutral chrome; status semantic colors are fine.
  - Buttons (`bg-indigo-600 text-white hover:bg-indigo-700` ~157/246/474): replace with `.btn-tint-indigo` (the design system's tinted button) to match Resources/detail pages.
  - Borders/neutral bg (`border-gray-200`, `bg-gray-100`, `text-gray-700`, `text-gray-500`): → `border-line`/`border-ink-800`, `bg-ink-900`, `text-ink-300`, `text-ink-500`.
  - ⚠️ This is a real change to the page's appearance in BOTH classic and island. Since the page currently uses light-ish raw grays, the token migration makes it theme-correct (dark/light). This is intended design-system compliance (D11). It does NOT change behavior (D12 = features, not pixels) — but call it out in the PR. Verify against both themes.
- [ ] **Step 4: NewIssueModal portal.** The modal uses `fixed inset-0` (~450). `fixed` is viewport-relative so it should overlay correctly even inside the work island; BUT verify it isn't clipped by an ancestor `overflow:hidden`/`transform` (the `.island-card` / work island). If clipped, wrap its render in `createPortal(modalJsx, document.body)`. Change only if needed.
- [ ] **Step 5:** tsc 126, vitest green, build OFF & ON, `scripts/check-no-zinc.sh` OK, and `grep -nE "bg-gray-|text-gray-|bg-blue-100|bg-amber-100|bg-red-100|bg-purple-100|bg-green-100" frontend/pages/IssuesPage.tsx` returns nothing (chrome migrated).
- [ ] **Step 6:** commit `feat(issues): island fit + semantic-token migration + modal portal (v2 P5 Task 3)`

---

### Task 4: Settings — verify in island (likely no-op)

**Files:** `frontend/components/SettingsView.tsx` (978L) / `frontend/pages/SettingsPage.tsx` — verify-first.

- [ ] **Step 1:** Read the SettingsView root layout. Confirm it has NO negative margins / NO `height:100vh` / uses `max-w-5xl mx-auto` + semantic tokens. Confirm it renders correctly inside the work island (the work island is `overflow-auto`; SettingsView should scroll internally fine).
- [ ] **Step 2:** If it renders correctly with no layout escape → **no code change** (document this in the task report, like P2/P4 verify-first no-ops). If a sub-modal (API key modal etc.) uses `fixed` and is clipped, portal it to `document.body` — change only that.
- [ ] **Step 3:** `grep -nE "bg-zinc-|text-zinc-|border-zinc-" frontend/components/SettingsView.tsx` — if any (CI only bans NEW zinc; existing may remain), leave unless trivial. Do NOT do a broad refactor here (out of scope).
- [ ] **Step 4:** tsc 126, vitest green, build OFF & ON, no-zinc OK.
- [ ] **Step 5:** commit `feat(settings): verify island frame fit (v2 P5 Task 4)` (or note "no code change — renders correctly" and skip the commit if nothing changed).

---

### Task 5: Verify all P5 pages + ship (flag still OFF)

- [ ] **Step 1:** Full verification — tsc 126, vitest green, `npm run build` (OFF & ON), `scripts/check-no-zinc.sh`.
- [ ] **Step 2:** Visual dogfood (REQUIRES auth — throwaway local account against local Supabase, or hand to user): with flag ON, open Projects / Issues / AI Library / Settings. Verify each fills the work island with NO content escaping the island (no overflow past the rounded card), the secondary sidebars are inside the work island, scroll works (no double scrollbar), modals overlay correctly, light + dark themes readable, and classic (flag OFF) is unchanged. Run each page's D12 checklist.
- [ ] **Step 3:** ⚠️ **Mandatory parallel-collision check before PR** (P2 lesson): `git fetch origin master && git diff <base> origin/master -- frontend/pages/ProjectsPage.tsx frontend/pages/IssuesPage.tsx frontend/components/AILibrary/AILibraryLayout.tsx frontend/components/SettingsView.tsx`. Reconcile if master moved them.
- [ ] **Step 4:** Version bump (read `origin/master` first), PR with D12 statement (+ the Issues token-migration note) + ship chain (public → CI → merge → Vercel Production row + `curl` live version → inflight=0 → private). `gh pr merge` then `gh pr view` MERGED. **Flag stays OFF in this PR.**
- [ ] **Step 5:** Update memory `project_island_redesign.md` item 8 → P5 shipped (flag still OFF; Task 6 = the gated flip).

---

### Task 6: Flag flip (GATED — requires user dogfood sign-off)

> This is the milestone: turning the island UI ON for all users. It is a deliberate, user-gated step — NOT auto-executed.

- [ ] **Step 1: User dogfood sign-off.** The user must dogfood P2–P5 (resources / video / audio / projects / issues / AI library / settings) in a real authenticated session (local `VITE_FEATURE_ISLAND_UI=true npm run dev`, or a preview) and confirm everything looks/works right. The reviewer cannot do this headless (auth + real data). **Do not flip the flag until the user explicitly signs off.**
- [ ] **Step 2: Flip in prod.** Set `VITE_FEATURE_ISLAND_UI=true` in the **Vercel project env** (frontend prod) and redeploy (a Vercel env change needs a redeploy/rebuild — pushing a no-op or re-running the deploy). Confirm the live site renders the island frame. (Note: the flag is build-time `import.meta.env`, so it's a Vercel **Environment Variable** + rebuild, not a runtime toggle.)
- [ ] **Step 3: Post-flip verification.** curl/visit prod, confirm island UI is live; smoke-test the core flows; watch for errors (Supabase logs / frontend_error_logs).
- [ ] **Step 4: Cleanup window.** Per memory's feature-flag rule (remove flag within 2 weeks of going live), schedule removal of the `VITE_FEATURE_ISLAND_UI` flag + the classic-mode branches in a follow-up once the island UI is confirmed stable in prod.
- [ ] **Step 5:** Update memory `project_island_redesign.md` → island v2 fully live (flag ON); note the flag-removal cleanup follow-up.

---

## Self-Review

**1. Coverage:** Projects (Task 1), AI Library (Task 2), Issues (Task 3), Settings (Task 4) — the 4 "remaining pages" from spec §6 P5. Each gets island frame-fit; Issues also gets token compliance + modal portal. Flag flip (Task 6) is the §6 "完成后开 flag" milestone, gated on user dogfood. ✅

**2. Placeholder scan:** Tasks 1-4 say "read the file first, gate island-only, keep classic byte-identical, change only if needed" rather than dictating exact diffs, because these are existing pages whose precise wrappers must be read at execution (and Settings/modal fixes are conditional on what the verify finds). The gotcha lines are cited from the inventory. The token-migration mapping (Task 3 Step 3) is concrete (which classes → which tokens). No TBD.

**3. Type/pattern consistency:** Every page uses the same `const island = islandUI();` + island-gated layout-root swap pattern established in P2 (`ResourcesShell`). `useIslandWork` is NOT needed (no info-island portaling in P5 — out of scope). `createPortal` only for clipped modals.

**4. Risks:**
- *Issues token migration changes appearance in classic too* (it's design-system compliance, not flag-gated) — this is intended (D11) but is the one P5 change that alters the flag-OFF render. Call it out in the PR; verify both themes; it must not change behavior (D12). If the user wants it flag-gated instead, gate it — but token compliance is generally desired app-wide.
- *Negative-margin removal*: the classic pages NEED the negative margins (to escape AppLayout padding); only the island branch drops them. Keep classic byte-identical — diff-review.
- *Modals clipped by the work island's `overflow-auto`*: `fixed` is viewport-relative and usually fine, but an ancestor `transform`/`overflow` can clip it — verify each modal in island mode; portal to body if clipped.
- *Settings is likely a no-op* — don't manufacture changes; verify-first.
- *Flag flip is build-time* (Vercel env + rebuild), gated on user dogfood — never auto-flip.
- *Parallel collision*: Task 5 Step 3 mandatory.
