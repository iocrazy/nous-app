import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface UserData {
  id: string
  email: string | null
  username: string | null
  avatar_url: string | null
  role: string
  is_banned: boolean
  created_at: string
  updated_at: string | null
  last_sign_in_at: string | null
  video_count: number
  team_count: number
}

interface UserListParams {
  page?: number
  pageSize?: number
  search?: string
  role?: string
}

interface UserListResponse {
  items: UserData[]
  total: number
}

export function useUsers(params: UserListParams = {}) {
  const { page = 1, pageSize = 20, search, role } = params
  return useQuery({
    queryKey: ['users', { page, pageSize, search, role }],
    queryFn: async () => {
      const { data } = await apiClient.get<UserListResponse>('/api/v1/admin/users', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(role && { role }),
        },
      })
      return data
    },
  })
}

export function useUpdateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, values }: { id: string; values: Record<string, unknown> }) => {
      const { data } = await apiClient.patch(`/api/v1/admin/users/${id}`, values)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}

export function useDeleteUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/v1/admin/users/${id}`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
}
