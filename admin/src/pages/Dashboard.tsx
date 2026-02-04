import { useCustom } from '@refinedev/core'
import { Link } from 'react-router-dom'
import {
  LayoutDashboard,
  Users,
  Video,
  Download,
  TrendingUp,
  Activity,
  UserPlus,
  Settings,
  ScrollText,
  Users2,
} from 'lucide-react'

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

      {/* Main Stats - 4 cards */}
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
          icon={Download}
          color="orange"
        />
      </div>

      {/* Today's Activity - 3 cards */}
      <h2 className="text-lg font-semibold text-gray-900 mb-4">Today's Activity</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <ActivityCard
          title="Active Today"
          value={isLoading ? '--' : stats?.active_users_today?.toLocaleString() || '0'}
          icon={Activity}
          description="Users active in the last 24 hours"
        />
        <ActivityCard
          title="New Users Today"
          value={isLoading ? '--' : stats?.new_users_today?.toLocaleString() || '0'}
          icon={UserPlus}
          description="Users registered today"
        />
        <ActivityCard
          title="New Videos Today"
          value={isLoading ? '--' : stats?.new_videos_today?.toLocaleString() || '0'}
          icon={TrendingUp}
          description="Videos added today"
        />
      </div>

      {/* Quick Actions */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="font-semibold text-gray-900 mb-4">Quick Actions</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <QuickActionLink to="/users" icon={Users} label="Manage Users" />
          <QuickActionLink to="/teams" icon={Users2} label="Manage Teams" />
          <QuickActionLink to="/audit-logs" icon={ScrollText} label="View Audit Logs" />
          <QuickActionLink to="/settings" icon={Settings} label="Settings" />
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

interface ActivityCardProps {
  title: string
  value: string
  icon: React.ComponentType<{ className?: string }>
  description: string
}

function ActivityCard({ title, value, icon: Icon, description }: ActivityCardProps) {
  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="flex items-center gap-4">
        <div className="p-3 rounded-lg bg-gray-100 text-gray-600">
          <Icon className="w-6 h-6" />
        </div>
        <div>
          <p className="text-sm text-gray-500">{title}</p>
          <p className="text-xl font-bold text-gray-900">{value}</p>
          <p className="text-xs text-gray-400 mt-1">{description}</p>
        </div>
      </div>
    </div>
  )
}

interface QuickActionLinkProps {
  to: string
  icon: React.ComponentType<{ className?: string }>
  label: string
}

function QuickActionLink({ to, icon: Icon, label }: QuickActionLinkProps) {
  return (
    <Link
      to={to}
      className="flex flex-col items-center gap-2 p-4 rounded-lg border border-gray-200 hover:border-blue-500 hover:bg-blue-50 transition-colors group"
    >
      <Icon className="w-6 h-6 text-gray-500 group-hover:text-blue-600" />
      <span className="text-sm text-gray-700 group-hover:text-blue-600">{label}</span>
    </Link>
  )
}
