import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface SystemSetting {
  key: string
  value: any
  description: string | null
  updated_at: string
  updated_by: string | null
}

export function useSystemSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: async () => {
      const { data } = await apiClient.get<SystemSetting[]>('/api/v1/admin/settings')
      return data
    },
  })
}

export function useUpdateSetting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ key, value }: { key: string; value: any }) => {
      const { data } = await apiClient.patch<SystemSetting>(
        `/api/v1/admin/settings/${key}`,
        { value },
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

// --- Graph memory (Graphiti) config — dedicated bundle endpoint. API keys are
// masked in the GET response (only *_set booleans); the PUT writes only the
// fields sent, and key fields only when a value is provided.

export interface GraphMemorySettings {
  enabled: boolean
  falkordb_host: string
  falkordb_port: string
  falkordb_database: string
  extractor_base_url: string
  extractor_model: string
  extractor_structured_output_mode: string
  extractor_api_key_set: boolean
  embedder_base_url: string
  embedder_model: string
  embedder_api_key_set: boolean
}

export interface GraphMemorySettingsUpdate {
  enabled?: boolean
  falkordb_host?: string
  falkordb_port?: string
  falkordb_database?: string
  extractor_base_url?: string
  extractor_api_key?: string
  extractor_model?: string
  extractor_structured_output_mode?: string
  embedder_base_url?: string
  embedder_api_key?: string
  embedder_model?: string
}

const GRAPH_MEMORY_URL = '/api/v1/admin/settings/graph-memory'

export function useGraphMemorySettings() {
  return useQuery({
    queryKey: ['settings', 'graph-memory'],
    queryFn: async () => {
      const { data } = await apiClient.get<GraphMemorySettings>(GRAPH_MEMORY_URL)
      return data
    },
  })
}

export function useUpdateGraphMemorySettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (update: GraphMemorySettingsUpdate) => {
      const { data } = await apiClient.put<GraphMemorySettings>(GRAPH_MEMORY_URL, update)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'graph-memory'] })
    },
  })
}
