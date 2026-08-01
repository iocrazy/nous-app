import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

const BASE = '/api/v1/admin/storage'

export interface StorageStats {
  videos: { count: number; size_bytes: number }
  fs_residue: number
  orphans: number
  hls_ready: number
  broken: number | null
  last_scan: { at: string; scanned: number; missing: number; errors: number } | null
}

export interface MediaStorageRow {
  media_id: string
  video_key: string | null
  video_size: number | null
  cover_ok: boolean
  thumbnail_ok: boolean
  hls_ok: boolean
  storage_status: 'ok' | 'no_video' | 'fs_residue'
  scope_id: string | null
}

export interface StorageAsset {
  kind: 'video' | 'cover' | 'thumbnail' | 'sprite' | 'hls'
  key: string | null
  size_bytes: number | null
  present_in_db: boolean
}

export interface AuditMissing {
  key: string
  kind: string
  media_id: string | null
  resource_id: string | null
}

export interface AuditResult {
  status: 'none' | 'queued' | 'in_progress' | 'completed' | 'failed'
  scanned: number
  errors: number
  scanned_at: string | null
  missing: AuditMissing[]
  missing_truncated?: boolean
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
    refetchInterval: (q) =>
      q.state.data?.status === 'queued' || q.state.data?.status === 'in_progress'
        ? 4000
        : false,
  })
}

export function useMediaStatus(ids: string[]) {
  return useQuery({
    queryKey: ['storage-media-status', ids.join(',')],
    enabled: ids.length > 0,
    queryFn: async () =>
      (
        await apiClient.get<{ rows: MediaStorageRow[] }>(`${BASE}/media-status`, {
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
        await apiClient.get<{ assets: StorageAsset[]; scope_id: string | null }>(
          `${BASE}/media/${id}/detail`,
        )
      ).data,
  })
}

export function useVerifyMedia() {
  return useMutation({
    mutationFn: async (id: number) =>
      (
        await apiClient.post<{
          results: { kind: string; key: string; exists: boolean | null }[]
        }>(`${BASE}/media/${id}/verify`)
      ).data.results,
  })
}

export function useDeepVerify() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (
        await apiClient.post<{ workflow_id: string; already_running: boolean }>(
          `${BASE}/verify`,
        )
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['storage-audit'] })
    },
  })
}
