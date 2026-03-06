import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface TranscodeVersionData {
  id: string
  resource_id: string
  version_number: number
  filename: string | null
  file_size_bytes: number
  mime_type: string | null
  transcode_status: string | null
  hls_path: string | null
  transcode_at: string | null
  created_at: string | null
  video_title: string | null
  cover_url: string | null
  author: string | null
}

interface TranscodeListParams {
  page?: number
  pageSize?: number
  status?: string
  minSizeMb?: number
  search?: string
  sortBy?: string
  sortOrder?: string
}

interface TranscodeListResponse {
  items: TranscodeVersionData[]
  total: number
  page: number
  page_size: number
}

export interface TranscodeStatsData {
  total_video_versions: number
  completed: number
  processing: number
  failed: number
  pending: number
  not_transcoded: number
}

export function useTranscodeStats() {
  return useQuery({
    queryKey: ['transcode', 'stats'],
    queryFn: async () => {
      const { data } = await apiClient.get<TranscodeStatsData>('/api/v1/admin/transcode/stats')
      return data
    },
  })
}

export function useTranscodeList(params: TranscodeListParams = {}) {
  const { page = 1, pageSize = 20, status, minSizeMb, search, sortBy, sortOrder } = params
  return useQuery({
    queryKey: ['transcode', { page, pageSize, status, minSizeMb, search, sortBy, sortOrder }],
    queryFn: async () => {
      const { data } = await apiClient.get<TranscodeListResponse>('/api/v1/admin/transcode', {
        params: {
          page,
          page_size: pageSize,
          ...(status && { status }),
          ...(minSizeMb && { min_size_mb: minSizeMb }),
          ...(search && { search }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return data
    },
  })
}

export function useRetryTranscode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (versionId: string) => {
      const { data } = await apiClient.post(`/api/v1/admin/transcode/${versionId}/retry`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcode'] })
    },
  })
}

export function useBatchTranscode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (action: 'retry_failed' | 'transcode_new') => {
      const { data } = await apiClient.post('/api/v1/admin/transcode/batch', null, {
        params: { action },
      })
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcode'] })
    },
  })
}
