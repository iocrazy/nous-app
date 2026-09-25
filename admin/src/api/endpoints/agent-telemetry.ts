import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'
import type { Schema } from '../../types/api'

export type AgentTelemetryOverview = Schema<'AdminTelemetryOverview'>
export type TopAgentEntry = Schema<'AdminTelemetryTopAgent'>
export type TopUserEntry = Schema<'AdminTelemetryTopUser'>
export type DailyTrendPoint = Schema<'AdminTelemetryDailyPoint'>
export type FailureModeEntry = Schema<'AdminTelemetryFailureMode'>
export type AgentTelemetrySnapshot = Schema<'AdminAgentTelemetry'>

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
