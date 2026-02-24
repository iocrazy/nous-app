# Team Settings — Rename & Delete Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Team Settings" tab to SettingsModal allowing team owners to rename or permanently delete a team (with typed-name confirmation and full data cleanup).

**Architecture:** Reuse existing Supabase-direct pattern (no backend API). Add a PostgreSQL RPC function for transactional cascade deletion of soft references. Frontend adds a TeamSettings component inside the existing SettingsModal.

**Tech Stack:** PostgreSQL function (RPC), React + TypeScript, i18next

---

## Context

### Current State
- `SettingsModal` has ACCOUNT (Personal Settings) + APP SETTINGS (7 tabs) — no team tab
- `useTeams.ts` already has `handleTeamSettings()` that sets `initialTab('team')` and opens the modal
- `teamService.ts` has `updateTeam()`, `deleteTeam()` via Supabase direct queries
- All team CRUD goes through Supabase client (no backend teams_router)

### Problem
When deleting a team, PostgreSQL CASCADE handles hard FK references (team_members, team_invites, team_quotas, etc.), but **soft references** using `scope_type + scope_id` TEXT pattern are NOT cleaned up:
- `folders` (scope_type='team', scope_id=team_id)
- `resource_items` (scope_type='team', scope_id=team_id)
- `tags` (scope_type='team', scope_id=team_id)
- `smart_collections` (scope_type='team', scope_id=team_id)

Additionally, `projects.team_id` uses SET NULL (orphaned projects remain).

### Decision
**Hard Delete All** — delete all associated data when a team is deleted. Resources themselves survive (they may have personal scope references).

---

## Implementation

### Step 1: SQL Migration — `delete_team_with_cleanup()` RPC

**File:** `supabase/migrations/078_team_delete_cascade.sql`

Creates a PostgreSQL function that:
1. Verifies the caller is the team owner
2. Deletes soft references: folders, resource_items, tags, smart_collections (where scope_type='team' AND scope_id=team_id)
3. Deletes projects where team_id = target
4. Deletes the team row (CASCADE handles team_members, team_invites, etc.)

Called via `supabase.rpc('delete_team_with_cleanup', { target_team_id: teamId })`.

### Step 2: Frontend Service — Update `deleteTeam()`

**File:** `frontend/services/teamService.ts`

Change `deleteTeam()` from direct `.delete()` to RPC call:
```typescript
export const deleteTeam = async (teamId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');
  const { error } = await supabase.rpc('delete_team_with_cleanup', {
    target_team_id: teamId,
  });
  if (error) throw error;
};
```

### Step 3: Frontend Component — `TeamSettings.tsx`

**File:** `frontend/components/TeamSettings.tsx`

Layout:
```
┌──────────────────────────────────────┐
│  Team Name                           │
│  ┌──────────────────┐  [Save]        │
│  │ Current Name     │                │
│  └──────────────────┘                │
│                                      │
│  ─────── Danger Zone ───────         │
│                                      │
│  Delete Team                         │
│  Permanently delete this team and    │
│  all its resources, projects, and    │
│  folders.                            │
│                                      │
│  [Delete This Team]  (red button)    │
│                                      │
│  ┌─ Confirmation Dialog ──────────┐  │
│  │ Type "Team Name" to confirm    │  │
│  │ ┌─────────────────────┐       │  │
│  │ │                     │       │  │
│  │ └─────────────────────┘       │  │
│  │                               │  │
│  │ ⚠ This will permanently       │  │
│  │   delete:                     │  │
│  │   • All team folders          │  │
│  │   • All team resources        │  │
│  │   • All team projects         │  │
│  │   • All team tags             │  │
│  │   • All team members          │  │
│  │                               │  │
│  │ [Cancel]  [Delete Permanently] │  │
│  └───────────────────────────────┘  │
└──────────────────────────────────────┘
```

Props:
```typescript
interface TeamSettingsProps {
  team: Team;
  onTeamUpdated: (team: Team) => void;
  onTeamDeleted: (teamId: string) => void;
  onClose: () => void;  // close modal after delete
}
```

Behavior:
- **Rename**: Input + Save button, calls `updateTeam(teamId, { name })`
- **Delete**: Red button opens inline confirmation. User must type exact team name. "Delete Permanently" button only enabled when name matches.
- After successful delete: call `onTeamDeleted(teamId)`, close modal

### Step 4: SettingsModal Integration

**File:** `frontend/components/SettingsModal.tsx`

Changes:
1. Add `'team'` to `SettingsTab` union type
2. Add new props: `currentTeam`, `onTeamUpdated`, `onTeamDeleted`
3. Dynamically add TEAM nav section when `currentTeam` exists (team mode)
4. Render `<TeamSettings>` when `activeTab === 'team'`

### Step 5: AppLayout — Pass team props to SettingsModal

**File:** `frontend/components/AppLayout.tsx`

Pass `currentTeam`, `handleTeamUpdated`, `handleTeamDeleted` from TeamContext to SettingsModal.

### Step 6: i18n

**Files:** `frontend/public/locales/en.json`, `zh.json`

Keys under `settings.team`:
- `title`, `teamName`, `save`, `dangerZone`, `deleteTeam`, `deleteDescription`, `deleteButton`, `deleteConfirmTitle`, `deleteConfirmPrompt`, `deleteConfirmWarning`, `deleteItems.*`, `cancel`, `deleteConfirmButton`, `renamed`, `deleted`

---

## Verification

1. `npm run build` passes
2. Execute migration on local Supabase
3. Open Settings in team mode → see "Team Settings" tab
4. Rename team → refresh → name persists
5. Delete team → type name → confirm → redirected to personal workspace
6. Verify: team folders, resource_items, tags, projects all cleaned up in DB
