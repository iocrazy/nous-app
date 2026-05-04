import React from 'react';
import {
  Activity, ListTodo, ScrollText, Cpu, Brain, FileText, Calendar, Layers,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { StatsChart } from '../components/StatsChart';
import { TasksPanel } from '../components/TasksPanel';
import { LogsPanel } from '../components/LogsPanel';
import { SystemMonitorPanel } from '../components/SystemMonitorPanel';
import { AdminNousModelsPage } from './admin/AdminNousModelsPage';
import { AdminDeploymentLogsPage } from './admin/AdminDeploymentLogsPage';
import { AdminLanesPage } from './admin/AdminLanesPage';
import { SchedulesPage } from './SchedulesPage';
import { useAuth } from '../contexts/AuthContext';
import { useNavigation } from '../hooks/useNavigation';
import { useTeamContext } from '../contexts/TeamContext';

type SubView =
  | 'overview' | 'tasks' | 'logs' | 'monitor'
  | 'schedules' | 'lanes' | 'nous-models' | 'deployment-logs';

interface SubViewTab {
  value: SubView;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
}

const SUBVIEW_TABS: SubViewTab[] = [
  { value: 'overview',        label: 'Overview',        icon: <Activity size={14} /> },
  { value: 'tasks',           label: 'Tasks',           icon: <ListTodo size={14} /> },
  { value: 'logs',            label: 'Logs',            icon: <ScrollText size={14} /> },
  { value: 'schedules',       label: 'Schedules',       icon: <Calendar size={14} /> },
  { value: 'monitor',         label: 'Monitor',         icon: <Cpu size={14} />,           adminOnly: true },
  { value: 'lanes',           label: 'Lanes',           icon: <Layers size={14} />,        adminOnly: true },
  { value: 'nous-models',     label: 'Nous Models',     icon: <Brain size={14} />,         adminOnly: true },
  { value: 'deployment-logs', label: 'Deploy Logs',     icon: <FileText size={14} />,      adminOnly: true },
];

export function DashboardPage() {
  const { t } = useTranslation();
  const { userProfile } = useAuth();
  const { selectedTeamId } = useTeamContext();
  const { dashboardSubView, setDashboardSubView, dashboardStats } = useNavigation({
    isAuthenticated: true,
    selectedTeamId,
  });

  const isAdmin = userProfile.role === 'admin';
  const visibleTabs = SUBVIEW_TABS.filter((tab) => !tab.adminOnly || isAdmin);

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* A6: dashboard sub-view nav strip — surfaces tasks/schedules/lanes/etc. */}
      <div className="mb-6 flex flex-wrap items-center gap-1 p-1 bg-zinc-900 border border-zinc-800 rounded-lg overflow-x-auto">
        {visibleTabs.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setDashboardSubView(tab.value)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium whitespace-nowrap transition ${
              dashboardSubView === tab.value
                ? 'bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-500/30'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {dashboardSubView === 'overview' && (
        <div className="space-y-8">
          <header className="mb-8">
            <h1 className="text-2xl font-bold text-white">{t('dashboard.title')}</h1>
            <p className="text-zinc-400 text-sm">{t('dashboard.subtitle')}</p>
          </header>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            {(() => {
              const formatStorage = (bytes: number): string => {
                if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
                if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
                if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
                return `${bytes} B`;
              };
              const stats = dashboardStats;
              return [
                { label: t('dashboard.totalVideos'), val: stats?.totalVideos?.toLocaleString() || "0", change: `${stats?.completedDownloads || 0} done`, color: "text-indigo-400" },
                { label: t('dashboard.storageUsed'), val: formatStorage(stats?.totalStorageBytes || 0), change: `${stats?.pendingDownloads || 0} pending`, color: "text-purple-400" },
                { label: t('dashboard.savedCreators'), val: (stats?.uniqueAuthors || 0).toString(), change: `${stats?.failedDownloads || 0} failed`, color: "text-pink-400" },
                { label: t('dashboard.successRate'), val: stats?.totalVideos ? `${Math.round((stats.completedDownloads / stats.totalVideos) * 100)}%` : "0%", change: "overall", color: "text-green-400" },
              ];
            })().map((stat, i) => (
              <div key={i} className="bg-zinc-900 border border-zinc-800 p-6 rounded-xl shadow-sm">
                <p className="text-zinc-500 text-sm font-medium mb-2">{stat.label}</p>
                <div className="flex items-end justify-between">
                  <span className="text-2xl font-bold text-white">{stat.val}</span>
                  <span className={`text-xs ${stat.color} bg-zinc-950 px-1.5 py-0.5 rounded`}>{stat.change}</span>
                </div>
              </div>
            ))}
          </div>

          <StatsChart
            weeklyActivity={dashboardStats?.weeklyActivity || []}
            mediaDistribution={dashboardStats?.mediaDistribution || []}
            topTags={dashboardStats?.topTags || []}
          />

          {dashboardStats?.recentLogs && dashboardStats.recentLogs.length > 0 && (
            <div className="mt-8 bg-zinc-900 border border-zinc-800 rounded-xl p-6">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <Activity size={18} className="text-amber-400" />
                  <h3 className="text-sm font-semibold text-zinc-200">Recent Activity</h3>
                </div>
                <button
                  onClick={() => setDashboardSubView('logs')}
                  className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  View all logs
                </button>
              </div>
              <div className="space-y-2">
                {dashboardStats.recentLogs.slice(0, 5).map((log, i) => (
                  <div key={i} className="flex items-center gap-3 py-1.5">
                    <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                      log.status === 'success' ? 'bg-green-400' :
                      log.status === 'error' ? 'bg-red-400' :
                      'bg-zinc-500'
                    }`} />
                    <span className="text-sm text-zinc-300 truncate flex-1">{log.message}</span>
                    <span className="text-xs text-zinc-600 flex-shrink-0">{log.time}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {dashboardSubView === 'tasks' && <TasksPanel />}
      {dashboardSubView === 'logs' && <LogsPanel />}
      {dashboardSubView === 'monitor' && userProfile.role === 'admin' && <SystemMonitorPanel />}
      {dashboardSubView === 'nous-models' && userProfile.role === 'admin' && <AdminNousModelsPage />}
      {dashboardSubView === 'deployment-logs' && userProfile.role === 'admin' && <AdminDeploymentLogsPage />}
      {dashboardSubView === 'schedules' && <SchedulesPage />}
      {dashboardSubView === 'lanes' && userProfile.role === 'admin' && <AdminLanesPage />}
    </div>
  );
}
