import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

// Codex (GPT Image 2) CLI auth — STATUS ONLY. Unlike jimeng there is no
// in-panel login: the Codex OAuth session is created on the host
// (`codex login`, browser flow) and bind-mounted into the containers, so
// re-auth happens on the host too (docs/runbook/codex-image.md). This
// endpoint just makes the session state visible without a shell.

export interface CodexStatus {
  logged_in: boolean
  error?: string
}

export function useCodexStatus(refetchInterval?: number | false) {
  return useQuery({
    queryKey: ['codex-status'],
    queryFn: async () => {
      const { data } = await apiClient.get<CodexStatus>('/api/v1/admin/codex/status')
      return data
    },
    refetchInterval,
  })
}
