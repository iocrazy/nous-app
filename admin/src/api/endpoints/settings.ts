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
  embedder_dimensions: number
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
  embedder_dimensions?: number
}

// --- AI Config Governance — per-module user-config toggles + admin platform
// config. API keys are write-only (GET returns only *_api_key_set booleans;
// keys never leave the backend). PUT writes api_key only when non-blank.

export interface AIGovernanceChatSettings {
  user_allowed: boolean
  nous_allowed: boolean
}

export interface AIGovernanceTaskSettings {
  user_allowed: boolean
  nous_allowed: boolean
  base_url: string
  model: string
  api_key_set: boolean
}

export interface AIGovernanceSettings {
  nous_user_enabled: boolean
  chat: AIGovernanceChatSettings
  transcription: AIGovernanceTaskSettings
  translation: AIGovernanceTaskSettings
  visual_analysis: AIGovernanceTaskSettings
  caption: AIGovernanceTaskSettings
  classification: AIGovernanceTaskSettings
  summarization: AIGovernanceTaskSettings
  topic_scorer: AIGovernanceTaskSettings
  embedding: AIGovernanceTaskSettings
}

export interface AIGovernanceModuleUpdate {
  user_allowed: boolean
  nous_allowed?: boolean
  base_url?: string
  model?: string
  /** Only send when non-blank — blank keeps the stored key unchanged. */
  api_key?: string
}

export interface AIGovernanceUpdate {
  nous_user_enabled?: boolean
  chat?: AIGovernanceModuleUpdate
  transcription?: AIGovernanceModuleUpdate
  translation?: AIGovernanceModuleUpdate
  visual_analysis?: AIGovernanceModuleUpdate
  caption?: AIGovernanceModuleUpdate
  classification?: AIGovernanceModuleUpdate
  summarization?: AIGovernanceModuleUpdate
  topic_scorer?: AIGovernanceModuleUpdate
  embedding?: AIGovernanceModuleUpdate
}

const AI_GOVERNANCE_URL = '/api/v1/admin/settings/ai-governance'

export function useAIGovernanceSettings() {
  return useQuery({
    queryKey: ['settings', 'ai-governance'],
    queryFn: async () => {
      const { data } = await apiClient.get<AIGovernanceSettings>(AI_GOVERNANCE_URL)
      return data
    },
  })
}

export function useUpdateAIGovernanceSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (update: AIGovernanceUpdate) => {
      const { data } = await apiClient.put<AIGovernanceSettings>(AI_GOVERNANCE_URL, update)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'ai-governance'] })
    },
  })
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

// ── Memory Control Plane — provider slot routing + health + hot-reload ────

const MEMORY_CONTROL_URL = '/api/v1/admin/settings/memory'

export interface MemorySlotStatus {
  slot: string
  provider: string
  health: boolean
}

export interface MemoryControl {
  slots: MemorySlotStatus[]
}

export function useMemoryControl() {
  return useQuery({
    queryKey: ['settings', 'memory-control'],
    queryFn: async () => {
      const { data } = await apiClient.get<MemoryControl>(`${MEMORY_CONTROL_URL}/control`)
      return data
    },
  })
}

export function useSetMemorySlot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { slot: string; provider: string }) => {
      const { data } = await apiClient.put<MemoryControl>(`${MEMORY_CONTROL_URL}/slot`, vars)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'memory-control'] }),
  })
}

export function useReloadMemorySlot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (slot: string) => {
      const { data } = await apiClient.post<{ ok: boolean; reloaded: string | null }>(
        `${MEMORY_CONTROL_URL}/${slot}/reload`,
      )
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'memory-control'] }),
  })
}

// ── Topic Scoring config ──────────────────────────────────────────────────
export interface TopicScoringConfig {
  dim_weights: Record<string, number>
  tier_weights: Record<string, number>
  featured_min_score: number
}

const TOPIC_SCORING_URL = '/api/v1/admin/settings/topics-scoring'

export function useTopicScoringConfig() {
  return useQuery({
    queryKey: ['settings', 'topics-scoring'],
    queryFn: async () => {
      const { data } = await apiClient.get<TopicScoringConfig>(TOPIC_SCORING_URL)
      return data
    },
  })
}

export function useUpdateTopicScoringConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (update: TopicScoringConfig) => {
      const { data } = await apiClient.put<TopicScoringConfig>(TOPIC_SCORING_URL, update)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'topics-scoring'] })
    },
  })
}

// ── Signal Sources (admin global enable/disable + tier) ───────────────────
export interface AdminSource {
  id: string
  name: string
  kind: string
  category: string | null
  enabled: boolean
  tier: number
  health: string
  consecutive_failures: number
  last_error: string | null
  last_ok_at: string | null
  is_system: boolean
}

const ADMIN_SOURCES_URL = '/api/v1/admin/topics/sources'

export function useAdminSources() {
  return useQuery({
    queryKey: ['admin', 'signal-sources'],
    queryFn: async () => {
      const { data } = await apiClient.get<{ count: number; sources: AdminSource[] }>(
        ADMIN_SOURCES_URL,
      )
      return data.sources
    },
  })
}

export function useUpdateAdminSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      id,
      enabled,
      tier,
    }: {
      id: string
      enabled?: boolean
      tier?: number
    }) => {
      const { data } = await apiClient.patch<AdminSource>(`${ADMIN_SOURCES_URL}/${id}`, {
        enabled,
        tier,
      })
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'signal-sources'] })
    },
  })
}

export function useCreateAdminSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: {
      kind: string
      name: string
      config: Record<string, unknown>
      category?: string | null
      tier?: number
    }) => {
      const { data } = await apiClient.post<AdminSource>(ADMIN_SOURCES_URL, body)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'signal-sources'] })
    },
  })
}

// ── Honcho (L2) connection ─────────────────────────────────────────────────

const HONCHO_CONN_URL = '/api/v1/admin/settings/memory/honcho-connection'

export interface HonchoConnection {
  enabled: boolean
  base_url: string
  workspace_id: string
}

export function useHonchoConnection() {
  return useQuery({
    queryKey: ['settings', 'honcho-connection'],
    queryFn: async () => {
      const { data } = await apiClient.get<HonchoConnection>(HONCHO_CONN_URL)
      return data
    },
  })
}

export function useUpdateHonchoConnection() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (update: Partial<HonchoConnection>) => {
      const { data } = await apiClient.put<HonchoConnection>(HONCHO_CONN_URL, update)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings', 'honcho-connection'] })
      qc.invalidateQueries({ queryKey: ['settings', 'memory-control'] })
    },
  })
}
