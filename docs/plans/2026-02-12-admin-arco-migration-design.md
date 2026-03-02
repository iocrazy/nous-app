# Admin Panel Migration: Refine + TailwindCSS -> Arco Design

**Date**: 2026-02-12
**Status**: Design
**Branch**: `feature/admin-web-design`

## Overview

Migrate the admin panel from Refine + TailwindCSS to Arco Design component library with TanStack Query for data fetching. This is a full replacement - Refine will be removed entirely.

Additionally, this document includes design specifications for three new features (design only, implementation deferred):
1. Resource-level permission system
2. Credits management system
3. Enhanced team management

## Current State

### Existing Pages (to migrate)
- **Login** - Supabase auth with admin role check
- **Dashboard** - Stats cards + today's activity + quick actions
- **UserList** - Table with search, role filter, ban/delete actions
- **TeamList** - Card-based list with expandable member view
- **AuditLogList** - Table with action/target filters + details modal

### Placeholder Pages (keep as stubs)
- Videos, Tags, Credits, API Keys, Settings

### Current Dependencies
```
@refinedev/core, @refinedev/react-router-v6, @refinedev/simple-rest, @refinedev/supabase
@supabase/supabase-js, react, react-dom, react-router-dom, lucide-react
tailwindcss, postcss, autoprefixer
```

---

## Part 1: Framework Migration (Implement)

### 1.1 New Tech Stack

| Layer | Technology |
|-------|-----------|
| UI Components | `@arco-design/web-react` |
| Icons | `@arco-design/web-react/icon` (replaces lucide-react) |
| Data Fetching | `@tanstack/react-query` (replaces Refine hooks) |
| HTTP Client | `axios` (replaces manual fetch) |
| Routing | `react-router-dom` v6 (keep) |
| Auth | `@supabase/supabase-js` (keep) |
| Build | `vite` (keep) |
| Styles | Arco Design CSS (replaces TailwindCSS) |

### 1.2 Dependencies Change

**Remove:**
- `@refinedev/core`
- `@refinedev/react-router-v6`
- `@refinedev/simple-rest`
- `@refinedev/supabase`
- `@refinedev/cli`
- `tailwindcss`
- `postcss`
- `autoprefixer`
- `lucide-react`

**Add:**
- `@arco-design/web-react`
- `@tanstack/react-query`
- `axios`

**Keep:**
- `@supabase/supabase-js`
- `react`, `react-dom`
- `react-router-dom`
- `vite`, `@vitejs/plugin-react`, `typescript`

### 1.3 Project Structure

```
admin/src/
├── main.tsx                      # Entry: QueryClientProvider + ArcoConfigProvider
├── App.tsx                       # Routes definition
├── index.css                     # Arco CSS import + minimal overrides
│
├── api/
│   ├── client.ts                 # Axios instance with Supabase JWT interceptor
│   └── endpoints/
│       ├── users.ts              # useUsers(), useUpdateUser(), useDeleteUser()
│       ├── teams.ts              # useTeams(), useTeamMembers(), useDeleteTeam()
│       ├── audit-logs.ts         # useAuditLogs()
│       └── stats.ts              # useStats()
│
├── auth/
│   ├── AuthProvider.tsx          # React context: user state + login/logout
│   ├── ProtectedRoute.tsx        # Route guard: isAdmin check
│   └── supabase.ts              # Supabase client config
│
├── layouts/
│   └── AdminLayout.tsx           # Arco Layout + Sider + Header + Content
│
├── pages/
│   ├── login.tsx                 # Arco Form login
│   ├── dashboard.tsx             # Arco Statistic + Card + Grid
│   ├── users/
│   │   └── index.tsx             # Arco Table with filters, pagination, actions
│   ├── teams/
│   │   └── index.tsx             # Arco Table with expandable rows
│   ├── audit-logs/
│   │   └── index.tsx             # Arco Table + Modal
│   └── placeholder.tsx           # Generic Arco Empty stub for unimplemented pages
│
└── types/
    └── index.ts                  # Shared TypeScript types
```

### 1.4 API Layer Design

#### HTTP Client (`api/client.ts`)

