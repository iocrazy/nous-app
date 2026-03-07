import type { ReactNode } from 'react'

// --- Filter/Sort primitives ---

export interface TableFilter {
  field: string
  operator: string
  value: string | string[] | number | boolean | null
}

export interface TableSort {
  field: string
  direction: 'asc' | 'desc'
}

// --- Column definition ---

export type ColumnType = 'text' | 'number' | 'select' | 'date' | 'boolean'

export interface NotionColumnDef<T> {
  key: string
  header: string
  type: ColumnType
  filterable?: boolean
  sortable?: boolean
  required?: boolean
  filterOptions?: { label: string; value: string }[]
  cell?: (row: T) => ReactNode
  size?: number
  minSize?: number
}

// --- Fetch params ---

export interface FetchParams {
  page: number
  pageSize: number
  filters: TableFilter[]
  sorts: TableSort[]
  search: string
}

export interface FetchResult<T> {
  items: T[]
  total: number
}

// --- Hook config ---

export interface UseNotionTableConfig<T> {
  tableKey: string
  columns: NotionColumnDef<T>[]
  fetchData: (params: FetchParams) => Promise<FetchResult<T>>
  defaultSorts?: TableSort[]
  defaultPageSize?: number
  refetchInterval?: number | false
  rowKey?: string | ((row: T) => string)
}

// --- Toolbar props ---

export interface NotionTableToolbarProps<T> {
  columns: NotionColumnDef<T>[]
  filters: TableFilter[]
  sorts: TableSort[]
  visibleColumns: string[]
  search: string
  onAddFilter: (filter: TableFilter) => void
  onRemoveFilter: (index: number) => void
  onUpdateFilter: (index: number, filter: TableFilter) => void
  onAddSort: (sort: TableSort) => void
  onRemoveSort: (index: number) => void
  onUpdateSort: (index: number, sort: TableSort) => void
  onToggleColumn: (key: string) => void
  onResetColumns: () => void
  onSetSearch: (value: string) => void
  onResetAll: () => void
}

// --- Operator definitions ---

export interface OperatorDef {
  value: string
  label: string
  needsValue?: boolean
}

export const OPERATORS_BY_TYPE: Record<ColumnType, OperatorDef[]> = {
  text: [
    { value: 'contains', label: 'Contains', needsValue: true },
    { value: 'equals', label: 'Equals', needsValue: true },
    { value: 'not_equals', label: 'Not equals', needsValue: true },
    { value: 'starts_with', label: 'Starts with', needsValue: true },
    { value: 'is_empty', label: 'Is empty', needsValue: false },
  ],
  number: [
    { value: 'eq', label: '=', needsValue: true },
    { value: 'neq', label: '!=', needsValue: true },
    { value: 'gt', label: '>', needsValue: true },
    { value: 'lt', label: '<', needsValue: true },
    { value: 'gte', label: '>=', needsValue: true },
    { value: 'lte', label: '<=', needsValue: true },
  ],
  select: [
    { value: 'is', label: 'Is', needsValue: true },
    { value: 'is_not', label: 'Is not', needsValue: true },
    { value: 'is_any_of', label: 'Is any of', needsValue: true },
  ],
  date: [
    { value: 'after', label: 'After', needsValue: true },
    { value: 'before', label: 'Before', needsValue: true },
    { value: 'last_7_days', label: 'Last 7 days', needsValue: false },
    { value: 'last_30_days', label: 'Last 30 days', needsValue: false },
  ],
  boolean: [
    { value: 'is_true', label: 'Is true', needsValue: false },
    { value: 'is_false', label: 'Is false', needsValue: false },
  ],
}
