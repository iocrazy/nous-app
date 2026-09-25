import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'
import type { Schema } from '../../types/api'

// ============================================
// Types
// ============================================

// Response shapes are the backend's Pydantic models (see src/types/api.ts).
export type CreditsStats = Schema<'AdminCreditsStatsResponse'>
export type RevenueChartItem = Schema<'AdminRevenueChartItem'>
export type ConsumptionChartItem = Schema<'AdminConsumptionChartItem'>
export type TopTeamItem = Schema<'AdminTopTeamItem'>
export type CreditTransaction = Schema<'AdminCreditTransactionResponse'>
export type CreditOrder = Schema<'AdminOrderResponse'>
export type TeamCreditsDetail = Schema<'AdminTeamCreditsDetailResponse'>
export type PointPackage = Schema<'AdminPointPackage'>
export type PricingRule = Schema<'AdminPointPricing'>
export type OrderActionResult = Schema<'AdminCreditsOrderActionResult'>
export type CreditsOkResult = Schema<'AdminCreditsOkResult'>
export type BatchGiftResult = Schema<'AdminBatchGiftResult'>
export type PointsAdjustResult = Schema<'AdminPointsAdjustResult'>

/** Request body for creating / updating a package (hand-written: requests are
 * not generated, see src/types/api.ts). */
export interface PointPackageInput {
  name: string
  description: string | null
  points_amount: number
  price_cents: number
  sort_order: number
  is_active: boolean
}

// ============================================
// Stats & Charts Hooks
// ============================================

export function useCreditsStats() {
  return useQuery({
    queryKey: ['credits', 'stats'],
    queryFn: async () => {
      const { data } = await apiClient.get<CreditsStats>('/api/v1/admin/credits/stats')
      return data
    },
    refetchInterval: 60000,
  })
}

export function useRevenueChart(period: string = 'day', days: number = 30) {
  return useQuery({
    queryKey: ['credits', 'revenue-chart', period, days],
    queryFn: async () => {
      const { data } = await apiClient.get<RevenueChartItem[]>(
        '/api/v1/admin/credits/revenue-chart',
        { params: { period, days } },
      )
      return data
    },
    refetchInterval: 60000,
  })
}

export function useConsumptionChart() {
  return useQuery({
    queryKey: ['credits', 'consumption-chart'],
    queryFn: async () => {
      const { data } = await apiClient.get<ConsumptionChartItem[]>(
        '/api/v1/admin/credits/consumption-chart',
      )
      return data
    },
    refetchInterval: 60000,
  })
}

export function useTopTeams(limit: number = 10) {
  return useQuery({
    queryKey: ['credits', 'top-teams', limit],
    queryFn: async () => {
      const { data } = await apiClient.get<TopTeamItem[]>(
        '/api/v1/admin/credits/top-teams',
        { params: { limit } },
      )
      return data
    },
    refetchInterval: 60000,
  })
}

// ============================================
// Order Mutations
// ============================================

export function useConfirmOrder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderId: string) => {
      const { data } = await apiClient.post<OrderActionResult>(
        `/api/v1/admin/credits/orders/${orderId}/confirm`,
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits'] })
    },
  })
}

export function useRefundOrder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderId: string) => {
      const { data } = await apiClient.post<OrderActionResult>(
        `/api/v1/admin/credits/orders/${orderId}/refund`,
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits'] })
    },
  })
}

// ============================================
// Packages CRUD
// ============================================

export function usePackages() {
  return useQuery({
    queryKey: ['credits', 'packages'],
    queryFn: async () => {
      const { data } = await apiClient.get<PointPackage[]>('/api/v1/admin/credits/packages')
      return data
    },
  })
}

export function useCreatePackage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: PointPackageInput) => {
      const { data } = await apiClient.post<PointPackage>('/api/v1/admin/credits/packages', payload)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits', 'packages'] })
    },
  })
}

export function useUpdatePackage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, ...payload }: PointPackageInput & { id: string }) => {
      const { data } = await apiClient.put<PointPackage>(
        `/api/v1/admin/credits/packages/${id}`,
        payload,
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits', 'packages'] })
    },
  })
}

export function useDeletePackage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete<CreditsOkResult>(`/api/v1/admin/credits/packages/${id}`)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits', 'packages'] })
    },
  })
}

// ============================================
// Pricing
// ============================================

export function usePricing() {
  return useQuery({
    queryKey: ['credits', 'pricing'],
    queryFn: async () => {
      const { data } = await apiClient.get<PricingRule[]>('/api/v1/admin/credits/pricing')
      return data
    },
  })
}

export function useUpdatePricing() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      action_type,
      points_cost,
      description,
    }: {
      action_type: string
      points_cost: number
      description?: string | null
    }) => {
      const { data } = await apiClient.put<PricingRule>(
        `/api/v1/admin/credits/pricing/${action_type}`,
        { points_cost, description },
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits', 'pricing'] })
    },
  })
}

// ============================================
// Team Detail & Operations
// ============================================

export function useTeamDetail(teamId: string | null) {
  return useQuery({
    queryKey: ['credits', 'team-detail', teamId],
    queryFn: async () => {
      const { data } = await apiClient.get<TeamCreditsDetail>(
        `/api/v1/admin/credits/team/${teamId}/detail`,
      )
      return data
    },
    enabled: !!teamId,
  })
}

export function useAdjustPoints() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: { team_id: string; amount: number; description?: string }) => {
      const { data } = await apiClient.post<PointsAdjustResult>('/api/v1/admin/credits/adjust', payload)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits'] })
    },
  })
}

export function useBatchGift() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: { team_ids: string[]; amount: number; description?: string }) => {
      const { data } = await apiClient.post<BatchGiftResult>(
        '/api/v1/admin/credits/batch-gift',
        payload,
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits'] })
    },
  })
}
