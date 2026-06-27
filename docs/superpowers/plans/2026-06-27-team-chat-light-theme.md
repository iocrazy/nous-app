# Team Chat — Light-Theme Token Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Migrate all Team Chat components off hardcoded dark hex onto the island semantic theme tokens so chat renders correctly in BOTH light and dark themes. Closes the module-wide light-theme debt logged across the chat epic. Frontend-only, no behavior change.

**Architecture:** The app theme is driven by `[data-theme="light|dark"]` on `<html>` (ThemeContext writes it). Semantic tokens are CSS custom properties in `frontend/index.css` that auto-flip per theme, exposed as Tailwind classes (`bg-island`, `bg-card`, `text-content`, `border-line`, …). Hardcoded hex does NOT flip → light theme is broken. This migration is a mechanical hex→token replacement per the canonical mapping table below, with ONE judgment rule: white-opacity used as a **border** must become a flipping `border-line*` token (an `rgba(255,255,255,.065)` border is invisible on a white panel); white-opacity used as a **non-border hover tint** stays as-is (reads fine in both themes).

**Tech Stack:** React 19 + Tailwind (CSS-var semantic tokens) — no `dark:` variants needed (tokens flip automatically).

## Global Constraints
- **Branch:** `feature/chat-light-theme` (off origin/master).
- **Behavior-neutral:** className-only changes. No logic, no markup structure, no prop changes. Dark theme must look IDENTICAL after migration (the tokens' dark values equal the old hexes).
- **The canonical mapping table is the spec — apply it verbatim:**

  | Current (hex / opacity) | Role | Replace with |
  |---|---|---|
  | `bg-[#15151a]` | panel/island bg | `bg-island` |
  | `bg-[#17171b]` | island-2 / hover bg | `bg-island-2` |
  | `bg-[#1d1d22]`, `bg-[#1c1c22]` | card bg | `bg-card` |
  | `bg-[#252529]` | card hover (darker) | `bg-island-2` |
  | `bg-[#09090b]` | input/darkest bg | `bg-app-bg` |
  | `bg-[#0e0e12]` | thumbnail placeholder bg | `bg-ink-950` |
  | `text-[#e7e7ea]` | primary text | `text-content` |
  | `text-[#a3a3ad]` | secondary text | `text-content-2` |
  | `text-[#74747e]` | muted text (timestamps/hints/placeholder) | `text-content-3` |
  | `text-[#6b6b75]`, `text-[#55555e]` | section headers / very muted | `text-content-4` |
  | **border** `border-white/[.12]`, `border-[rgba(255,255,255,.12)]` | strong border | `border-line-strong` |
  | **border** `border-white/[.065]`, `border-white/[.06]` (as border), `border-[rgba(255,255,255,.065)]` | subtle border | `border-line` |
  | NON-border `hover:bg-white/[.06]` / `bg-white/[.0xx]` tint | hover tint | **KEEP as-is** |
  | `indigo-500/[.12]`, `indigo-500/[.18]`, `indigo-500/[.35]`, `text-indigo-300`, `border-indigo-500/50` | indigo accent | **KEEP as-is** (accent doesn't flip) |
  | `bg-amber-400/…`, `text-amber-400`, `text-[#1a1505]`/`#1505..` on amber | agent treatment | **KEEP as-is** |
  | shadows `shadow-[…rgba(0,0,0,…)]`, `rgba(0,0,0,.x)` overlays | depth | **KEEP as-is** |

- **Judgment rule (the only non-mechanical part):** for each `white/[.0xx]` occurrence, decide border vs tint by the CSS property — if it's in a `border`/`border-b`/`border-t`/etc. utility → migrate to `border-line*`; if it's a `bg`/`hover:bg` → keep. When ambiguous, look at the element.
- **No `zinc-*`** (project rule). Use `ink-*` if a raw ladder value is needed.
- **i18n:** none (no text changes).
- **Verification per task:** `npx tsc --noEmit` (no new errors) + `npm run build`; grep the touched files for remaining `\[#[0-9a-f]` to confirm only intentional keeps (amber `#1a1505`, shadows) remain.

## File Structure (9 files, by hex density)
Heavy: `CreateGroupModal.tsx` (30), `MessageBubble.tsx` (25), `ChatSidebar.tsx` (18), `ResourcePicker.tsx` (16).
Light: `Composer.tsx` (12), `ChatPage.tsx` (8), `MessageList.tsx` (3), `MentionDropdown.tsx` (2), `TypingIndicator.tsx` (1).

---

## Task 1: Migrate the 4 heavy components

**Files:** `frontend/components/chat/CreateGroupModal.tsx`, `MessageBubble.tsx`, `ChatSidebar.tsx`, `ResourcePicker.tsx`.

- [ ] **Step 1:** For each file, apply the mapping table verbatim to every hardcoded hex + every white-opacity BORDER. Keep indigo/amber accents, non-border white tints, and shadows. Preserve all non-color classes, structure, and logic exactly. Pay attention to MessageBubble's agent amber avatar (`text-[#1a1505]` on amber — KEEP) and the indigo media-card accent (KEEP). ChatSidebar's section headers `text-[#6b6b75]` → `text-content-4`; active channel `text-indigo-300` KEEP; agent-DM amber row KEEP.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` (no new errors) + `npm run build`. Then `grep -nE "\[#[0-9a-fA-F]{3,8}\]" <the 4 files>` and confirm every remaining hex is an intentional keep (amber-on-agent `#1a1505`-type, or a shadow rgba) — list them in the commit body.
- [ ] **Step 3:** Commit — `refactor(chat): light-theme tokens for create-group/bubble/sidebar/picker`.

---

## Task 2: Migrate the 5 light components

**Files:** `frontend/components/chat/Composer.tsx`, `MessageList.tsx`, `MentionDropdown.tsx`, `TypingIndicator.tsx`, `frontend/pages/ChatPage.tsx`.

- [ ] **Step 1:** Apply the same mapping table verbatim to these 5 files. Note: `TypingIndicator.tsx` uses `bg-ink-500` already (a token — leave it) + `text-[#74747e]`→`text-content-3`. `MentionDropdown.tsx` agent amber tag KEEP; its `bg-[#1c1c22]`/`bg-[#1d1d22]` → `bg-card`, active row indigo KEEP. `ChatPage.tsx`: the conversation-island shell `bg-[#15151a] border border-white/[.12]` → `bg-island border border-line-strong`; the online-presence dot `bg-emerald-400` KEEP.
- [ ] **Step 2:** `npx tsc --noEmit` + `npm run build`; grep the 5 files for remaining `[#…]` and confirm only intentional keeps remain.
- [ ] **Step 3:** Commit — `refactor(chat): light-theme tokens for composer/list/mention/typing/page`.

---

## Self-Review
**Spec coverage:** all 9 chat module files migrated to semantic tokens; chat now renders in light theme. Dark theme byte-identical (token dark values == old hexes). Accents (indigo/amber) and depth (shadows) intentionally preserved.
**Deferrals:** AIChatPanel + AIChatDrawer (separate, not chat/ module — already theme-aware via earlier work / out of scope); any pixel-level light-theme polish after a real visual pass.
**Verification:** build + grep audit; true visual check via Vercel preview in BOTH themes (toggle theme, open chat: channels/sidebar/bubbles/composer/create-group/picker/agent-DM all legible in light).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review (focus: dark-theme parity + no border made invisible + accents preserved); then ship (frontend-only → Vercel + merge).
