import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface VideoData {
  id: number
  aweme_id: string
  user_id: string | null
  user_email: string | null
  video_title: string | null
  video_desc: string | null
  author: string | null
  aweme_type: string | null
  video_download_status: string
  cover_url: string | null
  cover_download_path: string | null
  source_platform: string | null
  video_duration: string | null
  video_datasize: string | null
  video_datasize_bytes: number
  video_digg_count: number
  video_comment_count: number
  video_share_count: number
  error_message: string | null
  download_time: string | null
  created_at: string
  updated_at: string | null
}

export interface VideoDetailData extends VideoData {
  video_original_url: string | null
  video_download_path: string | null
  cover_download_path: string | null
  music_name: string | null
  music_download_status: string
  cover_download_status: string
  video_download_urls: string[] | null
  video_hashtag_name: string | null
  video_collect_count: number
  view_count: number
  storage_size: number | null
  keep_forever: boolean
}

interface VideoListParams {
  page?: number
  pageSize?: number
  search?: string
  status?: string
  platform?: string
  userId?: string
  sortBy?: string
  sortOrder?: string
}

interface VideoListResponse {
  items: VideoData[]
  total: number
}

export interface VideoStatsData {
  total: number
  completed: number
  pending: number
  failed: number
  downloading: number
  skipped: number
  total_storage_bytes: number
}

export function useVideos(params: VideoListParams = {}) {
  const { page = 1, pageSize = 20, search, status, platform, userId, sortBy, sortOrder } = params
  return useQuery({
    queryKey: ['videos', { page, pageSize, search, status, platform, userId, sortBy, sortOrder }],
    queryFn: async () => {
      const { data } = await apiClient.get<VideoListResponse>('/api/v1/admin/videos', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(status && { status }),
          ...(platform && { platform }),
          ...(userId && { user_id: userId }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return data
    },
  })
}

export function useVideoDetail(id: number | null) {
  return useQuery({
    queryKey: ['videos', 'detail', id],
    queryFn: async () => {
      const { data } = await apiClient.get<VideoDetailData>(`/api/v1/admin/videos/${id}`)
      return data
    },
    enabled: id !== null,
  })
}

export function useVideoStats() {
  return useQuery({
    queryKey: ['videos', 'stats'],
    queryFn: async () => {
      const { data } = await apiClient.get<VideoStatsData>('/api/v1/admin/videos/stats')
      return data
    },
  })
}

export function useDeleteVideo() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      const { data } = await apiClient.delete(`/api/v1/admin/videos/${id}`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
  })
}

export function useRetryVideo() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      const { data } = await apiClient.post(`/api/v1/admin/videos/${id}/retry`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videos'] })
    },
  })
}
