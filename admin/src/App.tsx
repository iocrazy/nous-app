import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
