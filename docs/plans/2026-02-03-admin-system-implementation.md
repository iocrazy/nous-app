# MediaHub Admin System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an independent admin panel for MediaHub to manage users, teams, content, credits, and system settings.

**Architecture:** Separate React frontend with Refine.dev framework connecting to existing FastAPI backend via new `/api/v1/admin/*` endpoints. Shared Supabase Auth with admin role verification. Deployed as independent Docker container on port 3097.

**Tech Stack:** React 19 + Refine.dev + TailwindCSS (frontend), FastAPI + Supabase (backend), PostgreSQL with RLS policies (database)

---

## Phase 1: Foundation

### Task 1: Create Admin Frontend Project

**Files:**
- Create: `admin/package.json`
- Create: `admin/vite.config.ts`
- Create: `admin/tsconfig.json`
- Create: `admin/index.html`
- Create: `admin/src/main.tsx`
- Create: `admin/src/App.tsx`
- Create: `admin/.env.example`

**Step 1: Initialize project directory**

```bash
mkdir -p admin/src
cd admin
```

**Step 2: Create package.json**

```json
{
  "name": "mediahub-admin",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint src --ext ts,tsx"
  },
  "dependencies": {
    "@refinedev/cli": "^2.16.0",
    "@refinedev/core": "^4.47.0",
    "@refinedev/react-router-v6": "^4.5.0",
    "@refinedev/simple-rest": "^5.0.0",
    "@refinedev/supabase": "^5.7.0",
    "@supabase/supabase-js": "^2.39.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^6.22.0",
    "lucide-react": "^0.344.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.2.0",
    "autoprefixer": "^10.4.18",
    "postcss": "^8.4.35",
    "tailwindcss": "^3.4.1",
    "typescript": "^5.3.0",
    "vite": "^5.1.0"
  }
}
```

**Step 3: Create vite.config.ts**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3097,
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
```

**Step 4: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

**Step 5: Create index.html**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>MediaHub Admin</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

**Step 6: Create src/main.tsx**

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

**Step 7: Create src/App.tsx (minimal placeholder)**

```tsx
import { Refine } from '@refinedev/core'
import routerBindings from '@refinedev/react-router-v6'
import { BrowserRouter, Routes, Route } from 'react-router-dom'

function App() {
  return (
    <BrowserRouter>
      <Refine
        routerProvider={routerBindings}
        options={{
          syncWithLocation: true,
          warnWhenUnsavedChanges: true,
        }}
      >
        <Routes>
          <Route path="/" element={<div className="p-8">MediaHub Admin - Setup in progress</div>} />
        </Routes>
      </Refine>
    </BrowserRouter>
  )
}

export default App
```

**Step 8: Create .env.example**

```bash
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
VITE_API_URL=http://localhost:8080
```

**Step 9: Install dependencies and verify**

Run: `cd admin && npm install`
Expected: Dependencies installed successfully

**Step 10: Start dev server to verify setup**

Run: `cd admin && npm run dev`
Expected: Server running on http://localhost:3097

**Step 11: Commit**

```bash
git add admin/
git commit -m "feat(admin): initialize admin frontend project with Refine"
```

---

### Task 2: Setup TailwindCSS for Admin

**Files:**
- Create: `admin/tailwind.config.js`
- Create: `admin/postcss.config.js`
- Create: `admin/src/index.css`

**Step 1: Create tailwind.config.js**

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

**Step 2: Create postcss.config.js**

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

**Step 3: Create src/index.css**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  @apply bg-gray-50 text-gray-900;
}
```

**Step 4: Verify TailwindCSS works**

Run: `cd admin && npm run dev`
Expected: Page shows with gray background

**Step 5: Commit**

```bash
git add admin/tailwind.config.js admin/postcss.config.js admin/src/index.css
git commit -m "feat(admin): add TailwindCSS configuration"
```

---

### Task 3: Create Supabase Client for Admin

**Files:**
- Create: `admin/src/lib/supabase.ts`
- Create: `admin/src/types/index.ts`

**Step 1: Create src/lib/supabase.ts**

```typescript
import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error('Missing Supabase environment variables')
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey)
```

**Step 2: Create src/types/index.ts**

```typescript
export interface UserProfile {
  id: string
  username: string | null
  avatar_url: string | null
  role: 'admin' | 'user' | 'test'
  is_banned: boolean
  created_at: string
  updated_at: string
}

export interface Team {
  id: string
  name: string
  owner_id: string
  invite_code: string
  created_at: string
  member_count?: number
}

export interface TeamMember {
  team_id: string
  user_id: string
  role: 'owner' | 'member'
  joined_at: string
  user?: UserProfile
}

export interface Video {
  id: string
  aweme_id: string
  video_title: string
  author: string
  download_status: 'pending' | 'downloading' | 'completed' | 'failed' | 'skipped'
  user_id: string
  team_id: string | null
  created_at: string
}

export interface Tag {
  id: string
  name: string
  color: string | null
  icon: string | null
  type: 'system' | 'user' | 'time'
  user_id: string | null
  created_at: string
}

export interface AuditLog {
  id: string
  admin_id: string
  action: string
  target_type: string
  target_id: string
  details: Record<string, unknown>
  ip_address: string
  created_at: string
}

export interface ApiKey {
  key_id: string
  name: string
  user_id: string
  scopes: string[]
  status: 'active' | 'revoked' | 'expired'
  last_used_at: string | null
  expires_at: string | null
  created_at: string
}
```

**Step 3: Commit**

```bash
git add admin/src/lib/ admin/src/types/
git commit -m "feat(admin): add Supabase client and TypeScript types"
```

---

### Task 4: Create Auth Provider for Admin

**Files:**
- Create: `admin/src/providers/authProvider.ts`

**Step 1: Create src/providers/authProvider.ts**

```typescript
import { AuthProvider } from '@refinedev/core'
import { supabase } from '../lib/supabase'

export const authProvider: AuthProvider = {
  login: async ({ email, password }) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
    })

    if (error) {
      return {
        success: false,
        error: {
          name: 'LoginError',
          message: error.message,
        },
      }
    }

    // Check if user is admin
    const { data: profile } = await supabase
      .from('user_profiles')
      .select('role')
      .eq('id', data.user.id)
      .single()

    if (profile?.role !== 'admin') {
      await supabase.auth.signOut()
      return {
        success: false,
        error: {
          name: 'AuthorizationError',
          message: 'Access denied. Admin role required.',
        },
      }
    }

    return {
      success: true,
      redirectTo: '/',
    }
  },

  logout: async () => {
    const { error } = await supabase.auth.signOut()

    if (error) {
      return {
        success: false,
        error: {
          name: 'LogoutError',
          message: error.message,
        },
      }
    }

    return {
      success: true,
      redirectTo: '/login',
    }
  },

  check: async () => {
    const { data: { session } } = await supabase.auth.getSession()

    if (!session) {
      return {
        authenticated: false,
        redirectTo: '/login',
      }
    }

    // Verify admin role
    const { data: profile } = await supabase
      .from('user_profiles')
      .select('role')
      .eq('id', session.user.id)
      .single()

    if (profile?.role !== 'admin') {
      return {
        authenticated: false,
        redirectTo: '/login',
        error: {
          name: 'AuthorizationError',
          message: 'Admin role required',
        },
      }
    }

    return {
      authenticated: true,
    }
  },

  getPermissions: async () => {
    const { data: { session } } = await supabase.auth.getSession()

    if (!session) return null

    const { data: profile } = await supabase
      .from('user_profiles')
      .select('role')
      .eq('id', session.user.id)
      .single()

    return profile?.role
  },

  getIdentity: async () => {
    const { data: { user } } = await supabase.auth.getUser()

    if (!user) return null

    const { data: profile } = await supabase
      .from('user_profiles')
      .select('*')
      .eq('id', user.id)
      .single()

    return {
      id: user.id,
      email: user.email,
      name: profile?.username || user.email,
      avatar: profile?.avatar_url,
      role: profile?.role,
    }
  },

  onError: async (error) => {
    if (error.status === 401 || error.status === 403) {
      return {
        logout: true,
        redirectTo: '/login',
      }
    }

    return { error }
  },
}
```

**Step 2: Commit**

```bash
git add admin/src/providers/
git commit -m "feat(admin): add auth provider with admin role verification"
```

---

### Task 5: Create Data Provider for Admin API

**Files:**
- Create: `admin/src/providers/dataProvider.ts`

**Step 1: Create src/providers/dataProvider.ts**

```typescript
import { DataProvider } from '@refinedev/core'
import { supabase } from '../lib/supabase'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080'

async function getAuthHeaders(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession()

  if (session?.access_token) {
    return {
      'Authorization': `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    }
  }

  return {
    'Content-Type': 'application/json',
  }
}

