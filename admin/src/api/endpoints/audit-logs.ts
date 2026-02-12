import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface AuditLog {
  id: string
  admin_id: string
  admin_email: string | null
  action: string
  target_type: string
  target_id: string
  details: Record<string, unknown> | null
  ip_address: string | null
  created_at: string
}

interface AuditLogsResponse {
  data: AuditLog[]
  total: number
}

interface AuditLogsParams {
  page: number
  pageSize: number
  action?: string
  target_type?: string
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}

export function useAuditLogs(params: AuditLogsParams) {
  return useQuery({
    queryKey: ['admin', 'audit-logs', params],
    queryFn: async () => {
      const query: Record<string, string | number> = {
        page: params.page,
        pageSize: params.pageSize,
        sort_by: params.sort_by ?? 'created_at',
        sort_order: params.sort_order ?? 'desc',
      }
      if (params.action) query.action = params.action
      if (params.target_type) query.target_type = params.target_type

      const { data } = await apiClient.get<AuditLogsResponse>(
        '/api/v1/admin/audit-logs',
        { params: query },
      )
      return data
    },
    placeholderData: keepPreviousData,
  })
}
