import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

// --- Types ---

export interface TagGroup {
  id: string
  name: string
  sort_order: number
  tag_count: number
  created_at: string
}

export interface TagData {
  id: string
  name: string
  name_zh: string | null
  color: string | null
  icon: string | null
  type: string
  group_id: string | null
  group_name: string | null
  sort_order: number
  usage_count: number
  created_at: string
}

interface GroupsResponse {
  success: boolean
  groups: TagGroup[]
  total_tags: number
  uncategorized_count: number
}

// --- Tag Groups ---

export function useTagGroups() {
  return useQuery({
    queryKey: ['admin-tag-groups'],
    queryFn: async () => {
      const { data } = await apiClient.get<GroupsResponse>('/api/v1/admin/tags/groups')
      return data
    },
  })
}

export function useCreateGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (name: string) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/groups', { name })
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tag-groups'] }),
  })
}

export function useUpdateGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, values }: { id: string; values: { name?: string; sort_order?: number } }) => {
      const { data } = await apiClient.patch(`/api/v1/admin/tags/groups/${id}`, values)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tag-groups'] }),
  })
}

export function useDeleteGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/v1/admin/tags/groups/${id}`)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
    },
  })
}

export function useReorderGroups() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (ids: string[]) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/groups/reorder', { ids })
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tag-groups'] }),
  })
}

// --- Tags ---

export function useCreateTag() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (values: {
      name: string
      name_zh?: string
      color?: string
      icon?: string
      group_id?: string
    }) => {
      const { data } = await apiClient.post('/api/v1/admin/tags', values)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useUpdateTag() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, values }: { id: string; values: Record<string, unknown> }) => {
      const { data } = await apiClient.patch(`/api/v1/admin/tags/${id}`, values)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useDeleteTag() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/v1/admin/tags/${id}`)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useBatchTagAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: {
      action: 'move' | 'delete' | 'color'
      tag_ids: string[]
      group_id?: string
      color?: string
    }) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/batch', body)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-tags'] })
      qc.invalidateQueries({ queryKey: ['admin-tag-groups'] })
    },
  })
}

export function useReorderTags() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { group_id?: string; tag_ids: string[] }) => {
      const { data } = await apiClient.post('/api/v1/admin/tags/reorder', body)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-tags'] }),
  })
}
