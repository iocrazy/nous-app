import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'

export interface CeleryWorker {
  name: string
  status: string
  active: number
  processed: number
  concurrency: number | null
  uptime: number | null
}

export interface CeleryWorkersResponse {
  online: number
  total: number
  workers: CeleryWorker[]
  error?: string
}

export interface CeleryQueue {
  name: string
  messages: number
}

export interface CeleryQueuesResponse {
  queues: CeleryQueue[]
}

export function useCeleryWorkers() {
  return useQuery({
    queryKey: ['celery', 'workers'],
    queryFn: async () => {
      const { data } = await apiClient.get<CeleryWorkersResponse>('/api/v1/admin/celery/workers')
      return data
    },
    refetchInterval: 15000,
  })
}

export function useCeleryQueues() {
  return useQuery({
    queryKey: ['celery', 'queues'],
    queryFn: async () => {
      const { data } = await apiClient.get<CeleryQueuesResponse>('/api/v1/admin/celery/queues')
      return data
    },
    refetchInterval: 10000,
  })
}
