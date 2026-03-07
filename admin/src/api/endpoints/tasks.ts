import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface AdminTaskData {
  id: string
  user_id: string
  user_email: string | null
  task_type: string
  status: string
  phase: string | null
  title: string
  subtitle: string | null
  progress: number
  speed: number | null
  total_bytes: number | null
  error_msg: string | null
  error_code: string | null
  resource_id: string | null
  media_id: string | null
  celery_task_id: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

interface TaskListParams {
  page?: number
  pageSize?: number
  status?: string
  taskType?: string
  search?: string
  sortBy?: string
  sortOrder?: string
}

interface TaskListResponse {
  items: AdminTaskData[]
  total: number
  page: number
  page_size: number
}

export interface TaskStatsData {
  total: number
  pending: number
  processing: number
  completed: number
  failed: number
  cancelled: number
}

export function useAdminTaskStats() {
  return useQuery({
    queryKey: ['admin-tasks', 'stats'],
    queryFn: async () => {
      const { data } = await apiClient.get<TaskStatsData>('/api/v1/admin/tasks/stats')
      return data
    },
  })
}

export function useAdminTasks(params: TaskListParams = {}, autoRefresh = false) {
  const { page = 1, pageSize = 20, status, taskType, search, sortBy, sortOrder } = params
  return useQuery({
    queryKey: ['admin-tasks', { page, pageSize, status, taskType, search, sortBy, sortOrder }],
    queryFn: async () => {
      const { data } = await apiClient.get<TaskListResponse>('/api/v1/admin/tasks', {
        params: {
          page,
          page_size: pageSize,
          ...(status && { status }),
          ...(taskType && { task_type: taskType }),
          ...(search && { search }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return data
    },
    refetchInterval: autoRefresh ? 5000 : false,
  })
}

export function useCancelTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (taskId: string) => {
      const { data } = await apiClient.post(`/api/v1/admin/tasks/${taskId}/cancel`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-tasks'] })
    },
  })
}

export function useRetryTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (taskId: string) => {
      const { data } = await apiClient.post(`/api/v1/admin/tasks/${taskId}/retry`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-tasks'] })
    },
  })
}
