import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface UnifiedLogEntry {
  id: string
  source: 'request' | 'app' | 'frontend' | 'audit'
  timestamp: string
  level?: string
  method?: string
  path?: string
  status_code?: number
  response_time_ms?: number
  module?: string
  message?: string
  action?: string
  target_type?: string
  request_id?: string
  details?: Record<string, unknown>
}

export interface Facets {
  levels: Record<string, number>
  sources: Record<string, number>
  modules: Record<string, number>
  status_codes: Record<string, number>
}

export interface SearchResponse {
  items: UnifiedLogEntry[]
  total: number
  facets: Facets
}

export interface TraceEntry {
  source: 'request' | 'app'
  timestamp: string
  level?: string
  module?: string
  message?: string
  status_code?: number
  response_time_ms?: number
  offset_ms: number
}

export interface TraceResponse {
  request_id: string
  method?: string
  path?: string
  status_code?: number
  total_ms?: number
  entries: TraceEntry[]
}

interface SearchParams {
  q?: string
  filters?: string
  sources?: string
  period?: string
  start_date?: string
  end_date?: string
  page?: number
  page_size?: number
}

export function useSearchLogs(params: SearchParams, enabled = true) {
  return useQuery({
    queryKey: ['admin', 'search', params],
    queryFn: async () => {
      const query: Record<string, string | number> = {}
      if (params.q) query.q = params.q
      if (params.filters) query.filters = params.filters
      if (params.sources) query.sources = params.sources
      if (params.period) query.period = params.period
      if (params.start_date) query.start_date = params.start_date
      if (params.end_date) query.end_date = params.end_date
      if (params.page) query.page = params.page
      if (params.page_size) query.page_size = params.page_size

      const { data } = await apiClient.get<SearchResponse>(
        '/api/v1/admin/search',
        { params: query },
      )
      return data
    },
    enabled,
  })
}

export function useRequestTrace(requestId: string | null) {
  return useQuery({
    queryKey: ['admin', 'search', 'trace', requestId],
    queryFn: async () => {
      const { data } = await apiClient.get<TraceResponse>(
        `/api/v1/admin/search/trace/${requestId}`,
      )
      return data
    },
    enabled: !!requestId,
  })
}
