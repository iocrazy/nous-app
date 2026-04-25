import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface HealthCheck {
  name: string
  status: 'healthy' | 'degraded' | 'down' | 'unknown'
  detail: string
  metrics?: Record<string, unknown>
}

export interface BackendHealthResponse {
  overall: 'healthy' | 'degraded' | 'down'
  checks: HealthCheck[]
  timestamp: number
}

/** Backend health snapshot, refreshed every 10s. Admin-only endpoint. */
export function useBackendHealth() {
  return useQuery({
    queryKey: ['system', 'health'],
    queryFn: async () => {
      const { data } = await apiClient.get<BackendHealthResponse>(
        '/api/v1/system/health',
      )
      return data
    },
    refetchInterval: 10_000,
    refetchOnWindowFocus: true,
  })
}