```typescript
import axios from 'axios'
import { supabase } from '../auth/supabase'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080'

export const apiClient = axios.create({
  baseURL: API_URL,
  headers: { 'Content-Type': 'application/json' },
})

// Auto-attach Supabase JWT to every request
apiClient.interceptors.request.use(async (config) => {
  const { data: { session } } = await supabase.auth.getSession()
  if (session?.access_token) {
    config.headers.Authorization = `Bearer ${session.access_token}`
  }
  return config
})
```

#### Endpoint Hooks (example: `api/endpoints/users.ts`)

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

interface UsersParams {
  page: number
  pageSize: number
  search?: string
  role?: string
}

export function useUsers(params: UsersParams) {
  return useQuery({
    queryKey: ['users', params],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/admin/users', {
        params: {
          page: params.page,
          page_size: params.pageSize,
          ...(params.search && { search: params.search }),
          ...(params.role && { role: params.role }),
        },
      })
      return { items: data.items || data.data || data, total: data.total || 0 }
    },
  })
}

export function useUpdateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Record<string, unknown> }) => {
      const { data: result } = await apiClient.patch(`/api/v1/admin/users/${id}`, data)
      return result
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}

export function useDeleteUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/v1/admin/users/${id}`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}
```

**API endpoints remain unchanged** - no backend modifications needed.

### 1.5 Auth Layer Design

#### AuthProvider (`auth/AuthProvider.tsx`)

```typescript
// React Context providing:
interface AuthContext {
  user: User | null           // Current user info
  isAdmin: boolean            // Whether user has admin role
  isLoading: boolean          // Auth state loading
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

// Implementation:
// - useEffect listens to supabase.auth.onAuthStateChange()
// - On auth change, fetches user_profiles.role
// - Rejects non-admin users at login time (same as current)
```

#### ProtectedRoute (`auth/ProtectedRoute.tsx`)

```typescript
// Wraps authenticated routes
// - Shows Arco Spin while checking auth
// - Redirects to /login if not authenticated or not admin
// - Renders <Outlet /> if authenticated admin
```

### 1.6 Layout Design

#### AdminLayout (`layouts/AdminLayout.tsx`)

Uses Arco `Layout` component system:

