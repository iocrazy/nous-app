import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface AgentTelemetryOverview {
  total_runs: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_cost_cents: number
  unique_agents: number
  unique_users: number
}

export interface TopAgentEntry {
  agent_id: string
  run_count: number
  cost_cents: number
  total_tokens: number
}

export interface TopUserEntry {
  user_id: string
  run_count: number
  cost_cents: number
  total_tokens: number
}

export interface DailyTrendPoint {
  date: string
  runs: number
  cost_cents: number
  prompt_tokens: number
  completion_tokens: number
}

export interface FailureModeEntry {
  error_code: string
  count: number
}

export interface AgentTelemetrySnapshot {
  window_days: number
  window_start: string
  window_end: string
  overview: AgentTelemetryOverview
  status_breakdown: Record<string, number>
  top_agents: TopAgentEntry[]
  top_users: TopUserEntry[]
  daily_trend: DailyTrendPoint[]
  failure_modes: FailureModeEntry[]
}

export function useAgentTelemetry(days: number) {
  return useQuery({
    queryKey: ['agent-telemetry', days],
    queryFn: async () => {
      const { data } = await apiClient.get<AgentTelemetrySnapshot>(
        `/api/v1/ai-library/admin/telemetry?days=${days}`,
      )
      return data
    },
    refetchInterval: 60_000,
  })
}