async function fetchWithAuth(url: string, options: RequestInit = {}) {
  const headers = await getAuthHeaders()
  const response = await fetch(url, {
    ...options,
    headers: {
      ...headers,
      ...options.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Request failed' }))
    throw new Error(error.message || error.detail || 'Request failed')
  }

  return response.json()
}

export const dataProvider: DataProvider = {
  getList: async ({ resource, pagination, filters, sorters }) => {
    const { current = 1, pageSize = 10 } = pagination ?? {}

    const params = new URLSearchParams({
      page: String(current),
      page_size: String(pageSize),
    })

    // Add filters
    filters?.forEach(filter => {
      if ('field' in filter && filter.value !== undefined) {
        params.append(filter.field, String(filter.value))
      }
    })

    // Add sorting
    if (sorters && sorters.length > 0) {
      const sorter = sorters[0]
      params.append('sort_by', sorter.field)
      params.append('sort_order', sorter.order)
    }

    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}?${params}`
    )

    return {
      data: data.items || data.data || data,
      total: data.total || data.length || 0,
    }
  },

  getOne: async ({ resource, id }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}/${id}`
    )

    return { data }
  },

  create: async ({ resource, variables }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}`,
      {
        method: 'POST',
        body: JSON.stringify(variables),
      }
    )

    return { data }
  },

  update: async ({ resource, id, variables }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}/${id}`,
      {
        method: 'PATCH',
        body: JSON.stringify(variables),
      }
    )

    return { data }
  },

  deleteOne: async ({ resource, id }) => {
    const data = await fetchWithAuth(
      `${API_URL}/api/v1/admin/${resource}/${id}`,
      {
        method: 'DELETE',
      }
    )

    return { data }
  },

  getApiUrl: () => API_URL,

  custom: async ({ url, method, payload, headers }) => {
    const data = await fetchWithAuth(
      url.startsWith('http') ? url : `${API_URL}${url}`,
      {
        method: method || 'GET',
        body: payload ? JSON.stringify(payload) : undefined,
        headers,
      }
    )

    return { data }
  },
}
```

**Step 2: Commit**

```bash
git add admin/src/providers/dataProvider.ts
git commit -m "feat(admin): add data provider for admin API calls"
```

---

### Task 6: Create Admin Layout Component

**Files:**
- Create: `admin/src/components/Layout.tsx`
- Create: `admin/src/components/Sidebar.tsx`
- Create: `admin/src/components/Header.tsx`

**Step 1: Create src/components/Sidebar.tsx**

```tsx
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Users,
  Users2,
  Video,
  Tags,
  CreditCard,
  ScrollText,
  Key,
  Settings,
} from 'lucide-react'

const menuItems = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/users', label: 'Users', icon: Users },
  { path: '/teams', label: 'Teams', icon: Users2 },
  { path: '/videos', label: 'Videos', icon: Video },
  { path: '/tags', label: 'Tags', icon: Tags },
  { path: '/credits', label: 'Credits', icon: CreditCard },
  { path: '/audit-logs', label: 'Audit Logs', icon: ScrollText },
  { path: '/api-keys', label: 'API Keys', icon: Key },
  { path: '/settings', label: 'Settings', icon: Settings },
]

