import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'
import type { TableFilter, TableSort } from '../../components/notion-table/types'

interface TablePreference {
  table_key: string
  filters: TableFilter[]
  sorts: TableSort[]
  visible_columns: string[] | null
  column_order: string[] | null
}

interface TablePreferenceUpdate {
  filters: TableFilter[]
  sorts: TableSort[]
  visible_columns: string[] | null
  column_order: string[] | null
}

export function useTablePreference(tableKey: string) {
  return useQuery({
    queryKey: ['table-preferences', tableKey],
    queryFn: async () => {
      const { data } = await apiClient.get<TablePreference>(
        `/api/v1/admin/table-preferences/${tableKey}`,
      )
      return data
    },
    staleTime: Infinity,
    retry: 1,
  })
}

export function useUpdateTablePreference(tableKey: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: TablePreferenceUpdate) => {
      const { data } = await apiClient.put<TablePreference>(
        `/api/v1/admin/table-preferences/${tableKey}`,
        body,
      )
      return data
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['table-preferences', tableKey], data)
    },
  })
}

export function useResetTablePreference(tableKey: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      await apiClient.delete(`/api/v1/admin/table-preferences/${tableKey}`)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['table-preferences', tableKey] })
    },
  })
}
