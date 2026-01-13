import { useQuery } from '@tanstack/react-query'
import { douyinApi } from '@/lib/api'
import {
  Video,
  Download,
  CheckCircle,
  AlertCircle,
  Clock,
  TrendingUp,
} from 'lucide-react'
import { cn, formatNumber } from '@/lib/utils'
import type { Statistics } from '@/lib/supabase'

function StatCard({
  title,
  value,
  icon: Icon,
  color,
  subtitle,
}: {
  title: string
  value: number | string
  icon: React.ElementType
  color: string
  subtitle?: string
}) {
  return (
    <div className="card p-6">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-dark-400 mb-1">{title}</p>
          <p className="text-2xl font-bold text-white">{value}</p>
          {subtitle && <p className="text-xs text-dark-500 mt-1">{subtitle}</p>}
        </div>
        <div className={cn('p-3 rounded-lg', color)}>
          <Icon className="w-6 h-6 text-white" />
        </div>
      </div>
    </div>
  )
}

export default function DashboardPage() {
  const { data: statsData, isLoading } = useQuery({
    queryKey: ['statistics'],
    queryFn: () => douyinApi.getStatistics(),
  })

  const stats: Statistics = statsData?.statistics || {
    total: 0,
    pending: 0,
    completed: 0,
    failed: 0,
    skipped: 0,
  }

  const completionRate = stats.total > 0
    ? ((stats.completed / stats.total) * 100).toFixed(1)
    : '0'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">仪表盘</h1>
        <p className="text-dark-400 mt-1">查看系统概览和统计数据</p>
      </div>

      {/* Stats Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="card p-6 animate-pulse">
              <div className="h-4 w-20 bg-dark-700 rounded mb-3" />
              <div className="h-8 w-16 bg-dark-700 rounded" />
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            title="总视频数"
            value={formatNumber(stats.total)}
            icon={Video}
            color="bg-blue-500"
          />
          <StatCard
            title="已完成"
            value={formatNumber(stats.completed)}
            icon={CheckCircle}
            color="bg-green-500"
            subtitle={`完成率 ${completionRate}%`}
          />
          <StatCard
            title="待下载"
            value={formatNumber(stats.pending)}
            icon={Clock}
            color="bg-yellow-500"
          />
          <StatCard
            title="下载失败"
            value={formatNumber(stats.failed)}
            icon={AlertCircle}
            color="bg-red-500"
          />
        </div>
      )}

      {/* Quick Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Activity */}
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-primary-500" />
            快速操作
          </h2>
          <div className="space-y-3">
            <a
              href="/fetch"
              className="block p-4 bg-dark-700 hover:bg-dark-600 rounded-lg transition-colors"
            >
              <div className="flex items-center gap-3">
                <Download className="w-5 h-5 text-primary-500" />
                <div>
                  <p className="font-medium text-white">获取新视频</p>
                  <p className="text-sm text-dark-400">输入抖音链接获取视频</p>
                </div>
              </div>
            </a>
            <a
              href="/videos"
              className="block p-4 bg-dark-700 hover:bg-dark-600 rounded-lg transition-colors"
            >
              <div className="flex items-center gap-3">
                <Video className="w-5 h-5 text-blue-500" />
                <div>
                  <p className="font-medium text-white">管理视频</p>
                  <p className="text-sm text-dark-400">查看和管理所有视频</p>
                </div>
              </div>
            </a>
          </div>
        </div>

        {/* System Status */}
        <div className="card p-6">
          <h2 className="text-lg font-semibold text-white mb-4">系统状态</h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-dark-300">数据库连接</span>
              <span className="badge badge-success">正常</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-dark-300">下载服务</span>
              <span className="badge badge-success">运行中</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-dark-300">存储空间</span>
              <span className="text-dark-400">-</span>
            </div>
          </div>

          {/* Progress Bar */}
          <div className="mt-6">
            <div className="flex items-center justify-between text-sm mb-2">
              <span className="text-dark-400">下载进度</span>
              <span className="text-white">{completionRate}%</span>
            </div>
            <div className="h-2 bg-dark-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary-500 rounded-full transition-all duration-300"
                style={{ width: `${completionRate}%` }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
