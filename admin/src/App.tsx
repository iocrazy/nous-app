import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AdminLayout } from './layouts/AdminLayout'
import { Login } from './pages/login'
import { Dashboard } from './pages/dashboard'
import { UserList } from './pages/users'
import { TeamList } from './pages/teams'
import { AuditLogList } from './pages/audit-logs'
import { VideoList } from './pages/videos'
import { PlaceholderPage } from './pages/placeholder'
import { Settings } from './pages/settings'
import { RequestLogs } from './pages/request-logs'
import { MonitoringDashboard } from './pages/monitoring'
import { GlobalSearch } from './pages/search'
import { Alerts } from './pages/alerts'

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
              <Route path="/videos" element={<VideoList />} />
              <Route path="/tags" element={<PlaceholderPage title="Tags" />} />
              <Route path="/credits" element={<PlaceholderPage title="Credits" />} />
              <Route path="/monitoring" element={<MonitoringDashboard />} />
              <Route path="/search" element={<GlobalSearch />} />
              <Route path="/audit-logs" element={<AuditLogList />} />
              <Route path="/request-logs" element={<RequestLogs />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="/api-keys" element={<PlaceholderPage title="API Keys" />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
