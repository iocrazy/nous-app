import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { apiClient } from '../client'

// ============================================
// Types
// ============================================

export interface RequestLog {
  id: string
  request_id: string
  user_id: string | null
  user_email: string | null
  auth_type: string
  method: string
  path: string
  query_params: Record<string, unknown> | null
  request_body: Record<string, unknown> | null
  status_code: number | null
  response_time_ms: number | null
  ip_address: string | null
  user_agent: string | null
  error_detail: string | null
  timestamp: string
}

interface RequestLogListResponse {
  data: RequestLog[]
  total: number
}

export interface FrontendError {
  id: string
  user_id: string | null
  user_email: string | null
  session_id: string | null
  error_type: string
  message: string | null
  stack: string | null
  url: string | null
  component: string | null
  user_agent: string | null
  metadata: Record<string, unknown> | null
  created_at: string
}

interface FrontendErrorListResponse {
  data: FrontendError[]
  total: number
}

export interface AppLog {
  id: string
  level: string
  message: string
  module: string | null
  function: string | null
  line: number | null
  file_path: string | null
  exception: string | null
  extra: Record<string, unknown> | null
  logged_at: string
}

interface AppLogListResponse {
  data: AppLog[]
  total: number
}

// ============================================
// Hooks
// ============================================

interface RequestLogsParams {
  page: number
  pageSize: number
  method?: string
  path?: string
  status_group?: string
  user_id?: string
  min_response_time?: number
  request_id?: string
  start_date?: string
  end_date?: string
}

export function useRequestLogs(params: RequestLogsParams) {
  return useQuery({
    queryKey: ['admin', 'request-logs', params],
    queryFn: async () => {
      const query: Record<string, string | number> = {
        page: params.page,
        pageSize: params.pageSize,
      }
      if (params.method) query.method = params.method
      if (params.path) query.path = params.path
      if (params.status_group) query.status_group = params.status_group
      if (params.user_id) query.user_id = params.user_id
      if (params.min_response_time) query.min_response_time = params.min_response_time
      if (params.request_id) query.request_id = params.request_id
      if (params.start_date) query.start_date = params.start_date
      if (params.end_date) query.end_date = params.end_date

      const { data } = await apiClient.get<RequestLogListResponse>(
        '/api/v1/admin/request-logs',
        { params: query },
      )
      return data
    },
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })
}

interface FrontendErrorsParams {
  page: number
  pageSize: number
  error_type?: string
  start_date?: string
  end_date?: string
}

export function useFrontendErrors(params: FrontendErrorsParams) {
  return useQuery({
    queryKey: ['admin', 'frontend-errors', params],
    queryFn: async () => {
      const query: Record<string, string | number> = {
        page: params.page,
        pageSize: params.pageSize,
      }
      if (params.error_type) query.error_type = params.error_type
      if (params.start_date) query.start_date = params.start_date
      if (params.end_date) query.end_date = params.end_date

      const { data } = await apiClient.get<FrontendErrorListResponse>(
        '/api/v1/admin/request-logs/frontend-errors',
        { params: query },
      )
      return data
    },
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })
}

interface AppLogsParams {
  page: number
  pageSize: number
  level?: string
  module?: string
  message?: string
  has_exception?: boolean
  start_date?: string
  end_date?: string
}

export function useAppLogs(params: AppLogsParams) {
  return useQuery({
    queryKey: ['admin', 'app-logs', params],
    queryFn: async () => {
      const query: Record<string, string | number | boolean> = {
        page: params.page,
        pageSize: params.pageSize,
      }
      if (params.level) query.level = params.level
      if (params.module) query.module = params.module
      if (params.message) query.message = params.message
      if (params.has_exception !== undefined) query.has_exception = params.has_exception
      if (params.start_date) query.start_date = params.start_date
      if (params.end_date) query.end_date = params.end_date

      const { data } = await apiClient.get<AppLogListResponse>(
        '/api/v1/admin/request-logs/app-logs',
        { params: query },
      )
      return data
    },
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })
}
