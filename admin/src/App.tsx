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
import { AIGovernance } from './pages/settings/AIGovernance'
import { TopicScoring } from './pages/settings/TopicScoring'
import { SignalSources } from './pages/settings/SignalSources'
import { MemorySettings } from './pages/settings/MemorySettings'
import { Modules } from './pages/settings/Modules'
import { RequestLogs } from './pages/request-logs'
import { MonitoringDashboard } from './pages/monitoring'
import { GlobalSearch } from './pages/search'
import { Alerts } from './pages/alerts'
import { TranscodeList } from './pages/transcode'
import { TranscodeConfig } from './pages/transcode-config'
import { TaskCenter } from './pages/tasks'
import { AIModelsPage } from './pages/ai'
import { AgentTelemetryDashboard } from './pages/agent-telemetry'
import { AgentsCatalogPage } from './pages/agents'
import { AiUsagePage } from './pages/ai-usage'
import { DeploymentLogsPage } from './pages/deployment-logs'

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
              <Route path="/agent-telemetry" element={<AgentTelemetryDashboard />} />
              <Route path="/agents-catalog" element={<AgentsCatalogPage />} />
              <Route path="/ai-usage" element={<AiUsagePage />} />
              <Route path="/monitoring" element={<MonitoringDashboard />} />
              <Route path="/search" element={<GlobalSearch />} />
              <Route path="/audit-logs" element={<AuditLogList />} />
              <Route path="/logs" element={<RequestLogs />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route
                path="/api-keys"
                element={
                  <PlaceholderPage
                    title="API Keys"
                    description="Planned: admin oversight of users' programmatic API keys — the access tokens (with scopes, max 10/user) that call the MediaHub API for external integrations and automations (backend api_key_router already exists; this page will list / revoke them). Not AI provider keys — those live in System → AI Models."
                  />
                }
              />
              <Route path="/settings" element={<Settings />} />
              <Route path="/settings/modules" element={<Modules />} />
              <Route path="/settings/ai-governance" element={<AIGovernance />} />
              <Route path="/settings/topic-scoring" element={<TopicScoring />} />
              <Route path="/settings/signal-sources" element={<SignalSources />} />
              <Route path="/settings/memory" element={<MemorySettings />} />
              <Route path="/transcode-config" element={<TranscodeConfig />} />
              <Route path="/deployment-logs" element={<DeploymentLogsPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
    </ErrorBoundary>
  )
}
