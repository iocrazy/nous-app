import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

// One agent_runs row as surfaced to the admin AI Usage page.
export interface AiUsageRow {
  id: string
  user_id: string | null
  user_email: string | null
  agent_id: string | null
  model: string | null
  provider: string | null
  status: string
  trigger: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_cents: number
  duration_ms: number | null
  started_at: string | null
  ended_at: string | null
  error_code: string | null
}

// One day × model bucket for the usage charts.
export interface AiUsageDailyRow {
  date: string
  model: string | null
  provider: string | null
  requests: number
  total_tokens: number
  cost_cents: number
}

export interface AiUsageSummary {
  days: number
  total_requests: number
  total_tokens: number
  total_cost_cents: number
  daily: AiUsageDailyRow[]
}

// Daily × model rollup powering the DeepSeek-console-style charts.
export function useAiUsageSummary(days = 30) {
  return useQuery({
    queryKey: ['ai-usage', 'summary', days],
    queryFn: async () => {
      const { data } = await apiClient.get<AiUsageSummary>(
        '/api/v1/admin/ai-usage/summary',
        { params: { days } },
      )
      return data
    },
    refetchInterval: 60000,
  })
}

// Distinct model names present in agent_runs — populates the filter dropdown.
export function useAiUsageModels() {
  return useQuery({
    queryKey: ['ai-usage', 'models'],
    queryFn: async () => {
      const { data } = await apiClient.get<string[]>(
        '/api/v1/admin/ai-usage/models',
      )
      return data
    },
    staleTime: 5 * 60 * 1000,
  })
}