```
┌──────────────────────────────────────────────────┐
│ Layout                                            │
│ ┌───────────┐ ┌────────────────────────────────┐ │
│ │   Sider    │ │ Header                         │ │
│ │ (collapsible)│ │  Admin Panel    [User] [Logout]│ │
│ │            │ ├────────────────────────────────┤ │
│ │ Logo       │ │ Content                        │ │
│ │            │ │                                │ │
│ │ Menu:      │ │   <Outlet />                   │ │
│ │ - Dashboard│ │                                │ │
│ │ - Users    │ │                                │ │
│ │ - Teams    │ │                                │ │
│ │ - Videos   │ │                                │ │
│ │ - Tags     │ │                                │ │
│ │ - Credits  │ │                                │ │
│ │ - Logs     │ │                                │ │
│ │ - API Keys │ │                                │ │
│ │ - Settings │ │                                │ │
│ └───────────┘ └────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

Key Arco components:
- `Layout`, `Layout.Sider`, `Layout.Header`, `Layout.Content`
- `Menu` + `Menu.Item` for navigation (auto highlight based on route)
- `Avatar` + `Dropdown` for user menu in header
- Sider is **collapsible** (built-in feature, upgrade over current fixed sidebar)

### 1.7 Page Migration Mapping

| Current | Arco Replacement | Key Benefit |
|---------|-----------------|-------------|
| Login form (hand-written) | `Form` + `Form.Item` + `Input` + `Button` | Built-in validation, error display |
| Dashboard StatCard (hand-written) | `Card` + `Statistic` + `Grid.Row/Col` | Built-in number animation |
| Dashboard QuickActions | `Card` + `Link` + `Grid` | Consistent with theme |
| UserList search bar | `Input.Search` + `Select` | Cleaner API |
| UserList table (hand-written) | `Table` component | **Built-in pagination, sorting, loading, empty state** |
| UserList action menu (hand-written popover) | `Dropdown` + `Menu` | Proper accessibility |
| UserList role badge | `Tag` component with color | Consistent styling |
| UserList status badge | `Badge` / `Tag` | Built-in status styles |
| TeamList card layout | `Table` + `expandedRowRender` | Expandable rows built-in |
| TeamList invite code copy | `Typography.Paragraph` copyable | One-line copy feature |
| AuditLogList table | `Table` + `Tag` + `Modal` | Same built-in benefits |
| AuditLogList filters | `Select` component | Consistent styling |
| All pages: hand-written pagination (~100 lines each) | `Table` pagination prop | **Delete ~400 lines of pagination code** |
| All pages: loading spinner | `Table` loading prop / `Spin` | Automatic |
| All pages: empty state | `Table` noDataElement / `Empty` | Automatic |
| Confirm dialogs (window.confirm) | `Modal.confirm()` | Styled, async-friendly |

### 1.8 Entry Point (`main.tsx`)

```typescript
import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from '@arco-design/web-react'
import enUS from '@arco-design/web-react/es/locale/en-US'
import App from './App'
import '@arco-design/web-react/dist/css/arco.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,      // 30s cache before refetch
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={enUS}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>
)
```

### 1.9 Routing (`App.tsx`)

```typescript
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AdminLayout } from './layouts/AdminLayout'
import { Login } from './pages/login'
import { Dashboard } from './pages/dashboard'
import { UserList } from './pages/users'
import { TeamList } from './pages/teams'
import { AuditLogList } from './pages/audit-logs'
import { PlaceholderPage } from './pages/placeholder'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<AdminLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="/users" element={<UserList />} />
              <Route path="/teams" element={<TeamList />} />
              <Route path="/videos" element={<PlaceholderPage title="Videos" />} />
              <Route path="/tags" element={<PlaceholderPage title="Tags" />} />
              <Route path="/credits" element={<PlaceholderPage title="Credits" />} />
              <Route path="/audit-logs" element={<AuditLogList />} />
              <Route path="/api-keys" element={<PlaceholderPage title="API Keys" />} />
              <Route path="/settings" element={<PlaceholderPage title="Settings" />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
```

---

## Part 2: New Feature Designs (Design Only)

### 2.1 Resource-Level Permission System

#### Data Model

```sql
-- New tables for permission system

CREATE TABLE roles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(50) UNIQUE NOT NULL,        -- 'admin', 'editor', 'viewer', etc.
  description TEXT,
  is_system BOOLEAN DEFAULT false,          -- System roles cannot be deleted
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE permissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource VARCHAR(50) NOT NULL,            -- 'users', 'teams', 'videos', 'credits', etc.
  action VARCHAR(50) NOT NULL,              -- 'read', 'write', 'delete', 'manage'
  description TEXT,
  UNIQUE(resource, action)
);

CREATE TABLE role_permissions (
  role_id UUID REFERENCES roles(id) ON DELETE CASCADE,
  permission_id UUID REFERENCES permissions(id) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_id)
);

-- Modify user_profiles to reference roles table
ALTER TABLE user_profiles
  ADD COLUMN role_id UUID REFERENCES roles(id);
```

#### Permission Resources & Actions

| Resource | Actions |
|----------|---------|
| `users` | `read`, `write`, `delete` |
| `teams` | `read`, `write`, `delete`, `manage_members` |
| `videos` | `read`, `write`, `delete` |
| `tags` | `read`, `write`, `delete` |
| `credits` | `read`, `adjust`, `configure_rules` |
| `audit_logs` | `read` |
| `api_keys` | `read`, `write`, `delete` |
| `settings` | `read`, `write` |
| `roles` | `read`, `write`, `delete` |

#### Default Roles

| Role | Permissions |
|------|------------|
| **Super Admin** | All permissions (system role, cannot be deleted) |
| **Admin** | All except roles.write, roles.delete, settings.write |
| **Editor** | Read all + write users/teams/videos/tags |
| **Viewer** | Read all only |

#### Admin Pages

**Role Management Page** (`/roles`):
- Table listing all roles
- Create/edit role modal:
  - Role name + description
  - Permission matrix: Arco `Checkbox.Group` per resource
  - Or `Transfer` component for permission assignment
- Cannot edit/delete system roles (Super Admin)

**User Page Enhancement**:
- Role column changes from `<select>` to role picker (Arco `Select` with role options)
- Role picker fetches from roles API instead of hardcoded array

**Frontend Route Guards**:
- `AdminLayout` menu items show/hide based on user permissions
- `ProtectedRoute` enhanced with permission check
- API calls return 403 if insufficient permissions

#### API Endpoints (new)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/admin/roles` | GET | List all roles |
| `/api/v1/admin/roles` | POST | Create role |
| `/api/v1/admin/roles/:id` | PATCH | Update role |
| `/api/v1/admin/roles/:id` | DELETE | Delete role |
| `/api/v1/admin/roles/:id/permissions` | GET | Get role permissions |
| `/api/v1/admin/roles/:id/permissions` | PUT | Set role permissions |
| `/api/v1/admin/permissions` | GET | List all available permissions |

