import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'
import type { Schema } from '../../types/api'

const BASE = '/api/v1/admin/storage'

export type StorageStats = Schema<'AdminStorageStats'>
export type MediaStorageRow = Schema<'AdminStorageMediaStatusRow'>
export type MediaStorageDetail = Schema<'AdminStorageMediaDetail'>
export type StorageAsset = Schema<'AdminStorageAsset'>
export type DeepVerifyDispatch = Schema<'AdminStorageDeepVerifyDispatch'>
export type AuditMissing = Schema<'AdminStorageAuditMissing'>
/** `status` is the run's task_tracking phase, or `none` before any scan. */
export type AuditResult = Schema<'AdminStorageAudit'>

// task_tracking phases that are not terminal: the backend's ACTIVE_PHASES
// (unified_task_manager.py). A running audit sits at `processing` almost the
// whole time; `in_progress` is only the trigger's brief mirror value.
const AUDIT_ACTIVE_PHASES = new Set(['queued', 'dedup_check', 'processing', 'in_progress'])

export function isAuditRunning(status: string | undefined): boolean {
  return status != null && AUDIT_ACTIVE_PHASES.has(status)
}

export function useStorageStats() {
  return useQuery({
    queryKey: ['storage-stats'],
    queryFn: async () => (await apiClient.get<StorageStats>(`${BASE}/stats`)).data,
  })
}

export function useAudit() {
  return useQuery({
    queryKey: ['storage-audit'],
    queryFn: async () => (await apiClient.get<AuditResult>(`${BASE}/audit`)).data,
    refetchInterval: (q) => (isAuditRunning(q.state.data?.status) ? 4000 : false),
  })
}

export function useMediaStatus(ids: string[]) {
  return useQuery({
    queryKey: ['storage-media-status', ids.join(',')],
    enabled: ids.length > 0,
    queryFn: async () =>
      (
        await apiClient.get<Schema<'AdminStorageMediaStatusList'>>(`${BASE}/media-status`, {
          params: { media_ids: ids.join(',') },
        })
      ).data.rows,
  })
}

export function useMediaStorageDetail(id: number | null) {
  return useQuery({
    queryKey: ['storage-media-detail', id],
    enabled: id != null,
    queryFn: async () =>
      (
        await apiClient.get<MediaStorageDetail>(`${BASE}/media/${id}/detail`)
      ).data,
  })
}

export function useVerifyMedia() {
  return useMutation({
    mutationFn: async (id: number) =>
      (
        await apiClient.post<Schema<'AdminStorageMediaVerify'>>(`${BASE}/media/${id}/verify`)
      ).data.results,
  })
}

export function useDeepVerify() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (
        await apiClient.post<DeepVerifyDispatch>(`${BASE}/verify`)
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['storage-audit'] })
    },
  })
}
