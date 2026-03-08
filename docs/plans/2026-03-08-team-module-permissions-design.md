# Team Module Permissions Design

## Overview

Admin can control which modules each Team has access to. Uses a module-level on/off switch stored as a JSONB array on the `teams` table. 7 controllable modules; base modules (Settings, Auth, Billing, Points, Members) are always available.

## Controllable Modules

| Key | Display Name | Default | Description |
|-----|-------------|---------|-------------|
| `parser` | Parser | ON | URL parsing, video downloading |
| `resources` | Resources | ON | Resource library CRUD, folders, tags |
| `library` | Library | ON | Media library browsing |
| `projects` | Projects | ON | Project management, file review |
| `ai_analysis` | AI Analysis | ON | Transcription, summary, visual analysis |
| `dashboard` | Dashboard | ON | Statistics and analytics |
| `cleanup` | Cleanup | ON | Storage cleanup suggestions |

### Always-Available Modules

Settings, Auth, Billing, Points, Members, Notifications, Player, Task Center, Search, Shared, TodoList

## Database

### Migration: `100_team_module_permissions.sql`

Add `enabled_modules` JSONB column to `teams` table:

```sql
ALTER TABLE teams
ADD COLUMN enabled_modules JSONB
DEFAULT '["parser","resources","library","projects","ai_analysis","dashboard","cleanup"]'::jsonb;

-- Backfill existing teams: all modules enabled
UPDATE teams SET enabled_modules = '["parser","resources","library","projects","ai_analysis","dashboard","cleanup"]'::jsonb
WHERE enabled_modules IS NULL;
```

Design choice: JSONB array on `teams` table (not a separate table) because:
- Only 7 modules, no complex per-module metadata needed
- Single read per team load, no JOIN
- Easy to extend by appending new module keys

## Backend

### 1. Module Constants (`backend/app/core/modules.py`)

```python
ALL_MODULES = ["parser", "resources", "library", "projects", "ai_analysis", "dashboard", "cleanup"]

MODULE_ROUTE_MAP = {
    "parser": ["/api/v1/videos"],
    "resources": ["/api/v1/resources"],
    "library": ["/api/v1/libraries"],
    "projects": ["/api/v1/projects"],
    "ai_analysis": ["/api/v1/ai", "/api/v1/analysis"],
    "dashboard": ["/api/v1/dashboard", "/api/v1/system/stats"],
    "cleanup": ["/api/v1/cleanup"],
}
```

### 2. Middleware (`backend/app/core/module_guard.py`)

FastAPI dependency that checks if the current team has the requested module enabled:

```python
async def check_module_access(request: Request):
    """Middleware: reject API calls for disabled modules with 403."""
    path = request.url.path
    team_id = extract_team_id(request)  # from header/token
    if not team_id:
        return  # no team context = skip check

    team = await get_team(team_id)
    enabled = team.get("enabled_modules", ALL_MODULES)

    for module_key, prefixes in MODULE_ROUTE_MAP.items():
        if any(path.startswith(prefix) for prefix in prefixes):
            if module_key not in enabled:
                raise HTTPException(403, f"Module '{module_key}' is not enabled for this team")
            break
```

Register as a global dependency on the main app router (not on admin routes).

### 3. Admin API Endpoints

**GET /api/v1/admin/teams/{team_id}/modules**
Returns current module settings for a team.

Response:
```json
{
  "team_id": "...",
  "modules": [
    { "key": "parser", "name": "Parser", "enabled": true },
    { "key": "resources", "name": "Resources", "enabled": true },
    ...
  ]
}
```

**PATCH /api/v1/admin/teams/{team_id}/modules**
Update module settings.

Request body:
```json
{
  "enabled_modules": ["parser", "resources", "projects"]
}
```

Validation: all keys must be in `ALL_MODULES`. Creates audit log entry.

### 4. Teams List API Update

Include `enabled_modules` in `AdminTeamResponse` so the admin UI can show current state.

## Frontend (User App)

### 1. TeamContext Enhancement

When team data loads, store `enabledModules: string[]` in context:

```tsx
interface TeamContextValue {
  // existing fields...
  enabledModules: string[]
  isModuleEnabled: (key: string) => boolean
}
```

### 2. Sidebar Filtering

In `Sidebar.tsx`, filter navigation items based on `isModuleEnabled()`:

```tsx
const navItems = allNavItems.filter(item =>
  !item.moduleKey || isModuleEnabled(item.moduleKey)
)
```

Each nav item gets a `moduleKey` property mapping to the module constants.

### 3. Route Guard

In the router config, wrap module-specific routes with a guard component:

```tsx
<ModuleGuard module="parser">
  <ParserPage />
</ModuleGuard>
```

`ModuleGuard` checks `isModuleEnabled()` and redirects to a "Module not available" page if disabled.

### 4. API Response Handling

When backend returns 403 with module-disabled message, show a user-friendly toast: "This feature is not available for your workspace. Contact your administrator."

## Frontend (Admin Panel)

### Admin Teams Page: Module Access Section

In the expanded row of Teams table, add a "Module Access" section below Members:

```
[Team Row] 8512939's Workspace | 1 | 1000 | ...
  ├── Members (1)
  │   └── [member table]
  └── Module Access
      ☑ Parser  ☑ Resources  ☑ Library  ☑ Projects
      ☑ AI Analysis  ☑ Dashboard  ☑ Cleanup
      [Save]
```

Implementation: `Checkbox.Group` from Arco Design with module options. On change, PATCH to admin API.

## Data Flow

```
Admin toggles module OFF
  → PATCH /admin/teams/{id}/modules
  → teams.enabled_modules updated in DB
  → Audit log created

User loads app
  → GET /teams/{id} returns enabled_modules
  → TeamContext stores enabledModules
  → Sidebar hides disabled module nav items
  → Route guard prevents direct URL access
  → API middleware returns 403 for disabled module endpoints
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| User navigates to disabled module URL | Redirect to default page + toast message |
| API call to disabled module | 403 response + frontend toast |
| Admin disables all modules | Settings/Auth/Points always available, user can still access basic functions |
| New team created | All modules enabled by default |
| Module key added in future | Add to ALL_MODULES constant + migration to backfill existing teams |

## Security

- Module check runs server-side (middleware), not just frontend hiding
- Admin-only API for changing module settings
- Audit log tracks all module permission changes
- Frontend hiding is UX convenience; backend is the enforcement layer