---

### 2.2 Credits Management System

#### Data Model

```sql
-- Credits balance per user
CREATE TABLE user_credits (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id),
  balance INTEGER NOT NULL DEFAULT 0,
  total_earned INTEGER NOT NULL DEFAULT 0,
  total_spent INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Credits transaction log
CREATE TABLE credit_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id),
  amount INTEGER NOT NULL,                  -- Positive = earn, negative = spend
  balance_after INTEGER NOT NULL,
  type VARCHAR(50) NOT NULL,                -- 'earn', 'spend', 'admin_adjust'
  reason VARCHAR(100) NOT NULL,             -- 'video_parse', 'daily_login', 'admin_topup', etc.
  description TEXT,
  admin_id UUID REFERENCES auth.users(id),  -- NULL for system transactions
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Credits rules configuration
CREATE TABLE credit_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  type VARCHAR(20) NOT NULL,                -- 'earn' or 'spend'
  trigger VARCHAR(100) NOT NULL,            -- 'registration', 'daily_login', 'video_parse', etc.
  amount INTEGER NOT NULL,                  -- Credits per trigger
  is_enabled BOOLEAN DEFAULT true,
  daily_limit INTEGER,                      -- Max triggers per day (NULL = unlimited)
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

#### Admin Pages

**Credits Overview Tab** (`/credits`):
- Arco `Statistic` cards in `Grid`:
  - Total Credits Issued
  - Total Credits Spent
  - Credits in Circulation
  - Active Users (with balance > 0)

**User Credits Tab** (`/credits/users`):
- Arco `Table`:
  - Columns: User, Email, Balance, Total Earned, Total Spent, Last Updated
  - Search by email/username
  - Sort by balance
- "Adjust" button per row -> Arco `Modal`:
  - `Radio.Group`: Top Up / Deduct
  - `InputNumber`: Amount
  - `Input.TextArea`: Reason/notes
  - Submit calls `POST /api/v1/admin/credits/:userId/adjust`

**Transaction History Tab** (`/credits/transactions`):
- Arco `Table`:
  - Columns: Time, User, Type (Tag), Amount (+/-), Balance After, Reason, Admin
  - Filters: `DatePicker.RangePicker` + `Select` (type) + `Input.Search` (user)
  - Sort by time (default: newest first)

**Rules Configuration Tab** (`/credits/rules`):
- Arco `Table` with inline editing:
  - Columns: Rule Name, Type (earn/spend), Trigger, Amount, Daily Limit, Enabled, Actions
  - `Switch` for enable/disable (inline)
  - Edit button -> `Modal` with `Form`:
    - `Input`: Rule name
    - `Select`: Type (earn/spend)
    - `Select`: Trigger event
    - `InputNumber`: Amount
    - `InputNumber`: Daily limit (optional)
    - `Input.TextArea`: Description

#### API Endpoints (new)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/admin/credits/overview` | GET | Platform credits statistics |
| `/api/v1/admin/credits/users` | GET | User credits list (paginated) |
| `/api/v1/admin/credits/:userId/adjust` | POST | Manually adjust user credits |
| `/api/v1/admin/credits/transactions` | GET | Transaction history (paginated, filterable) |
| `/api/v1/admin/credits/rules` | GET | List all rules |
| `/api/v1/admin/credits/rules` | POST | Create rule |
| `/api/v1/admin/credits/rules/:id` | PATCH | Update rule |
| `/api/v1/admin/credits/rules/:id` | DELETE | Delete rule |

---

### 2.3 Enhanced Team Management

#### Data Model Changes

