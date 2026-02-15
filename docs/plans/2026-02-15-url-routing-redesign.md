# URL Routing Redesign — Team-Scoped URLs

## Context

Current app URLs are flat (`/parser`, `/resources`, `/projects`) with workspace context stored in `localStorage`. This causes:
- URLs are not shareable (recipient sees their own workspace, not the sender's)
- Multi-tab isolation impossible (tabs share localStorage state)
- Browser history cannot distinguish same page under different workspaces
- URLs don't look professional (no workspace context)

Reference: Figma uses `figma.com/files/team/SNOWFLAKE_ID/drafts?fuid=USER_ID` — team and user IDs embedded in URL.

## Design Decision

**All business routes prefixed with `/t/:teamId/`**, including personal workspace (which is also a team with `is_personal=true`).

## URL Structure

### Format

```
/t/:teamId/:view
/t/:teamId/:view/:subId
/t/:teamId/:view/:subId/:action/:actionId
```

### Route Mapping

| Current URL | New URL | Description |
|------------|---------|-------------|
| `/parser` | `/t/:id/parser` | Link parser |
| `/resources` | `/t/:id/resources` | Resource list |
| `/resources/folder/:folderId` | `/t/:id/resources/folder/:folderId` | Folder view |
| `/resources/smart/:smartFolderId` | `/t/:id/resources/smart/:smartFolderId` | Smart folder |
| `/projects` | `/t/:id/projects` | Project list |
| `/projects/:pid` | `/t/:id/projects/:pid` | Project detail |
| `/projects/:pid/review/:fid` | `/t/:id/projects/:pid/review/:fid` | File review |
| `/members` | `/t/:id/members` | Members management |
| `/billing` | `/t/:id/billing` | Billing |
| `/points` | `/t/:id/points` | Points system |
| `/todolist` | `/t/:id/todolist` | Todolist |
| `/settings` | `/settings` | Account settings (no team scope) |
| `/login` | `/login` | Login (no auth required) |
| `/share/:code` | `/share/:code` | Share page (own access control) |

### Redirect Rules

- `/` → `/t/:lastTeamId/parser` (or personal team default)
- `/parser` → `/t/:currentTeamId/parser` (backward compatibility)
- Post-login → `/t/:personalTeamId/parser`
- Workspace switch → `navigate(/t/:newTeamId/:currentView)`

## Security — Three-Layer Protection

### Layer 1: Supabase RLS (Database)

```sql
CREATE POLICY "Team members can view team resources"
ON resources FOR SELECT
USING (
  scope_id::BIGINT IN (
    SELECT team_id FROM team_members
    WHERE user_id = auth.uid()
  )
);
```

Even if a user constructs `/t/274717589983238/resources`, Supabase returns empty set if they're not a member.

### Layer 2: Backend API

```python
async def verify_team_membership(user_id: str, team_id: str) -> bool:
    result = await client.table("team_members")
        .select("user_id")
        .eq("team_id", team_id)
        .eq("user_id", user_id)
        .execute()
    return bool(result.data)
```

### Layer 3: Frontend Route Guard

```tsx
function TeamGuard({ children }) {
  const { teamId } = useParams();
  const { teams, personalTeamId } = useTeamContext();
  const hasAccess = teamId === personalTeamId
    || teams.some(t => t.id === teamId);
  if (!hasAccess) return <AccessDenied />;
  return children;
}
```

### Snowflake ID Security

Snowflake IDs are opaque numeric identifiers. Knowing an ID does not grant access — RLS enforces authorization.

## User Display ID

### Problem

`auth.users.id` is UUID (Supabase internal, cannot change). But user-facing URLs should use short numeric IDs.

### Solution

```sql
ALTER TABLE user_profiles
ADD COLUMN display_id BIGINT UNIQUE DEFAULT generate_snowflake_id();
```

- Internal: UUID (FK to auth.users) — no change
- External: Snowflake display_id — for URLs, API responses
- `handle_new_user()` trigger: DEFAULT auto-fills on insert

## Implementation

### Database Migration (054)

```sql
-- 054_user_display_id.sql
ALTER TABLE user_profiles
ADD COLUMN display_id BIGINT UNIQUE DEFAULT generate_snowflake_id();

UPDATE user_profiles SET display_id = generate_snowflake_id()
WHERE display_id IS NULL;
```

### Frontend Changes

#### router.tsx — Route restructure

```tsx
createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/share/:shareCode", element: <SharePage /> },
  { path: "/settings", element: <SettingsPage /> },

  // Legacy URL redirects
  { path: "/parser", element: <RedirectToTeam view="parser" /> },
  { path: "/resources/*", element: <RedirectToTeam view="resources" /> },
  // ... other legacy routes

  // Team-scoped routes
  { path: "/t/:teamId", element: <AppLayout />, children: [
    { path: "parser", element: <ParserPage /> },
    { path: "resources", element: <ResourcesPage /> },
    { path: "resources/folder/:folderId", element: <ResourcesPage /> },
    { path: "resources/smart/:smartFolderId", element: <ResourcesPage /> },
    { path: "projects", element: <ProjectsPage /> },
    { path: "projects/:projectId", element: <ProjectsPage /> },
    { path: "projects/:projectId/review/:fileId", element: <ProjectsPage /> },
    { path: "members", element: <MembersPage /> },
    { path: "billing", element: <BillingPage /> },
    { path: "points", element: <PointsPage /> },
    { path: "todolist", element: <TodolistPage /> },
  ]},

  { path: "/", element: <RedirectToDefaultTeam /> },
])
```

#### TeamContext — Source of truth changes to URL

```tsx
// AppLayout reads teamId from URL params
function AppLayout() {
  const { teamId } = useParams();
  const { setActiveTeamId } = useTeamContext();
  useEffect(() => { setActiveTeamId(teamId); }, [teamId]);
  return <><Sidebar /><Outlet /></>;
}
```

#### WorkspaceSwitcher — Navigate with teamId

```tsx
const handleSelect = (teamId: string) => {
  const currentView = getCurrentView();
  navigate(`/t/${teamId}/${currentView}`);
};
```

### Files Affected

| File | Scope | Description |
|------|-------|-------------|
| `054_user_display_id.sql` | New | Add display_id to user_profiles |
| `router.tsx` | Major | Restructure all routes |
| `AppLayout.tsx` | Medium | Read teamId from URL params |
| `TeamContext.tsx` | Medium | teamId source → URL |
| `useTeams.ts` | Medium | Remove localStorage sync for teamId |
| `WorkspaceSwitcher.tsx` | Small | Navigate with teamId in path |
| `Sidebar.tsx` | Small | Navigation links with /t/:teamId prefix |
| All Page components | Small | Internal links add /t/:teamId |

## Commit Strategy

1. `feat: add display_id to user_profiles` — migration 054
2. `feat: restructure routes with /t/:teamId prefix` — router + layout + context
3. `feat: update navigation for team-scoped URLs` — sidebar + workspace switcher + pages
4. `feat: add legacy URL redirects and route guards` — backward compat + 403 page
