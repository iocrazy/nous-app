import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { apiClient } from '../client'

// ============================================
// Types
// ============================================

export interface AlertRule {
  id: string
  name: string
  metric_type: string
  condition: string
  threshold: number
  window_minutes: number
  notification_channel: string
  is_active: boolean
  is_muted: boolean
  mute_until: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

interface AlertRuleListResponse {
  data: AlertRule[]
  total: number
}

export interface AlertRuleCreate {
  name: string
  metric_type: string
  condition: string
  threshold: number
  window_minutes?: number
  notification_channel?: string
}

export interface AlertRuleUpdate {
  name?: string
  metric_type?: string
  condition?: string
  threshold?: number
  window_minutes?: number
  notification_channel?: string
  is_active?: boolean
  is_muted?: boolean
  mute_until?: string | null
}

export interface AlertHistory {
  id: string
  rule_id: string
  rule_name: string
  metric_type: string
  metric_value: number
  threshold: number
  condition: string
  message: string
  notified: boolean
  resolved: boolean
  resolved_at: string | null
  created_at: string
}

interface AlertHistoryListResponse {
  data: AlertHistory[]
  total: number
}

interface AlertCheckResult {
  alerts_triggered: number
  details: Array<{
    rule_name: string
    metric_type: string
    metric_value: number
    threshold: number
    message: string
  }>
}

// ============================================
// Hooks
// ============================================

export function useAlertRules() {
  return useQuery({
    queryKey: ['admin', 'alert-rules'],
    queryFn: async () => {
      const { data } = await apiClient.get<AlertRuleListResponse>(
        '/api/v1/admin/alerts/rules',
      )
      return data
    },
  })
}

export function useCreateAlertRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: AlertRuleCreate) => {
      const { data } = await apiClient.post<AlertRule>(
        '/api/v1/admin/alerts/rules',
        body,
      )
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'alert-rules'] }),
  })
}

export function useUpdateAlertRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, ...body }: AlertRuleUpdate & { id: string }) => {
      const { data } = await apiClient.patch<AlertRule>(
        `/api/v1/admin/alerts/rules/${id}`,
        body,
      )
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'alert-rules'] }),
  })
}

export function useDeleteAlertRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/admin/alerts/rules/${id}`)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'alert-rules'] }),
  })
}

export function useMuteAlertRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, duration_minutes }: { id: string; duration_minutes: number }) => {
      await apiClient.post(
        `/api/v1/admin/alerts/rules/${id}/mute`,
        null,
        { params: { duration_minutes } },
      )
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'alert-rules'] }),
  })
}

export function useUnmuteAlertRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.post(`/api/v1/admin/alerts/rules/${id}/unmute`)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'alert-rules'] }),
  })
}

interface AlertHistoryParams {
  page: number
  pageSize: number
  rule_id?: string
  resolved?: boolean
  start_date?: string
  end_date?: string
}

export function useAlertHistory(params: AlertHistoryParams) {
  return useQuery({
    queryKey: ['admin', 'alert-history', params],
    queryFn: async () => {
      const query: Record<string, string | number | boolean> = {
        page: params.page,
        pageSize: params.pageSize,
      }
      if (params.rule_id) query.rule_id = params.rule_id
      if (params.resolved !== undefined) query.resolved = params.resolved
      if (params.start_date) query.start_date = params.start_date
      if (params.end_date) query.end_date = params.end_date

      const { data } = await apiClient.get<AlertHistoryListResponse>(
        '/api/v1/admin/alerts/history',
        { params: query },
      )
      return data
    },
    placeholderData: keepPreviousData,
  })
}

export function useResolveAlert() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (alertId: string) => {
      await apiClient.post(`/api/v1/admin/alerts/history/${alertId}/resolve`)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'alert-history'] }),
  })
}

export function useCheckAlerts() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<AlertCheckResult>(
        '/api/v1/admin/alerts/check',
      )
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'alert-history'] }),
  })
}
