import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface Team {
  id: string
  name: string
  owner_id: string
  owner_email: string | null
  owner_username: string | null
  invite_code: string
  is_personal: boolean
  member_count: number
  points_balance: number
  enabled_modules: string[]
  created_at: string
}

export interface ModuleDefinition {
  key: string
  name: string
  description: string
  enabled: boolean
}

export interface TeamModulesResponse {
  team_id: string
  modules: ModuleDefinition[]
}

/** Display name for a team: personal teams show "{Owner}'s Workspace" */
export function getTeamDisplayName(team: Team): string {
  if (!team.is_personal) return team.name
  const ownerName = team.owner_username || team.owner_email?.split('@')[0] || ''
  if (!ownerName) return team.name
  const capitalized = ownerName.charAt(0).toUpperCase() + ownerName.slice(1)
  return `${capitalized}'s Workspace`
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

export const ROLE_COLOR_MAP: Record<string, string> = {
  owner: 'purple',
  admin: 'blue',
  editor: 'green',
  reviewer: 'orangered',
  viewer: '',
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
      const { data } = await apiClient.get<TeamMember[]>(
        `/api/v1/admin/teams/${teamId}/members`,
      )
      return data
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

export function useTeamModules(teamId: string | null) {
  return useQuery({
    queryKey: ['teams', teamId, 'modules'],
    queryFn: async () => {
      const { data } = await apiClient.get<TeamModulesResponse>(
        `/api/v1/admin/teams/${teamId}/modules`,
      )
      return data
    },
    enabled: !!teamId,
  })
}

export function useUpdateTeamModules() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ teamId, enabledModules }: { teamId: string; enabledModules: string[] }) => {
      const { data } = await apiClient.patch(
        `/api/v1/admin/teams/${teamId}/modules`,
        { enabled_modules: enabledModules },
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] })
    },
  })
}
