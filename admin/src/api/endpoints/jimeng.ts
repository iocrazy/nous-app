import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

// Jimeng (即梦 / dreamina) CLI auth — device-flow login state managed from the
// admin panel (backend: /api/v1/admin/jimeng/*). The login process runs in the
// gateway container and the token lands on the shared volume the worker mounts.

export interface JimengStatus {
  logged_in: boolean
  credit?: number
  error?: string
}

export interface JimengLoginMaterial {
  status: 'pending' | 'already'
  verification_uri?: string
  user_code?: string
  device_code?: string
  expires_at?: string
}

/** Poll login state. Pass a refetchInterval (ms) to poll, or false/undefined for
 * a one-shot fetch — the panel polls fast while the login modal is open. */
export function useJimengStatus(refetchInterval?: number | false) {
  return useQuery({
    queryKey: ['jimeng-status'],
    queryFn: async () => {
      const { data } = await apiClient.get<JimengStatus>('/api/v1/admin/jimeng/status')
      return data
    },
    refetchInterval,
  })
}

export function useJimengLogin() {
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<JimengLoginMaterial>(
        '/api/v1/admin/jimeng/login',
      )
      return data
    },
  })
}

export function useJimengLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post('/api/v1/admin/jimeng/logout')
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jimeng-status'] })
    },
  })
}
