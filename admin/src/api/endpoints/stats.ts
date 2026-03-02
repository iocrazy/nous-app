import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

interface Stats {
  total_users: number
  total_videos: number
  total_teams: number
  total_downloads: number
  active_users_today: number
  new_users_today: number
  new_videos_today: number
}

export function useStats() {
  return useQuery({
    queryKey: ['admin', 'stats', 'overview'],
    queryFn: async () => {
      const { data } = await apiClient.get<Stats>('/api/v1/admin/stats/overview')
      return data
    },
  })
}
