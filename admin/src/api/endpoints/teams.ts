import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface Team {
  id: string
  name: string
  owner_id: string
  owner_email: string | null
  invite_code: string
  member_count: number
  video_count: number
  created_at: string
}

export interface TeamMember {
  user_id: string
  email: string
  username: string | null
  role: string
  joined_at: string
}

interface TeamListParams {
  page?: number
  pageSize?: number
  search?: string
}

interface TeamListResponse {
  items: Team[]
  total: number
}

export function useTeams(params: TeamListParams = {}) {
  const { page = 1, pageSize = 20, search } = params
  return useQuery({
    queryKey: ['teams', { page, pageSize, search }],
    queryFn: async () => {
      const { data } = await apiClient.get<TeamListResponse>('/api/v1/admin/teams', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
        },
      })
      return data
    },
  })
}

export function useTeamMembers(teamId: string | null) {
  return useQuery({
    queryKey: ['teams', teamId, 'members'],
    queryFn: async () => {
      const { data } = await apiClient.get<{ items: TeamMember[] }>(
        `/api/v1/admin/teams/${teamId}/members`,
      )
      return data.items
    },
    enabled: !!teamId,
  })
}

export function useDeleteTeam() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/v1/admin/teams/${id}`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] })
    },
  })
}
