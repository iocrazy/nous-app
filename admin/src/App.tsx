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
              <Authenticated key="authenticated-routes" fallback={<CatchAllNavigate to="/login" />}>
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
              <Authenticated key="auth-pages" fallback={<Outlet />}>
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
