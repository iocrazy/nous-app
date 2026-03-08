import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../client'

// ============================================
// Types
// ============================================

export interface CreditsStats {
  total_points_in_system: number
  total_consumed: number
  total_purchased: number
  total_revenue_cents: number
  active_teams_count: number
  pending_orders_count: number
  monthly_revenue_cents: number
}

export interface RevenueChartItem {
  date: string
  revenue_cents: number
  points_sold: number
}

export interface ConsumptionChartItem {
  action_type: string
  total_points: number
}

export interface TopTeamItem {
  team_id: string
  team_name: string
  total_consumed: number
}

export interface CreditTransaction {
  id: string
  team_id: string
  team_name: string | null
  user_id: string | null
  user_email: string | null
  type: string
  amount: number
  balance_after: number
  description: string | null
  created_at: string
}

export interface CreditOrder {
  id: string
  order_no: string
  team_id: string
  team_name: string | null
  user_id: string | null
  user_email: string | null
  package_name: string | null
  points_amount: number
  amount_cents: number
  payment_method: string | null
  payment_status: string
  created_at: string
  paid_at: string | null
}

export interface PointPackage {
  id: string
  name: string
  description: string | null
  points_amount: number
  price_cents: number
  sort_order: number
  is_active: boolean
  created_at?: string
}

export interface PricingRule {
  id?: string
  action_type: string
  points_cost: number
  description: string | null
  is_active?: boolean
}

export interface TeamCreditsDetail {
  team_id: string
  team_name: string
  points_balance: number
  storage_limit_bytes: number
  storage_used_bytes: number
  member_count: number
  recent_transactions: CreditTransaction[]
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
  })
}

// ============================================
// Order Mutations
// ============================================

export function useConfirmOrder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderId: string) => {
      const { data } = await apiClient.post(`/api/v1/admin/credits/orders/${orderId}/confirm`)
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
      const { data } = await apiClient.post(`/api/v1/admin/credits/orders/${orderId}/refund`)
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
    mutationFn: async (payload: Omit<PointPackage, 'id' | 'created_at'>) => {
      const { data } = await apiClient.post('/api/v1/admin/credits/packages', payload)
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
    mutationFn: async ({ id, ...payload }: Omit<PointPackage, 'created_at'>) => {
      const { data } = await apiClient.put(`/api/v1/admin/credits/packages/${id}`, payload)
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
      const { data } = await apiClient.delete(`/api/v1/admin/credits/packages/${id}`)
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
      const { data } = await apiClient.put(
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
      const { data } = await apiClient.post('/api/v1/admin/credits/adjust', payload)
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
      const { data } = await apiClient.post('/api/v1/admin/credits/batch-gift', payload)
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credits'] })
    },
  })
}
