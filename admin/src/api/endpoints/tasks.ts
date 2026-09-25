import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'
import type { Schema } from '../../types/api'

export type AdminTaskData = Schema<'AdminTaskResponse'>
type TaskListResponse = Schema<'AdminTaskListResponse'>
export type TaskStatsData = Schema<'AdminTaskStatsResponse'>
export type AdminTaskActionResult = Schema<'AdminTaskActionResponse'>

interface TaskListParams {
  page?: number
  pageSize?: number
  status?: string
  taskType?: string
  search?: string
  sortBy?: string
  sortOrder?: string
}

export function useAdminTaskStats() {
  return useQuery({
    queryKey: ['tasks', 'stats'],
    queryFn: async () => {
      const { data } = await apiClient.get<TaskStatsData>('/api/v1/admin/tasks/stats')
      return data
    },
  })
}

export function useAdminTasks(params: TaskListParams = {}) {
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
  })
}

export function useCancelTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (taskId: string) => {
      const { data } = await apiClient.post<AdminTaskActionResult>(
        `/api/v1/admin/tasks/${taskId}/cancel`,
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}
