import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AuthProvider } from './auth/AuthProvider'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AdminLayout } from './layouts/AdminLayout'
import { Login } from './pages/login'
import { Dashboard } from './pages/dashboard'
import { UserList } from './pages/users'
import { TeamList } from './pages/teams'
import { AuditLogList } from './pages/audit-logs'
import { MediaList } from './pages/media'
import { PlaceholderPage } from './pages/placeholder'
import { TagsPage } from './pages/tags'
import { CreditsPage } from './pages/credits'
import { Settings } from './pages/settings'
import { RequestLogs } from './pages/request-logs'
import { MonitoringDashboard } from './pages/monitoring'
import { GlobalSearch } from './pages/search'
import { Alerts } from './pages/alerts'
import { TranscodeList } from './pages/transcode'
import { TranscodeConfig } from './pages/transcode-config'
import { TaskCenter } from './pages/tasks'
import { AIModelsPage } from './pages/ai'

export default function App() {
  return (
    <ErrorBoundary>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<AdminLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="/users" element={<UserList />} />
              <Route path="/teams" element={<TeamList />} />
              <Route path="/media" element={<MediaList />} />
              <Route path="/videos" element={<Navigate to="/media" replace />} />
              <Route path="/transcode" element={<TranscodeList />} />
              <Route path="/tasks" element={<TaskCenter />} />
              <Route path="/tags" element={<TagsPage />} />
              <Route path="/credits" element={<CreditsPage />} />
              <Route path="/ai" element={<AIModelsPage />} />
              <Route path="/monitoring" element={<MonitoringDashboard />} />
              <Route path="/search" element={<GlobalSearch />} />
              <Route path="/audit-logs" element={<AuditLogList />} />
              <Route path="/logs" element={<RequestLogs />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="/api-keys" element={<PlaceholderPage title="API Keys" />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/transcode-config" element={<TranscodeConfig />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
    </ErrorBoundary>
  )
}