export function Sidebar() {
  return (
    <aside className="w-64 bg-gray-900 text-white min-h-screen">
      <div className="p-4 border-b border-gray-800">
        <h1 className="text-xl font-bold">MediaHub Admin</h1>
      </div>
      <nav className="p-4">
        <ul className="space-y-2">
          {menuItems.map((item) => (
            <li key={item.path}>
              <NavLink
                to={item.path}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-4 py-2 rounded-lg transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:bg-gray-800'
                  }`
                }
              >
                <item.icon size={20} />
                <span>{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  )
}
```

**Step 2: Create src/components/Header.tsx**

```tsx
import { useGetIdentity, useLogout } from '@refinedev/core'
import { LogOut, User } from 'lucide-react'

interface Identity {
  id: string
  email: string
  name: string
  avatar?: string
  role: string
}

export function Header() {
  const { data: identity } = useGetIdentity<Identity>()
  const { mutate: logout } = useLogout()

  return (
    <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-6">
      <div className="text-lg font-semibold text-gray-700">
        Admin Panel
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <User size={16} />
          <span>{identity?.name || identity?.email}</span>
          <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs">
            {identity?.role}
          </span>
        </div>
        <button
          onClick={() => logout()}
          className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-600 hover:text-red-600 transition-colors"
        >
          <LogOut size={16} />
          Logout
        </button>
      </div>
    </header>
  )
}
```

**Step 3: Create src/components/Layout.tsx**

```tsx
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'

export function Layout() {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col">
        <Header />
        <main className="flex-1 p-6 bg-gray-50">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
```

**Step 4: Commit**

```bash
git add admin/src/components/
git commit -m "feat(admin): add layout components (Sidebar, Header, Layout)"
```

---

### Task 7: Create Login Page

**Files:**
- Create: `admin/src/pages/Login.tsx`

**Step 1: Create src/pages/Login.tsx**

```tsx
import { useState } from 'react'
import { useLogin } from '@refinedev/core'
import { Lock, Mail, AlertCircle } from 'lucide-react'

export function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const { mutate: login, isLoading, error } = useLogin()

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    login({ email, password })
  }

  return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center">
      <div className="bg-white rounded-lg shadow-xl p-8 w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-gray-900">MediaHub Admin</h1>
          <p className="text-gray-600 mt-2">Sign in to admin panel</p>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2 text-red-700">
            <AlertCircle size={18} />
            <span className="text-sm">{(error as Error).message}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                placeholder="admin@example.com"
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                placeholder="Enter password"
                required
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <p className="text-center text-sm text-gray-500 mt-6">
          Only admin accounts can access this panel
        </p>
      </div>
    </div>
  )
}
```

**Step 2: Commit**

```bash
git add admin/src/pages/
git commit -m "feat(admin): add login page with admin role verification"
```

---

### Task 8: Wire Up App with Providers and Routes

**Files:**
- Modify: `admin/src/App.tsx`
- Create: `admin/src/pages/Dashboard.tsx`

**Step 1: Create src/pages/Dashboard.tsx (placeholder)**

```tsx
import { LayoutDashboard, Users, Video, CreditCard } from 'lucide-react'

export function Dashboard() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Total Users"
          value="--"
          icon={Users}
          color="blue"
        />
        <StatCard
          title="Total Videos"
          value="--"
          icon={Video}
          color="green"
        />
        <StatCard
          title="Active Teams"
          value="--"
          icon={LayoutDashboard}
          color="purple"
        />
        <StatCard
          title="Total Credits"
          value="--"
          icon={CreditCard}
          color="orange"
        />
      </div>

      <div className="mt-8 bg-white rounded-lg shadow p-6">
        <p className="text-gray-500">
          Dashboard statistics will be populated after backend API implementation.
        </p>
      </div>
    </div>
  )
}

interface StatCardProps {
  title: string
  value: string
  icon: React.ComponentType<{ className?: string }>
  color: 'blue' | 'green' | 'purple' | 'orange'
}

function StatCard({ title, value, icon: Icon, color }: StatCardProps) {
  const colorClasses = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-500">{title}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
        </div>
        <div className={`p-3 rounded-lg ${colorClasses[color]}`}>
          <Icon className="w-6 h-6" />
        </div>
      </div>
    </div>
  )
}
```

**Step 2: Update src/App.tsx**

```tsx
import { Refine, Authenticated } from '@refinedev/core'
import routerBindings, {
  NavigateToResource,
  CatchAllNavigate,
} from '@refinedev/react-router-v6'
import { BrowserRouter, Routes, Route, Outlet } from 'react-router-dom'

import { authProvider } from './providers/authProvider'
import { dataProvider } from './providers/dataProvider'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'

function App() {
  return (
    <BrowserRouter>
      <Refine
        authProvider={authProvider}
        dataProvider={dataProvider}
        routerProvider={routerBindings}
        resources={[
          { name: 'dashboard', list: '/' },
          { name: 'users', list: '/users', show: '/users/:id' },
          { name: 'teams', list: '/teams', show: '/teams/:id' },
          { name: 'videos', list: '/videos', show: '/videos/:id' },
          { name: 'tags', list: '/tags' },
          { name: 'credits', list: '/credits' },
          { name: 'audit-logs', list: '/audit-logs' },
          { name: 'api-keys', list: '/api-keys' },
          { name: 'settings', list: '/settings' },
        ]}
        options={{
          syncWithLocation: true,
          warnWhenUnsavedChanges: true,
        }}
      >
        <Routes>
          <Route
            element={
              <Authenticated fallback={<CatchAllNavigate to="/login" />}>
                <Layout />
              </Authenticated>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="/users" element={<PlaceholderPage title="Users" />} />
            <Route path="/teams" element={<PlaceholderPage title="Teams" />} />
            <Route path="/videos" element={<PlaceholderPage title="Videos" />} />
            <Route path="/tags" element={<PlaceholderPage title="Tags" />} />
            <Route path="/credits" element={<PlaceholderPage title="Credits" />} />
            <Route path="/audit-logs" element={<PlaceholderPage title="Audit Logs" />} />
            <Route path="/api-keys" element={<PlaceholderPage title="API Keys" />} />
            <Route path="/settings" element={<PlaceholderPage title="Settings" />} />
          </Route>
          <Route
            element={
              <Authenticated fallback={<Outlet />}>
                <NavigateToResource />
              </Authenticated>
            }
          >
            <Route path="/login" element={<Login />} />
          </Route>
        </Routes>
      </Refine>
    </BrowserRouter>
  )
}

function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-4">{title}</h1>
      <div className="bg-white rounded-lg shadow p-6">
        <p className="text-gray-500">
          This page will be implemented in upcoming tasks.
        </p>
      </div>
    </div>
  )
}

export default App
```

**Step 3: Verify the app runs**

Run: `cd admin && npm run dev`
Expected: App loads, redirects to login page

**Step 4: Commit**

```bash
git add admin/src/
git commit -m "feat(admin): wire up Refine with auth, data providers and routing"
```

---

### Task 9: Create Backend Admin Middleware

**Files:**
- Create: `backend/app/core/admin_deps.py`

**Step 1: Create backend/app/core/admin_deps.py**

```python
"""Admin-specific dependencies for FastAPI routes."""

from typing import Annotated
from fastapi import Depends, HTTPException, status

from app.core.deps import AuthContext, get_auth
from app.db.supabase import get_async_supabase


async def get_admin_auth(
    auth: AuthContext = Depends(get_auth),
) -> AuthContext:
    """
    Verify the authenticated user has admin role.

    Raises HTTPException 403 if user is not an admin.
    """
    supabase = await get_async_supabase()

    # Get user profile to check role
    result = await supabase.table("user_profiles").select("role").eq("id", auth.user_id).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User profile not found",
        )

    if result.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return auth


# Type alias for dependency injection
AdminAuthDep = Annotated[AuthContext, Depends(get_admin_auth)]
```

**Step 2: Run tests to verify no import errors**

Run: `cd backend && uv run python -c "from app.core.admin_deps import AdminAuthDep; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/core/admin_deps.py
git commit -m "feat(backend): add admin authentication dependency"
```

---

### Task 10: Create Database Migrations for Admin Tables

**Files:**
- Create: `supabase/migrations/030_admin_system_tables.sql`

**Step 1: Create supabase/migrations/030_admin_system_tables.sql**

```sql
-- Admin System Tables Migration
-- Date: 2026-02-03

-- ============================================
-- 1. User Credits Table
-- ============================================
CREATE TABLE IF NOT EXISTS user_credits (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    total_earned INTEGER NOT NULL DEFAULT 0,
    total_spent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for quick balance lookups
CREATE INDEX IF NOT EXISTS idx_user_credits_balance ON user_credits(balance);

-- ============================================
-- 2. Credit Transactions Table
-- ============================================
CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,  -- positive=income, negative=expense
    type VARCHAR(50) NOT NULL CHECK (type IN ('recharge', 'consume', 'refund', 'gift', 'adjustment')),
    description TEXT,
    related_id VARCHAR(255),  -- Reference to related entity (video_id, etc.)
    admin_id UUID REFERENCES auth.users(id),  -- Admin who performed action (for manual adjustments)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for transaction queries
CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id ON credit_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_type ON credit_transactions(type);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_created_at ON credit_transactions(created_at);

-- ============================================
-- 3. Credit Pricing Configuration Table
-- ============================================
CREATE TABLE IF NOT EXISTS credit_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action VARCHAR(100) UNIQUE NOT NULL,  -- 'parse', 'download', 'ai_analysis', 'storage_gb'
    cost INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default pricing
INSERT INTO credit_pricing (action, cost, description) VALUES
    ('parse', 1, 'Parse a single video link'),
    ('download', 2, 'Download video after parsing'),
    ('ai_analysis', 5, 'AI content analysis'),
    ('storage_gb', 10, 'Storage per GB per month')
ON CONFLICT (action) DO NOTHING;

-- ============================================
-- 4. System Settings Table
-- ============================================
CREATE TABLE IF NOT EXISTS system_settings (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by UUID REFERENCES auth.users(id)
);

-- Insert default settings
INSERT INTO system_settings (key, value, description) VALUES
    ('site_name', '"MediaHub"', 'Site display name'),
    ('registration_enabled', 'true', 'Allow new user registration'),
    ('download_enabled', 'true', 'Allow video downloads'),
    ('ai_analysis_enabled', 'true', 'Allow AI content analysis'),
    ('new_user_credits', '100', 'Credits given to new users'),
    ('free_storage_gb', '5', 'Free storage quota in GB')
ON CONFLICT (key) DO NOTHING;

-- ============================================
-- 5. Add is_banned field to user_profiles if not exists
-- ============================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_profiles' AND column_name = 'is_banned'
    ) THEN
        ALTER TABLE user_profiles ADD COLUMN is_banned BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;

-- ============================================
-- 6. Audit Logs Table (if not exists)
-- ============================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id UUID NOT NULL REFERENCES auth.users(id),
    action VARCHAR(100) NOT NULL,  -- 'user.ban', 'team.delete', 'credit.add'
    target_type VARCHAR(50) NOT NULL,  -- 'user', 'team', 'video', 'tag'
    target_id VARCHAR(255) NOT NULL,
    details JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for audit log queries
CREATE INDEX IF NOT EXISTS idx_audit_logs_admin_id ON audit_logs(admin_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_target_type ON audit_logs(target_type);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);

-- ============================================
-- 7. RLS Policies for Admin Tables
-- ============================================

-- Enable RLS on all new tables
ALTER TABLE user_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_pricing ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- User Credits: Users can view own, admins can view/update all
CREATE POLICY "Users can view own credits" ON user_credits
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Admins can manage all credits" ON user_credits
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- Credit Transactions: Users can view own, admins can view/create all
CREATE POLICY "Users can view own transactions" ON credit_transactions
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Admins can manage all transactions" ON credit_transactions
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- Credit Pricing: Anyone can view, only admins can modify
CREATE POLICY "Anyone can view pricing" ON credit_pricing
    FOR SELECT USING (true);

CREATE POLICY "Admins can manage pricing" ON credit_pricing
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- System Settings: Anyone can view, only admins can modify
CREATE POLICY "Anyone can view settings" ON system_settings
    FOR SELECT USING (true);

CREATE POLICY "Admins can manage settings" ON system_settings
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- Audit Logs: Only admins can view and create
CREATE POLICY "Admins can view audit logs" ON audit_logs
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

CREATE POLICY "Admins can create audit logs" ON audit_logs
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- ============================================
-- 8. Trigger to update updated_at timestamps
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_user_credits_updated_at
    BEFORE UPDATE ON user_credits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_credit_pricing_updated_at
    BEFORE UPDATE ON credit_pricing
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_system_settings_updated_at
    BEFORE UPDATE ON system_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

**Step 2: Commit**

```bash
git add supabase/migrations/
git commit -m "feat(db): add admin system tables migration"
```

---

## Phase 1 Complete Checkpoint

At this point you should have:
- Admin frontend project with Refine.dev setup
- TailwindCSS styling
- Auth provider with admin role verification
- Data provider for API calls
- Layout, Sidebar, Header components
- Login page
- Dashboard placeholder
- Backend admin authentication middleware
- Database migrations for credits, settings, audit logs

**Verify:**
1. Frontend runs: `cd admin && npm run dev`
2. Login page shows at http://localhost:3097/login
3. Backend import works: `cd backend && uv run python -c "from app.core.admin_deps import AdminAuthDep"`

---

## Phase 2: Core Admin Modules

### Task 11: Create Admin Users API Router

**Files:**
- Create: `backend/app/api/admin/users_router.py`
- Create: `backend/app/api/admin/__init__.py`
- Create: `backend/app/schemas/admin.py`

**Step 1: Create backend/app/schemas/admin.py**

```python
"""Admin-specific Pydantic schemas."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ============================================
# User Management Schemas
# ============================================

class AdminUserResponse(BaseModel):
    """User data for admin view."""
    id: str
    email: str
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str
    is_banned: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_sign_in_at: Optional[datetime] = None
    video_count: int = 0
    team_count: int = 0


class AdminUserListResponse(BaseModel):
    """Paginated user list response."""
    items: List[AdminUserResponse]
    total: int
    page: int
    page_size: int


class AdminUserUpdate(BaseModel):
    """Schema for updating user data."""
    role: Optional[str] = Field(None, pattern="^(admin|user|test)$")
    is_banned: Optional[bool] = None


class AdminUserBanRequest(BaseModel):
    """Request to ban/unban a user."""
    is_banned: bool
    reason: Optional[str] = None


# ============================================
# Team Management Schemas
# ============================================

class AdminTeamResponse(BaseModel):
    """Team data for admin view."""
    id: str
    name: str
    owner_id: str
    owner_email: Optional[str] = None
    invite_code: str
    member_count: int = 0
    video_count: int = 0
    created_at: datetime


class AdminTeamListResponse(BaseModel):
    """Paginated team list response."""
    items: List[AdminTeamResponse]
    total: int
    page: int
    page_size: int


class AdminTeamMemberResponse(BaseModel):
    """Team member for admin view."""
    user_id: str
    email: str
    username: Optional[str] = None
    role: str
    joined_at: datetime


# ============================================
# Audit Log Schemas
# ============================================

class AuditLogCreate(BaseModel):
    """Schema for creating audit log entry."""
    action: str
    target_type: str
    target_id: str
    details: Optional[dict] = None


class AuditLogResponse(BaseModel):
    """Audit log entry response."""
    id: str
    admin_id: str
    admin_email: Optional[str] = None
    action: str
    target_type: str
    target_id: str
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    """Paginated audit log response."""
    items: List[AuditLogResponse]
    total: int
    page: int
    page_size: int


# ============================================
# Statistics Schemas
# ============================================

class AdminStatsResponse(BaseModel):
    """Dashboard statistics."""
    total_users: int
    total_videos: int
    total_teams: int
    total_downloads: int
    active_users_today: int
    new_users_today: int
    new_videos_today: int
```

**Step 2: Create backend/app/api/admin/__init__.py**

```python
"""Admin API routers."""

from fastapi import APIRouter

from .users_router import router as users_router

admin_router = APIRouter(prefix="/admin", tags=["Admin"])

admin_router.include_router(users_router, prefix="/users", tags=["Admin - Users"])
```

**Step 3: Create backend/app/api/admin/users_router.py**

```python
"""Admin user management API routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request, status

from app.core.admin_deps import AdminAuthDep
from app.db.supabase import get_async_supabase
from app.schemas.admin import (
    AdminUserResponse,
    AdminUserListResponse,
    AdminUserUpdate,
    AdminUserBanRequest,
)

router = APIRouter()


async def create_audit_log(
    admin_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: dict = None,
    ip_address: str = None,
):
    """Helper to create audit log entry."""
    supabase = await get_async_supabase()
    await supabase.table("audit_logs").insert({
        "admin_id": admin_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "details": details or {},
        "ip_address": ip_address,
    }).execute()


@router.get("", response_model=AdminUserListResponse)
async def list_users(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    role: Optional[str] = None,
    is_banned: Optional[bool] = None,
    sort_by: str = Query("created_at", pattern="^(created_at|email|role)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List all users with pagination and filtering."""
    supabase = await get_async_supabase()

    # Build query
    query = supabase.table("user_profiles").select(
        "id, username, avatar_url, role, is_banned, created_at, updated_at",
        count="exact"
    )

    # Apply filters
    if search:
        query = query.or_(f"username.ilike.%{search}%,id.eq.{search}")

    if role:
        query = query.eq("role", role)

    if is_banned is not None:
        query = query.eq("is_banned", is_banned)

    # Apply sorting
    query = query.order(sort_by, desc=(sort_order == "desc"))

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()

    # Get auth.users data for emails
    users_data = []
    for profile in result.data:
        # Get email from auth.users via service role
        auth_result = await supabase.auth.admin.get_user_by_id(profile["id"])
        email = auth_result.user.email if auth_result.user else "Unknown"

        # Get video count
        video_result = await supabase.table("douyin_videos").select("id", count="exact").eq("user_id", profile["id"]).execute()

        # Get team count
        team_result = await supabase.table("team_members").select("team_id", count="exact").eq("user_id", profile["id"]).execute()

        users_data.append(AdminUserResponse(
            id=profile["id"],
            email=email,
            username=profile.get("username"),
            avatar_url=profile.get("avatar_url"),
            role=profile.get("role", "user"),
            is_banned=profile.get("is_banned", False),
            created_at=profile["created_at"],
            updated_at=profile.get("updated_at"),
            video_count=video_result.count or 0,
            team_count=team_result.count or 0,
        ))

    return AdminUserListResponse(
        items=users_data,
        total=result.count or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: str,
    auth: AdminAuthDep,
):
    """Get detailed user information."""
    supabase = await get_async_supabase()

    # Get profile
    profile_result = await supabase.table("user_profiles").select("*").eq("id", user_id).single().execute()

    if not profile_result.data:
        raise HTTPException(status_code=404, detail="User not found")

    profile = profile_result.data

    # Get email
    auth_result = await supabase.auth.admin.get_user_by_id(user_id)
    email = auth_result.user.email if auth_result.user else "Unknown"
    last_sign_in = auth_result.user.last_sign_in_at if auth_result.user else None

    # Get counts
    video_result = await supabase.table("douyin_videos").select("id", count="exact").eq("user_id", user_id).execute()
    team_result = await supabase.table("team_members").select("team_id", count="exact").eq("user_id", user_id).execute()

    return AdminUserResponse(
        id=profile["id"],
        email=email,
        username=profile.get("username"),
        avatar_url=profile.get("avatar_url"),
        role=profile.get("role", "user"),
        is_banned=profile.get("is_banned", False),
        created_at=profile["created_at"],
        updated_at=profile.get("updated_at"),
        last_sign_in_at=last_sign_in,
        video_count=video_result.count or 0,
        team_count=team_result.count or 0,
    )


@router.patch("/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: str,
    data: AdminUserUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Update user role or banned status."""
    supabase = await get_async_supabase()

    # Check user exists
    existing = await supabase.table("user_profiles").select("id, role, is_banned").eq("id", user_id).single().execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="User not found")

    # Prepare update data
    update_data = {}
    changes = {}

    if data.role is not None and data.role != existing.data.get("role"):
        update_data["role"] = data.role
        changes["role"] = {"from": existing.data.get("role"), "to": data.role}

    if data.is_banned is not None and data.is_banned != existing.data.get("is_banned"):
        update_data["is_banned"] = data.is_banned
        changes["is_banned"] = {"from": existing.data.get("is_banned"), "to": data.is_banned}

    if not update_data:
        raise HTTPException(status_code=400, detail="No changes provided")

    # Update profile
    await supabase.table("user_profiles").update(update_data).eq("id", user_id).execute()

    # Create audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="user.update",
        target_type="user",
        target_id=user_id,
        details=changes,
        ip_address=request.client.host if request.client else None,
    )

    # Return updated user
    return await get_user(user_id, auth)


@router.post("/{user_id}/ban")
async def ban_user(
    user_id: str,
    data: AdminUserBanRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Ban or unban a user."""
    supabase = await get_async_supabase()

    # Cannot ban yourself
    if user_id == auth.user_id:
        raise HTTPException(status_code=400, detail="Cannot ban yourself")

    # Check user exists
    existing = await supabase.table("user_profiles").select("id, is_banned").eq("id", user_id).single().execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="User not found")

    # Update banned status
    await supabase.table("user_profiles").update({"is_banned": data.is_banned}).eq("id", user_id).execute()

    # Create audit log
    action = "user.ban" if data.is_banned else "user.unban"
    await create_audit_log(
        admin_id=auth.user_id,
        action=action,
        target_type="user",
        target_id=user_id,
        details={"reason": data.reason} if data.reason else None,
        ip_address=request.client.host if request.client else None,
    )

    return {"success": True, "message": f"User {'banned' if data.is_banned else 'unbanned'} successfully"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Soft delete a user (mark as banned and anonymize)."""
    supabase = await get_async_supabase()

    # Cannot delete yourself
    if user_id == auth.user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    # Check user exists
    existing = await supabase.table("user_profiles").select("id").eq("id", user_id).single().execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="User not found")

    # Soft delete: ban and anonymize
    await supabase.table("user_profiles").update({
        "is_banned": True,
        "username": f"deleted_user_{user_id[:8]}",
        "avatar_url": None,
    }).eq("id", user_id).execute()

    # Create audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="user.delete",
        target_type="user",
        target_id=user_id,
        ip_address=request.client.host if request.client else None,
    )

    return {"success": True, "message": "User deleted successfully"}
```

**Step 4: Register admin router in main API**

Modify `backend/app/api/__init__.py` to include admin router.

**Step 5: Verify import works**

Run: `cd backend && uv run python -c "from app.api.admin import admin_router; print('OK')"`
Expected: `OK`

**Step 6: Commit**

```bash
git add backend/app/api/admin/ backend/app/schemas/admin.py
git commit -m "feat(backend): add admin users API router"
```

---

### Task 12: Create Admin Teams API Router

**Files:**
- Create: `backend/app/api/admin/teams_router.py`
- Modify: `backend/app/api/admin/__init__.py`

**Step 1: Create backend/app/api/admin/teams_router.py**

```python
"""Admin team management API routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request

from app.core.admin_deps import AdminAuthDep
from app.db.supabase import get_async_supabase
from app.schemas.admin import (
    AdminTeamResponse,
    AdminTeamListResponse,
    AdminTeamMemberResponse,
)

router = APIRouter()


async def create_audit_log(
    admin_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: dict = None,
    ip_address: str = None,
):
    """Helper to create audit log entry."""
    supabase = await get_async_supabase()
    await supabase.table("audit_logs").insert({
        "admin_id": admin_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "details": details or {},
        "ip_address": ip_address,
    }).execute()


@router.get("", response_model=AdminTeamListResponse)
async def list_teams(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: str = Query("created_at", pattern="^(created_at|name|member_count)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List all teams with pagination."""
    supabase = await get_async_supabase()

    # Build query
    query = supabase.table("teams").select("*", count="exact")

    if search:
        query = query.ilike("name", f"%{search}%")

    # Apply sorting (member_count needs special handling)
    if sort_by != "member_count":
        query = query.order(sort_by, desc=(sort_order == "desc"))
    else:
        query = query.order("created_at", desc=True)

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()

    # Enrich with member counts and owner info
    teams_data = []
    for team in result.data:
        # Get member count
        member_result = await supabase.table("team_members").select("user_id", count="exact").eq("team_id", team["id"]).execute()

        # Get video count
        video_result = await supabase.table("douyin_videos").select("id", count="exact").eq("team_id", team["id"]).execute()

        # Get owner email
        owner_auth = await supabase.auth.admin.get_user_by_id(team["owner_id"])
        owner_email = owner_auth.user.email if owner_auth.user else None

        teams_data.append(AdminTeamResponse(
            id=team["id"],
            name=team["name"],
            owner_id=team["owner_id"],
            owner_email=owner_email,
            invite_code=team["invite_code"],
            member_count=member_result.count or 0,
            video_count=video_result.count or 0,
            created_at=team["created_at"],
        ))

    # Sort by member_count if requested
    if sort_by == "member_count":
        teams_data.sort(key=lambda t: t.member_count, reverse=(sort_order == "desc"))

    return AdminTeamListResponse(
        items=teams_data,
        total=result.count or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/{team_id}", response_model=AdminTeamResponse)
async def get_team(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get team details."""
    supabase = await get_async_supabase()

    team_result = await supabase.table("teams").select("*").eq("id", team_id).single().execute()

    if not team_result.data:
        raise HTTPException(status_code=404, detail="Team not found")

    team = team_result.data

    # Get counts
    member_result = await supabase.table("team_members").select("user_id", count="exact").eq("team_id", team_id).execute()
    video_result = await supabase.table("douyin_videos").select("id", count="exact").eq("team_id", team_id).execute()

    # Get owner email
    owner_auth = await supabase.auth.admin.get_user_by_id(team["owner_id"])
    owner_email = owner_auth.user.email if owner_auth.user else None

    return AdminTeamResponse(
        id=team["id"],
        name=team["name"],
        owner_id=team["owner_id"],
        owner_email=owner_email,
        invite_code=team["invite_code"],
        member_count=member_result.count or 0,
        video_count=video_result.count or 0,
        created_at=team["created_at"],
    )


@router.get("/{team_id}/members")
async def get_team_members(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get all members of a team."""
    supabase = await get_async_supabase()

    # Verify team exists
    team_result = await supabase.table("teams").select("id").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(status_code=404, detail="Team not found")

    # Get members with profile data
    members_result = await supabase.table("team_members").select(
        "user_id, role, joined_at"
    ).eq("team_id", team_id).execute()

    members_data = []
    for member in members_result.data:
        # Get user profile
        profile = await supabase.table("user_profiles").select("username").eq("id", member["user_id"]).single().execute()

        # Get email
        auth_result = await supabase.auth.admin.get_user_by_id(member["user_id"])
        email = auth_result.user.email if auth_result.user else "Unknown"

        members_data.append(AdminTeamMemberResponse(
            user_id=member["user_id"],
            email=email,
            username=profile.data.get("username") if profile.data else None,
            role=member["role"],
            joined_at=member["joined_at"],
        ))

    return {"members": members_data, "total": len(members_data)}


@router.post("/{team_id}/transfer-ownership")
async def transfer_ownership(
    team_id: str,
    new_owner_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Transfer team ownership to another member."""
    supabase = await get_async_supabase()

    # Verify team exists
    team_result = await supabase.table("teams").select("id, owner_id, name").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(status_code=404, detail="Team not found")

    old_owner_id = team_result.data["owner_id"]

    # Verify new owner is a member
    member_check = await supabase.table("team_members").select("user_id").eq("team_id", team_id).eq("user_id", new_owner_id).single().execute()
    if not member_check.data:
        raise HTTPException(status_code=400, detail="New owner must be a team member")

    # Update team owner
    await supabase.table("teams").update({"owner_id": new_owner_id}).eq("id", team_id).execute()

    # Update member roles
    await supabase.table("team_members").update({"role": "member"}).eq("team_id", team_id).eq("user_id", old_owner_id).execute()
    await supabase.table("team_members").update({"role": "owner"}).eq("team_id", team_id).eq("user_id", new_owner_id).execute()

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="team.transfer_ownership",
        target_type="team",
        target_id=team_id,
        details={"old_owner": old_owner_id, "new_owner": new_owner_id},
        ip_address=request.client.host if request.client else None,
    )

    return {"success": True, "message": "Ownership transferred successfully"}


@router.delete("/{team_id}")
async def delete_team(
    team_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Delete a team and all related data."""
    supabase = await get_async_supabase()

    # Verify team exists
    team_result = await supabase.table("teams").select("id, name").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(status_code=404, detail="Team not found")

    team_name = team_result.data["name"]

    # Delete team members
    await supabase.table("team_members").delete().eq("team_id", team_id).execute()

    # Unlink videos from team
    await supabase.table("douyin_videos").update({"team_id": None}).eq("team_id", team_id).execute()

    # Delete team
    await supabase.table("teams").delete().eq("id", team_id).execute()

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="team.delete",
        target_type="team",
        target_id=team_id,
        details={"team_name": team_name},
        ip_address=request.client.host if request.client else None,
    )

    return {"success": True, "message": "Team deleted successfully"}
```

**Step 2: Update backend/app/api/admin/__init__.py**

```python
"""Admin API routers."""

from fastapi import APIRouter

from .users_router import router as users_router
from .teams_router import router as teams_router

admin_router = APIRouter(prefix="/admin", tags=["Admin"])

admin_router.include_router(users_router, prefix="/users", tags=["Admin - Users"])
admin_router.include_router(teams_router, prefix="/teams", tags=["Admin - Teams"])
```

**Step 3: Commit**

```bash
git add backend/app/api/admin/
git commit -m "feat(backend): add admin teams API router"
```

---

### Task 13: Create Admin Audit Logs API Router

**Files:**
- Create: `backend/app/api/admin/audit_logs_router.py`
- Modify: `backend/app/api/admin/__init__.py`

**Step 1: Create backend/app/api/admin/audit_logs_router.py**

```python
"""Admin audit logs API routes."""

from typing import Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

from app.core.admin_deps import AdminAuthDep
from app.db.supabase import get_async_supabase
from app.schemas.admin import AuditLogResponse, AuditLogListResponse

router = APIRouter()


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    admin_id: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
):
    """List audit logs with filtering."""
    supabase = await get_async_supabase()

    query = supabase.table("audit_logs").select("*", count="exact")

    # Apply filters
    if admin_id:
        query = query.eq("admin_id", admin_id)

    if action:
        query = query.eq("action", action)

    if target_type:
        query = query.eq("target_type", target_type)

    if start_date:
        query = query.gte("created_at", start_date.isoformat())

    if end_date:
        query = query.lte("created_at", end_date.isoformat())

    # Order by most recent
    query = query.order("created_at", desc=True)

    # Pagination
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()

    # Enrich with admin emails
    logs_data = []
    for log in result.data:
        admin_auth = await supabase.auth.admin.get_user_by_id(log["admin_id"])
        admin_email = admin_auth.user.email if admin_auth.user else None

        logs_data.append(AuditLogResponse(
            id=log["id"],
            admin_id=log["admin_id"],
            admin_email=admin_email,
            action=log["action"],
            target_type=log["target_type"],
            target_id=log["target_id"],
            details=log.get("details"),
            ip_address=log.get("ip_address"),
            created_at=log["created_at"],
        ))

    return AuditLogListResponse(
        items=logs_data,
        total=result.count or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/actions")
async def get_action_types(auth: AdminAuthDep):
    """Get list of unique action types for filtering."""
    supabase = await get_async_supabase()

    result = await supabase.table("audit_logs").select("action").execute()

    actions = list(set(log["action"] for log in result.data))
    actions.sort()

    return {"actions": actions}


@router.get("/stats")
async def get_audit_stats(
    auth: AdminAuthDep,
    days: int = Query(7, ge=1, le=90),
):
    """Get audit log statistics for the past N days."""
    supabase = await get_async_supabase()

    start_date = datetime.utcnow() - timedelta(days=days)

    result = await supabase.table("audit_logs").select(
        "action, target_type, created_at"
    ).gte("created_at", start_date.isoformat()).execute()

    # Aggregate stats
    action_counts = {}
    target_counts = {}
    daily_counts = {}

    for log in result.data:
        # Count by action
        action = log["action"]
        action_counts[action] = action_counts.get(action, 0) + 1

        # Count by target type
        target = log["target_type"]
        target_counts[target] = target_counts.get(target, 0) + 1

        # Count by day
        day = log["created_at"][:10]
        daily_counts[day] = daily_counts.get(day, 0) + 1

    return {
        "total": len(result.data),
        "by_action": action_counts,
        "by_target": target_counts,
        "by_day": daily_counts,
    }
```

**Step 2: Update backend/app/api/admin/__init__.py to include audit_logs_router**

**Step 3: Commit**

```bash
git add backend/app/api/admin/
git commit -m "feat(backend): add admin audit logs API router"
```

---

### Task 14: Create Admin Stats API Router

**Files:**
- Create: `backend/app/api/admin/stats_router.py`
- Modify: `backend/app/api/admin/__init__.py`

**Step 1: Create backend/app/api/admin/stats_router.py**

```python
"""Admin statistics API routes."""

from datetime import datetime, timedelta
from fastapi import APIRouter, Query

from app.core.admin_deps import AdminAuthDep
from app.db.supabase import get_async_supabase
from app.schemas.admin import AdminStatsResponse

router = APIRouter()


@router.get("/overview", response_model=AdminStatsResponse)
async def get_overview_stats(auth: AdminAuthDep):
    """Get dashboard overview statistics."""
    supabase = await get_async_supabase()

    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())

    # Total users
    users_result = await supabase.table("user_profiles").select("id", count="exact").execute()
    total_users = users_result.count or 0

    # Total videos
    videos_result = await supabase.table("douyin_videos").select("id", count="exact").execute()
    total_videos = videos_result.count or 0

    # Total teams
    teams_result = await supabase.table("teams").select("id", count="exact").execute()
    total_teams = teams_result.count or 0

    # Total downloads (completed)
    downloads_result = await supabase.table("douyin_videos").select("id", count="exact").eq("download_status", "completed").execute()
    total_downloads = downloads_result.count or 0

    # New users today
    new_users_result = await supabase.table("user_profiles").select("id", count="exact").gte("created_at", today_start.isoformat()).execute()
    new_users_today = new_users_result.count or 0

    # New videos today
    new_videos_result = await supabase.table("douyin_videos").select("id", count="exact").gte("created_at", today_start.isoformat()).execute()
    new_videos_today = new_videos_result.count or 0

    # Active users today (simplified: users who logged activity)
    active_result = await supabase.table("user_logs").select("user_id").gte("created_at", today_start.isoformat()).execute()
    active_users_today = len(set(log["user_id"] for log in active_result.data)) if active_result.data else 0

    return AdminStatsResponse(
        total_users=total_users,
        total_videos=total_videos,
        total_teams=total_teams,
        total_downloads=total_downloads,
        active_users_today=active_users_today,
        new_users_today=new_users_today,
        new_videos_today=new_videos_today,
    )


@router.get("/users/growth")
async def get_user_growth(
    auth: AdminAuthDep,
    days: int = Query(30, ge=7, le=365),
):
    """Get user registration growth over time."""
    supabase = await get_async_supabase()

    start_date = datetime.utcnow() - timedelta(days=days)

    result = await supabase.table("user_profiles").select("created_at").gte("created_at", start_date.isoformat()).execute()

    # Group by day
    daily_counts = {}
    for user in result.data:
        day = user["created_at"][:10]
        daily_counts[day] = daily_counts.get(day, 0) + 1

    # Fill missing days with 0
    data = []
    current = start_date.date()
    end = datetime.utcnow().date()
    while current <= end:
        day_str = current.isoformat()
        data.append({"date": day_str, "count": daily_counts.get(day_str, 0)})
        current += timedelta(days=1)

    return {"data": data}


@router.get("/videos/stats")
async def get_video_stats(
    auth: AdminAuthDep,
    days: int = Query(30, ge=7, le=365),
):
    """Get video statistics over time."""
    supabase = await get_async_supabase()

    start_date = datetime.utcnow() - timedelta(days=days)

    result = await supabase.table("douyin_videos").select(
        "created_at, download_status"
    ).gte("created_at", start_date.isoformat()).execute()

    # Group by day and status
    daily_data = {}
    for video in result.data:
        day = video["created_at"][:10]
        status = video["download_status"]

        if day not in daily_data:
            daily_data[day] = {"total": 0, "completed": 0, "failed": 0}

        daily_data[day]["total"] += 1
        if status == "completed":
            daily_data[day]["completed"] += 1
        elif status == "failed":
            daily_data[day]["failed"] += 1

    # Format response
    data = []
    current = start_date.date()
    end = datetime.utcnow().date()
    while current <= end:
        day_str = current.isoformat()
        day_data = daily_data.get(day_str, {"total": 0, "completed": 0, "failed": 0})
        data.append({"date": day_str, **day_data})
        current += timedelta(days=1)

    return {"data": data}


@router.get("/storage")
async def get_storage_stats(auth: AdminAuthDep):
    """Get storage usage statistics."""
    supabase = await get_async_supabase()

    # Get video storage info (this would need actual file size tracking)
    # For now, return placeholder data

    result = await supabase.table("douyin_videos").select(
        "user_id, download_status"
    ).eq("download_status", "completed").execute()

    # Count videos per user
    user_video_counts = {}
    for video in result.data:
        user_id = video["user_id"]
        user_video_counts[user_id] = user_video_counts.get(user_id, 0) + 1

    # Top users by video count
    top_users = sorted(user_video_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_videos_downloaded": len(result.data),
        "unique_users": len(user_video_counts),
        "top_users": [{"user_id": u[0], "video_count": u[1]} for u in top_users],
    }
```

**Step 2: Update backend/app/api/admin/__init__.py to include stats_router**

**Step 3: Commit**

```bash
git add backend/app/api/admin/
git commit -m "feat(backend): add admin stats API router"
```

---

### Task 15: Register Admin Router in Main API

**Files:**
- Modify: `backend/app/api/__init__.py`

**Step 1: Add admin router import and registration**

Find the existing router registrations in `backend/app/api/__init__.py` and add:

```python
from app.api.admin import admin_router

# In the router registration section:
api_router.include_router(admin_router, tags=["Admin"])
```

**Step 2: Verify all imports work**

Run: `cd backend && uv run python -c "from app.api import api_router; print('OK')"`
Expected: `OK`

**Step 3: Run backend to verify endpoints appear**

Run: `cd backend && uv run uvicorn app.main:app --reload`
Visit: http://localhost:8080/docs
Expected: Admin endpoints visible in Swagger UI under Admin tags

**Step 4: Commit**

```bash
git add backend/app/api/__init__.py
git commit -m "feat(backend): register admin router in main API"
```

---

## Phase 2 Complete Checkpoint

At this point you should have:
- Admin schemas (Pydantic models)
- Admin users API (list, get, update, ban, delete)
- Admin teams API (list, get, members, transfer, delete)
- Admin audit logs API (list, actions, stats)
- Admin stats API (overview, user growth, video stats, storage)
- All routers registered in main API

**Verify:**
1. Backend runs: `cd backend && uv run uvicorn app.main:app --reload`
2. Swagger shows admin endpoints at http://localhost:8080/docs
3. Frontend login works (requires admin user in database)

---

## Phase 3: Frontend Admin Pages

### Task 16: Create Users List Page

**Files:**
- Create: `admin/src/pages/users/UserList.tsx`
- Create: `admin/src/pages/users/index.ts`

**Step 1: Create admin/src/pages/users/UserList.tsx**

```tsx
import { useState } from 'react'
import { useList, useUpdate, useDelete } from '@refinedev/core'
import { Search, Ban, Trash2, Shield, ChevronLeft, ChevronRight } from 'lucide-react'

interface User {
  id: string
  email: string
  username: string | null
  role: string
  is_banned: boolean
  created_at: string
  video_count: number
  team_count: number
}

export function UserList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<string>('')

  const { data, isLoading } = useList<User>({
    resource: 'users',
    pagination: { current: page, pageSize: 20 },
    filters: [
      ...(search ? [{ field: 'search', operator: 'contains' as const, value: search }] : []),
      ...(roleFilter ? [{ field: 'role', operator: 'eq' as const, value: roleFilter }] : []),
    ],
  })

  const { mutate: updateUser } = useUpdate()
  const { mutate: deleteUser } = useDelete()

  const users = data?.data || []
  const total = data?.total || 0
  const totalPages = Math.ceil(total / 20)

  const handleBan = (user: User) => {
    if (confirm(`${user.is_banned ? 'Unban' : 'Ban'} user ${user.email}?`)) {
      updateUser({
        resource: 'users',
        id: user.id,
        values: { is_banned: !user.is_banned },
      })
    }
  }

  const handleDelete = (user: User) => {
    if (confirm(`Delete user ${user.email}? This action cannot be undone.`)) {
      deleteUser({
        resource: 'users',
        id: user.id,
      })
    }
  }

  const handleRoleChange = (user: User, newRole: string) => {
    updateUser({
      resource: 'users',
      id: user.id,
      values: { role: newRole },
    })
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Users</h1>
        <div className="text-sm text-gray-500">{total} total users</div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="flex gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
            <input
              type="text"
              placeholder="Search by email or username..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg"
            />
          </div>
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
            className="px-4 py-2 border border-gray-300 rounded-lg"
          >
            <option value="">All Roles</option>
            <option value="admin">Admin</option>
            <option value="user">User</option>
            <option value="test">Test</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Videos</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Teams</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Joined</th>
              <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {isLoading ? (
              <tr>
                <td colSpan={7} className="px-6 py-8 text-center text-gray-500">Loading...</td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-6 py-8 text-center text-gray-500">No users found</td>
              </tr>
            ) : (
              users.map((user) => (
                <tr key={user.id} className={user.is_banned ? 'bg-red-50' : ''}>
                  <td className="px-6 py-4">
                    <div>
                      <div className="font-medium text-gray-900">{user.email}</div>
                      <div className="text-sm text-gray-500">{user.username || 'No username'}</div>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <select
                      value={user.role}
                      onChange={(e) => handleRoleChange(user, e.target.value)}
                      className="text-sm border border-gray-300 rounded px-2 py-1"
                    >
                      <option value="user">User</option>
                      <option value="admin">Admin</option>
                      <option value="test">Test</option>
                    </select>
                  </td>
                  <td className="px-6 py-4">
                    {user.is_banned ? (
                      <span className="px-2 py-1 text-xs bg-red-100 text-red-700 rounded">Banned</span>
                    ) : (
                      <span className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded">Active</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500">{user.video_count}</td>
                  <td className="px-6 py-4 text-sm text-gray-500">{user.team_count}</td>
                  <td className="px-6 py-4 text-sm text-gray-500">
                    {new Date(user.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => handleBan(user)}
                        className={`p-1.5 rounded ${
                          user.is_banned
                            ? 'text-green-600 hover:bg-green-100'
                            : 'text-orange-600 hover:bg-orange-100'
                        }`}
                        title={user.is_banned ? 'Unban' : 'Ban'}
                      >
                        {user.is_banned ? <Shield size={18} /> : <Ban size={18} />}
                      </button>
                      <button
                        onClick={() => handleDelete(user)}
                        className="p-1.5 rounded text-red-600 hover:bg-red-100"
                        title="Delete"
                      >
                        <Trash2 size={18} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-6 py-4 border-t flex items-center justify-between">
            <div className="text-sm text-gray-500">
              Page {page} of {totalPages}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                <ChevronLeft size={18} />
              </button>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
```

**Step 2: Create admin/src/pages/users/index.ts**

```typescript
export { UserList } from './UserList'
```

**Step 3: Commit**

```bash
git add admin/src/pages/users/
git commit -m "feat(admin): add users list page"
```

---

### Task 17: Create Teams List Page

**Files:**
- Create: `admin/src/pages/teams/TeamList.tsx`
- Create: `admin/src/pages/teams/index.ts`

**Step 1: Create admin/src/pages/teams/TeamList.tsx**

```tsx
import { useState } from 'react'
import { useList, useDelete, useCustom } from '@refinedev/core'
import { Search, Trash2, UserPlus, ChevronDown, ChevronRight } from 'lucide-react'

interface Team {
  id: string
  name: string
  owner_id: string
  owner_email: string | null
  invite_code: string
  member_count: number
  video_count: number
  created_at: string
}

interface TeamMember {
  user_id: string
  email: string
  username: string | null
  role: string
  joined_at: string
}

export function TeamList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [expandedTeam, setExpandedTeam] = useState<string | null>(null)

  const { data, isLoading, refetch } = useList<Team>({
    resource: 'teams',
    pagination: { current: page, pageSize: 20 },
    filters: search ? [{ field: 'search', operator: 'contains' as const, value: search }] : [],
  })

  const { mutate: deleteTeam } = useDelete()

  const teams = data?.data || []
  const total = data?.total || 0

  const handleDelete = (team: Team) => {
    if (confirm(`Delete team "${team.name}"? All member associations will be removed.`)) {
      deleteTeam({
        resource: 'teams',
        id: team.id,
      }, {
        onSuccess: () => refetch(),
      })
    }
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Teams</h1>
        <div className="text-sm text-gray-500">{total} total teams</div>
      </div>

      {/* Search */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
          <input
            type="text"
            placeholder="Search teams..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg"
          />
        </div>
      </div>

      {/* Teams List */}
      <div className="space-y-4">
        {isLoading ? (
          <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">Loading...</div>
        ) : teams.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">No teams found</div>
        ) : (
          teams.map((team) => (
            <TeamCard
              key={team.id}
              team={team}
              isExpanded={expandedTeam === team.id}
              onToggle={() => setExpandedTeam(expandedTeam === team.id ? null : team.id)}
              onDelete={() => handleDelete(team)}
            />
          ))
        )}
      </div>
    </div>
  )
}

function TeamCard({
  team,
  isExpanded,
  onToggle,
  onDelete,
}: {
  team: Team
  isExpanded: boolean
  onToggle: () => void
  onDelete: () => void
}) {
  const { data: membersData, isLoading: membersLoading } = useCustom<{ members: TeamMember[] }>({
    url: `/api/v1/admin/teams/${team.id}/members`,
    method: 'get',
    queryOptions: {
      enabled: isExpanded,
    },
  })

  const members = membersData?.data?.members || []

  return (
    <div className="bg-white rounded-lg shadow">
      <div className="p-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button onClick={onToggle} className="p-1 hover:bg-gray-100 rounded">
            {isExpanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
          </button>
          <div>
            <h3 className="font-medium text-gray-900">{team.name}</h3>
            <p className="text-sm text-gray-500">Owner: {team.owner_email || team.owner_id}</p>
          </div>
        </div>
        <div className="flex items-center gap-6">
          <div className="text-sm text-gray-500">
            <span className="font-medium">{team.member_count}</span> members
          </div>
          <div className="text-sm text-gray-500">
            <span className="font-medium">{team.video_count}</span> videos
          </div>
          <div className="text-sm text-gray-500">
            Code: <code className="bg-gray-100 px-2 py-0.5 rounded">{team.invite_code}</code>
          </div>
          <button
            onClick={onDelete}
            className="p-2 text-red-600 hover:bg-red-100 rounded"
            title="Delete Team"
          >
            <Trash2 size={18} />
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="border-t px-4 py-4 bg-gray-50">
          <h4 className="text-sm font-medium text-gray-700 mb-3">Team Members</h4>
          {membersLoading ? (
            <p className="text-sm text-gray-500">Loading members...</p>
          ) : members.length === 0 ? (
            <p className="text-sm text-gray-500">No members</p>
          ) : (
            <div className="grid gap-2">
              {members.map((member) => (
                <div
                  key={member.user_id}
                  className="flex items-center justify-between bg-white p-3 rounded border"
                >
                  <div>
                    <span className="font-medium">{member.email}</span>
                    {member.username && (
                      <span className="text-gray-500 ml-2">({member.username})</span>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={`px-2 py-0.5 text-xs rounded ${
                      member.role === 'owner'
                        ? 'bg-blue-100 text-blue-700'
                        : 'bg-gray-100 text-gray-700'
                    }`}>
                      {member.role}
                    </span>
                    <span className="text-xs text-gray-400">
                      Joined {new Date(member.joined_at).toLocaleDateString()}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
```

**Step 2: Create admin/src/pages/teams/index.ts**

```typescript
export { TeamList } from './TeamList'
```

**Step 3: Commit**

```bash
git add admin/src/pages/teams/
git commit -m "feat(admin): add teams list page"
```

---

### Task 18: Create Audit Logs Page

**Files:**
- Create: `admin/src/pages/audit-logs/AuditLogList.tsx`
- Create: `admin/src/pages/audit-logs/index.ts`

**Step 1: Create admin/src/pages/audit-logs/AuditLogList.tsx**

```tsx
import { useState } from 'react'
import { useList } from '@refinedev/core'
import { Search, Filter, ChevronLeft, ChevronRight } from 'lucide-react'

interface AuditLog {
  id: string
  admin_id: string
  admin_email: string | null
  action: string
  target_type: string
  target_id: string
  details: Record<string, unknown> | null
  ip_address: string | null
  created_at: string
}

export function AuditLogList() {
  const [page, setPage] = useState(1)
  const [actionFilter, setActionFilter] = useState('')
  const [targetFilter, setTargetFilter] = useState('')

  const { data, isLoading } = useList<AuditLog>({
    resource: 'audit-logs',
    pagination: { current: page, pageSize: 50 },
    filters: [
      ...(actionFilter ? [{ field: 'action', operator: 'eq' as const, value: actionFilter }] : []),
      ...(targetFilter ? [{ field: 'target_type', operator: 'eq' as const, value: targetFilter }] : []),
    ],
  })

  const logs = data?.data || []
  const total = data?.total || 0
  const totalPages = Math.ceil(total / 50)

  const getActionColor = (action: string) => {
    if (action.includes('delete')) return 'bg-red-100 text-red-700'
    if (action.includes('ban')) return 'bg-orange-100 text-orange-700'
    if (action.includes('create')) return 'bg-green-100 text-green-700'
    return 'bg-blue-100 text-blue-700'
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Audit Logs</h1>
        <div className="text-sm text-gray-500">{total} total entries</div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="flex gap-4">
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="px-4 py-2 border border-gray-300 rounded-lg"
          >
            <option value="">All Actions</option>
            <option value="user.update">User Update</option>
            <option value="user.ban">User Ban</option>
            <option value="user.unban">User Unban</option>
            <option value="user.delete">User Delete</option>
            <option value="team.delete">Team Delete</option>
            <option value="team.transfer_ownership">Team Transfer</option>
          </select>
          <select
            value={targetFilter}
            onChange={(e) => setTargetFilter(e.target.value)}
            className="px-4 py-2 border border-gray-300 rounded-lg"
          >
            <option value="">All Targets</option>
            <option value="user">User</option>
            <option value="team">Team</option>
            <option value="video">Video</option>
            <option value="tag">Tag</option>
          </select>
        </div>
      </div>

      {/* Logs Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Time</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Admin</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Target</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Details</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">IP</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {isLoading ? (
              <tr>
                <td colSpan={6} className="px-6 py-8 text-center text-gray-500">Loading...</td>
              </tr>
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-6 py-8 text-center text-gray-500">No audit logs found</td>
              </tr>
            ) : (
              logs.map((log) => (
                <tr key={log.id}>
                  <td className="px-6 py-4 text-sm text-gray-500 whitespace-nowrap">
                    {new Date(log.created_at).toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-sm">
                    {log.admin_email || log.admin_id.slice(0, 8)}
                  </td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 text-xs rounded ${getActionColor(log.action)}`}>
                      {log.action}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm">
                    <span className="text-gray-500">{log.target_type}:</span>{' '}
                    <code className="text-xs bg-gray-100 px-1 rounded">{log.target_id.slice(0, 8)}</code>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 max-w-xs truncate">
                    {log.details ? JSON.stringify(log.details) : '-'}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-400">
                    {log.ip_address || '-'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-6 py-4 border-t flex items-center justify-between">
            <div className="text-sm text-gray-500">
              Page {page} of {totalPages}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                <ChevronLeft size={18} />
              </button>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
```

**Step 2: Create admin/src/pages/audit-logs/index.ts**

```typescript
export { AuditLogList } from './AuditLogList'
```

**Step 3: Commit**

```bash
git add admin/src/pages/audit-logs/
git commit -m "feat(admin): add audit logs page"
```

---

### Task 19: Update Dashboard with Real Stats

**Files:**
- Modify: `admin/src/pages/Dashboard.tsx`

**Step 1: Update admin/src/pages/Dashboard.tsx**

```tsx
import { useCustom } from '@refinedev/core'
import { LayoutDashboard, Users, Video, CreditCard, TrendingUp, Activity } from 'lucide-react'

interface Stats {
  total_users: number
  total_videos: number
  total_teams: number
  total_downloads: number
  active_users_today: number
  new_users_today: number
  new_videos_today: number
}

export function Dashboard() {
  const { data, isLoading } = useCustom<Stats>({
    url: '/api/v1/admin/stats/overview',
    method: 'get',
  })

  const stats = data?.data

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>

      {/* Main Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <StatCard
          title="Total Users"
          value={isLoading ? '--' : stats?.total_users?.toLocaleString() || '0'}
          icon={Users}
          color="blue"
        />
        <StatCard
          title="Total Videos"
          value={isLoading ? '--' : stats?.total_videos?.toLocaleString() || '0'}
          icon={Video}
          color="green"
        />
        <StatCard
          title="Active Teams"
          value={isLoading ? '--' : stats?.total_teams?.toLocaleString() || '0'}
          icon={LayoutDashboard}
          color="purple"
        />
        <StatCard
          title="Downloads"
          value={isLoading ? '--' : stats?.total_downloads?.toLocaleString() || '0'}
          icon={CreditCard}
          color="orange"
        />
      </div>

      {/* Today's Activity */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-3 mb-2">
            <Activity className="text-blue-500" size={20} />
            <h3 className="font-medium text-gray-700">Active Today</h3>
          </div>
          <p className="text-3xl font-bold text-gray-900">
            {isLoading ? '--' : stats?.active_users_today || 0}
          </p>
          <p className="text-sm text-gray-500 mt-1">users with activity</p>
        </div>

        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-3 mb-2">
            <TrendingUp className="text-green-500" size={20} />
            <h3 className="font-medium text-gray-700">New Users Today</h3>
          </div>
          <p className="text-3xl font-bold text-gray-900">
            {isLoading ? '--' : stats?.new_users_today || 0}
          </p>
          <p className="text-sm text-gray-500 mt-1">registrations</p>
        </div>

        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center gap-3 mb-2">
            <Video className="text-purple-500" size={20} />
            <h3 className="font-medium text-gray-700">New Videos Today</h3>
          </div>
          <p className="text-3xl font-bold text-gray-900">
            {isLoading ? '--' : stats?.new_videos_today || 0}
          </p>
          <p className="text-sm text-gray-500 mt-1">videos added</p>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="font-semibold text-gray-900 mb-4">Quick Actions</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <a
            href="/users"
            className="p-4 border rounded-lg hover:bg-gray-50 text-center"
          >
            <Users className="mx-auto mb-2 text-gray-600" size={24} />
            <span className="text-sm text-gray-700">Manage Users</span>
          </a>
          <a
            href="/teams"
            className="p-4 border rounded-lg hover:bg-gray-50 text-center"
          >
            <LayoutDashboard className="mx-auto mb-2 text-gray-600" size={24} />
            <span className="text-sm text-gray-700">Manage Teams</span>
          </a>
          <a
            href="/audit-logs"
            className="p-4 border rounded-lg hover:bg-gray-50 text-center"
          >
            <Activity className="mx-auto mb-2 text-gray-600" size={24} />
            <span className="text-sm text-gray-700">View Audit Logs</span>
          </a>
          <a
            href="/settings"
            className="p-4 border rounded-lg hover:bg-gray-50 text-center"
          >
            <CreditCard className="mx-auto mb-2 text-gray-600" size={24} />
            <span className="text-sm text-gray-700">System Settings</span>
          </a>
        </div>
      </div>
    </div>
  )
}

interface StatCardProps {
  title: string
  value: string
  icon: React.ComponentType<{ className?: string }>
  color: 'blue' | 'green' | 'purple' | 'orange'
}

function StatCard({ title, value, icon: Icon, color }: StatCardProps) {
  const colorClasses = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-500">{title}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
        </div>
        <div className={`p-3 rounded-lg ${colorClasses[color]}`}>
          <Icon className="w-6 h-6" />
        </div>
      </div>
    </div>
  )
}
```

**Step 2: Commit**

```bash
git add admin/src/pages/Dashboard.tsx
git commit -m "feat(admin): update dashboard with real statistics"
```

---

### Task 20: Update App.tsx with All Routes

**Files:**
- Modify: `admin/src/App.tsx`

**Step 1: Update admin/src/App.tsx**

```tsx
import { Refine, Authenticated } from '@refinedev/core'
import routerBindings, {
  NavigateToResource,
  CatchAllNavigate,
} from '@refinedev/react-router-v6'
import { BrowserRouter, Routes, Route, Outlet } from 'react-router-dom'

import { authProvider } from './providers/authProvider'
import { dataProvider } from './providers/dataProvider'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { UserList } from './pages/users'
import { TeamList } from './pages/teams'
import { AuditLogList } from './pages/audit-logs'

function App() {
  return (
    <BrowserRouter>
      <Refine
        authProvider={authProvider}
        dataProvider={dataProvider}
        routerProvider={routerBindings}
        resources={[
          { name: 'dashboard', list: '/' },
          { name: 'users', list: '/users', show: '/users/:id' },
          { name: 'teams', list: '/teams', show: '/teams/:id' },
          { name: 'videos', list: '/videos', show: '/videos/:id' },
          { name: 'tags', list: '/tags' },
          { name: 'credits', list: '/credits' },
          { name: 'audit-logs', list: '/audit-logs' },
          { name: 'api-keys', list: '/api-keys' },
          { name: 'settings', list: '/settings' },
        ]}
        options={{
          syncWithLocation: true,
          warnWhenUnsavedChanges: true,
        }}
      >
        <Routes>
          <Route
            element={
              <Authenticated fallback={<CatchAllNavigate to="/login" />}>
                <Layout />
              </Authenticated>
            }
          >
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
          <Route
            element={
              <Authenticated fallback={<Outlet />}>
                <NavigateToResource />
              </Authenticated>
            }
          >
            <Route path="/login" element={<Login />} />
          </Route>
        </Routes>
      </Refine>
    </BrowserRouter>
  )
}

function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-4">{title}</h1>
      <div className="bg-white rounded-lg shadow p-6">
        <p className="text-gray-500">
          This page will be implemented in Phase 4.
        </p>
      </div>
    </div>
  )
}

export default App
```

**Step 2: Verify frontend builds**

Run: `cd admin && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add admin/src/App.tsx
git commit -m "feat(admin): wire up all implemented pages in routing"
```

---

## Phase 3 Complete Checkpoint

At this point you should have:
- Users list page with search, filter, ban, delete
- Teams list page with expandable member view
- Audit logs page with filtering
- Dashboard with real statistics
- All pages wired up in routing

**Verify:**
1. Frontend builds: `cd admin && npm run build`
2. Run dev: `cd admin && npm run dev`
3. Navigate through pages

---

## Phase 4: Docker & Deployment

### Task 21: Create Admin Dockerfile

**Files:**
- Create: `admin/Dockerfile`
- Create: `admin/nginx.conf`

**Step 1: Create admin/nginx.conf**

```nginx
server {
    listen 80;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api {
        proxy_pass http://mediahub:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**Step 2: Create admin/Dockerfile**

```dockerfile
# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

**Step 3: Commit**

```bash
git add admin/Dockerfile admin/nginx.conf
git commit -m "feat(admin): add Dockerfile and nginx config"
```

---

### Task 22: Add Admin to Docker Compose

**Files:**
- Modify: `docker/docker-compose.yml`

**Step 1: Add mediahub-admin service to docker/docker-compose.yml**

Add after the existing services:

```yaml
  mediahub-admin:
    image: imheygo/mediahub-admin:latest
    container_name: mediahub-admin
    restart: unless-stopped
    ports:
      - "3097:80"
    environment:
      - VITE_SUPABASE_URL=${SUPABASE_URL}
      - VITE_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
      - VITE_API_URL=http://mediahub:8080
    depends_on:
      - mediahub
    networks:
      - mediahub-network
```

**Step 2: Commit**

```bash
git add docker/docker-compose.yml
git commit -m "feat(docker): add mediahub-admin service to compose"
```

---

### Task 23: Create GitHub Actions Workflow for Admin

**Files:**
- Create: `.github/workflows/admin-docker.yml`

**Step 1: Create .github/workflows/admin-docker.yml**

```yaml
name: Build Admin Docker Image

on:
  push:
    tags:
      - 'admin-v*'

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Extract version
        id: version
        run: echo "VERSION=${GITHUB_REF#refs/tags/admin-}" >> $GITHUB_OUTPUT

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: ./admin
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            imheygo/mediahub-admin:latest
            imheygo/mediahub-admin:${{ steps.version.outputs.VERSION }}
```

**Step 2: Commit**

```bash
git add .github/workflows/admin-docker.yml
git commit -m "feat(ci): add GitHub Actions workflow for admin Docker build"
```

---

## Final Summary

This implementation plan covers:

**Phase 1 (Foundation):**
- Tasks 1-10: Admin frontend project setup, TailwindCSS, Supabase client, auth provider, data provider, layout components, login page, backend admin middleware, database migrations

**Phase 2 (Core Modules):**
- Tasks 11-15: Backend admin APIs for users, teams, audit logs, statistics

**Phase 3 (Frontend Pages):**
- Tasks 16-20: Users list, teams list, audit logs page, dashboard with stats

**Phase 4 (Deployment):**
- Tasks 21-23: Dockerfile, docker-compose, GitHub Actions

**Not Covered (Future Phases):**
- Videos management page
- Tags management page
- Credits system pages
- API keys management page
- Settings page
- Charts and analytics

---

**Plan complete and saved to `docs/plans/2026-02-03-admin-system-implementation.md`.**

Two execution options:

1. **Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

2. **Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
