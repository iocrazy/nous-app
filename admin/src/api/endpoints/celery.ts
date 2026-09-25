import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../client'
import type { Schema } from '../../types/api'

// DBOS-backed since Celery was removed; the route names stayed.
export type CeleryWorker = Schema<'AdminWorkerInfo'>
export type CeleryWorkersResponse = Schema<'AdminWorkersResponse'>
export type CeleryQueue = Schema<'AdminQueueDepth'>
export type CeleryQueuesResponse = Schema<'AdminQueuesResponse'>

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
