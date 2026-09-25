import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'
import type { Schema } from '../../types/api'

export type TranscodeVersionData = Schema<'AdminTranscodeVersionResponse'>
type TranscodeListResponse = Schema<'AdminTranscodeListResponse'>
export type TranscodeStatsData = Schema<'AdminTranscodeStatsResponse'>
export type TranscodeRetryResult = Schema<'AdminTranscodeRetryResponse'>
export type TranscodeBatchResult = Schema<'AdminTranscodeBatchResponse'>

interface TranscodeListParams {
  page?: number
  pageSize?: number
  status?: string
  minSizeMb?: number
  search?: string
  sortBy?: string
  sortOrder?: string
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
      const { data } = await apiClient.post<TranscodeRetryResult>(
        `/api/v1/admin/transcode/${versionId}/retry`,
      )
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
      const { data } = await apiClient.post<TranscodeBatchResult>('/api/v1/admin/transcode/batch', null, {
        params: { action },
      })
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcode'] })
    },
  })
}

// ============================================
// Transcode Settings
// ============================================

export interface TranscodeSettings {
  transcode_enabled: boolean
  transcode_tiers: string
  ffmpeg_encoder: string
  ffmpeg_preset: string
  transcode_parallel_tiers: boolean
  transcode_min_size_mb: number
}

export function useTranscodeSettings() {
  return useQuery({
    queryKey: ['transcode', 'settings'],
    queryFn: async () => {
      const { data } = await apiClient.get<TranscodeSettings>('/api/v1/admin/transcode/settings')
      return data
    },
  })
}

export function useUpdateTranscodeSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (data: Partial<TranscodeSettings>) => {
      const { data: res } = await apiClient.put<TranscodeSettings>(
        '/api/v1/admin/transcode/settings',
        data,
      )
      return res
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcode', 'settings'] })
    },
  })
}
