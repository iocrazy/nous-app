import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface OverviewStats {
  total_requests: number
  error_rate: number
  avg_response_ms: number
  app_error_count: number
  frontend_error_count: number
}

export interface TrendPoint {
  time: string
  requests: number
  errors: number
}

export interface SlowApiEntry {
  path: string
  avg_ms: number
  p95_ms: number
  count: number
}

export interface ErrorEndpointEntry {
  path: string
  error_count: number
  last_status: number | null
}

export interface ErrorModuleEntry {
  module: string
  count: number
}

export interface RecentErrorEntry {
  level: string
  module: string | null
  message: string
  logged_at: string
}

export interface MonitoringStats {
  overview: OverviewStats
  request_trend: TrendPoint[]
  top_slow_apis: SlowApiEntry[]
  top_error_endpoints: ErrorEndpointEntry[]
  log_level_distribution: Record<string, number>
  top_error_modules: ErrorModuleEntry[]
  recent_errors: RecentErrorEntry[]
}

interface MonitoringParams {
  period?: string
  start_date?: string
  end_date?: string
}

export function useMonitoringStats(params: MonitoringParams) {
  return useQuery({
    queryKey: ['admin', 'monitoring', 'stats', params],
    queryFn: async () => {
      const query: Record<string, string> = {}
      if (params.period) query.period = params.period
      if (params.start_date) query.start_date = params.start_date
      if (params.end_date) query.end_date = params.end_date

      const { data } = await apiClient.get<MonitoringStats>(
        '/api/v1/admin/monitoring/stats',
        { params: query },
      )
      return data
    },
    refetchInterval: 60_000,
  })
}