```sql
-- Add quotas to teams table
ALTER TABLE teams ADD COLUMN max_members INTEGER DEFAULT 10;
ALTER TABLE teams ADD COLUMN max_videos INTEGER DEFAULT 100;
ALTER TABLE teams ADD COLUMN max_storage_mb INTEGER DEFAULT 1024;
ALTER TABLE teams ADD COLUMN description TEXT;

-- Team activity log
CREATE TABLE team_activity_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id),
  action VARCHAR(50) NOT NULL,            -- 'member_added', 'member_removed', 'role_changed', etc.
  target_user_id UUID REFERENCES auth.users(id),
  details JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

#### Admin Pages

**Team List Page (enhanced)**:
- Arco `Table` with additional columns:
  - Members: `{count}/{max_members}` with Arco `Progress` bar
  - Videos: `{count}/{max_videos}` with progress
  - Storage: `{used}/{max_storage_mb}` with progress
- Clicking team name navigates to detail page

**Team Detail Page** (`/teams/:id`) with Arco `Tabs`:

**Tab 1: Basic Info**
- Arco `Descriptions` component showing team info
- "Edit" button -> `Modal` with `Form`:
  - `Input`: Team name
  - `Input.TextArea`: Description

**Tab 2: Members**
- Arco `Table`:
  - Columns: User, Email, Role (Tag), Joined Date, Actions
  - "Add Member" button -> `Modal`:
    - `Select` with remote search (search users by email)
    - `Select`: Role (owner/admin/member)
  - Per-row actions:
    - Change role: `Select` inline or modal
    - Remove: `Popconfirm` confirmation

**Tab 3: Quotas**
- Arco `Form`:
  - `InputNumber`: Max members
  - `InputNumber`: Max videos
  - `InputNumber`: Max storage (MB)
  - Visual `Progress` bars showing current usage vs limits
- Save button updates team quotas

**Tab 4: Activity Log**
- Arco `Table`:
  - Columns: Time, User, Action (Tag), Target User, Details
  - Read-only, sorted by time desc

#### API Endpoints (new/modified)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/admin/teams/:id` | GET | Team detail with quotas |
| `/api/v1/admin/teams/:id` | PATCH | Update team info + quotas |
| `/api/v1/admin/teams/:id/members` | GET | List members (existing, enhanced) |
| `/api/v1/admin/teams/:id/members` | POST | Add member to team |
| `/api/v1/admin/teams/:id/members/:userId` | PATCH | Change member role |
| `/api/v1/admin/teams/:id/members/:userId` | DELETE | Remove member |
| `/api/v1/admin/teams/:id/activity` | GET | Team activity log |

---

## Implementation Plan (Migration Only)

### Phase 1: Foundation Setup
1. Update `package.json` - remove old deps, add new deps
2. Remove TailwindCSS config files (`tailwind.config.js`, `postcss.config.js`)
3. Create `api/client.ts` with axios + interceptor
4. Create `auth/supabase.ts` (rename from `lib/supabase.ts`)
5. Create `auth/AuthProvider.tsx`
6. Create `auth/ProtectedRoute.tsx`
7. Update `main.tsx` with new providers
8. Update `index.css` to import Arco CSS

### Phase 2: Layout
9. Create `layouts/AdminLayout.tsx` with Arco Layout + Menu
10. Update `App.tsx` with new routing structure

### Phase 3: API Hooks
11. Create `api/endpoints/stats.ts`
12. Create `api/endpoints/users.ts`
13. Create `api/endpoints/teams.ts`
14. Create `api/endpoints/audit-logs.ts`

### Phase 4: Page Migration
15. Migrate Login page
16. Migrate Dashboard page
17. Migrate UserList page
18. Migrate TeamList page
19. Migrate AuditLogList page
20. Create generic PlaceholderPage with Arco Empty

### Phase 5: Cleanup
21. Remove old components (`Sidebar.tsx`, `Header.tsx`, `Layout.tsx`)
22. Remove old providers (`providers/authProvider.ts`, `providers/dataProvider.ts`)
23. Remove unused types
24. Verify build passes (`npm run build`)
25. Manual testing of all pages

### Estimated File Count
- **Delete**: ~12 files (old components, providers, TailwindCSS config)
- **Create**: ~15 files (new structure)
- **Modify**: 3 files (package.json, vite.config.ts, index.html)
