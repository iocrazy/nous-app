import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  type ColumnDef,
  type Table,
  type VisibilityState,
} from '@tanstack/react-table'
import { useQuery } from '@tanstack/react-query'
import {
  useTablePreference,
  useUpdateTablePreference,
} from '../api/endpoints/table-preferences'
import type {
  UseNotionTableConfig,
  TableFilter,
  TableSort,
  NotionTableToolbarProps,
} from '../components/notion-table/types'

const SAVE_DEBOUNCE_MS = 800

export function useNotionTable<T>(config: UseNotionTableConfig<T>) {
  const {
    tableKey,
    columns,
    fetchData,
    defaultSorts = [],
    defaultPageSize = 20,
    refetchInterval = false,
    rowKey = 'id',
  } = config

  // --- Load saved preferences ---
  const { data: savedPrefs, isSuccess: prefsLoaded } = useTablePreference(tableKey)
  const updatePrefs = useUpdateTablePreference(tableKey)

  // --- Local state ---
  const [filters, setFilters] = useState<TableFilter[]>([])
  const [sorts, setSorts] = useState<TableSort[]>(defaultSorts)
  const [visibleColumns, setVisibleColumns] = useState<string[]>(
    columns.map((c) => c.key),
  )
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize] = useState(defaultPageSize)
  const [initialized, setInitialized] = useState(false)

  // --- Initialize from saved preferences ---
  useEffect(() => {
    if (prefsLoaded && !initialized) {
      if (savedPrefs?.filters?.length) setFilters(savedPrefs.filters)
      if (savedPrefs?.sorts?.length) setSorts(savedPrefs.sorts)
      if (savedPrefs?.visible_columns?.length) setVisibleColumns(savedPrefs.visible_columns)
      setInitialized(true)
    }
  }, [prefsLoaded, savedPrefs, initialized])

  // --- Debounced save ---
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const savePreferences = useCallback(
    (f: TableFilter[], s: TableSort[], vc: string[]) => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      saveTimerRef.current = setTimeout(() => {
        updatePrefs.mutate({
          filters: f,
          sorts: s,
          visible_columns: vc,
          column_order: null,
        })
      }, SAVE_DEBOUNCE_MS)
    },
    [updatePrefs],
  )

  // --- Filter actions ---
  const addFilter = useCallback(
    (filter: TableFilter) => {
      setFilters((prev) => {
        const next = [...prev, filter]
        setPage(1)
        savePreferences(next, sorts, visibleColumns)
        return next
      })
    },
    [sorts, visibleColumns, savePreferences],
  )

  const removeFilter = useCallback(
    (index: number) => {
      setFilters((prev) => {
        const next = prev.filter((_, i) => i !== index)
        setPage(1)
        savePreferences(next, sorts, visibleColumns)
        return next
      })
    },
    [sorts, visibleColumns, savePreferences],
  )

  const updateFilter = useCallback(
    (index: number, filter: TableFilter) => {
      setFilters((prev) => {
        const next = prev.map((f, i) => (i === index ? filter : f))
        setPage(1)
        savePreferences(next, sorts, visibleColumns)
        return next
      })
    },
    [sorts, visibleColumns, savePreferences],
  )

  // --- Sort actions ---
  const addSort = useCallback(
    (sort: TableSort) => {
      setSorts((prev) => {
        const exists = prev.findIndex((s) => s.field === sort.field)
        const next = exists >= 0
          ? prev.map((s, i) => (i === exists ? sort : s))
          : [...prev, sort]
        setPage(1)
        savePreferences(filters, next, visibleColumns)
        return next
      })
    },
    [filters, visibleColumns, savePreferences],
  )

  const removeSort = useCallback(
    (index: number) => {
      setSorts((prev) => {
        const next = prev.filter((_, i) => i !== index)
        savePreferences(filters, next, visibleColumns)
        return next
      })
    },
    [filters, visibleColumns, savePreferences],
  )

  const updateSort = useCallback(
    (index: number, sort: TableSort) => {
      setSorts((prev) => {
        const next = prev.map((s, i) => (i === index ? sort : s))
        savePreferences(filters, next, visibleColumns)
        return next
      })
    },
    [filters, visibleColumns, savePreferences],
  )

  // --- Column visibility ---
  const toggleColumn = useCallback(
    (key: string) => {
      const col = columns.find((c) => c.key === key)
      if (col?.required) return
      setVisibleColumns((prev) => {
        const next = prev.includes(key)
          ? prev.filter((k) => k !== key)
          : [...prev, key]
        savePreferences(filters, sorts, next)
        return next
      })
    },
    [columns, filters, sorts, savePreferences],
  )

  const resetColumns = useCallback(() => {
    const all = columns.map((c) => c.key)
    setVisibleColumns(all)
    savePreferences(filters, sorts, all)
  }, [columns, filters, sorts, savePreferences])

  // --- Search ---
  const handleSetSearch = useCallback((value: string) => {
    setSearch(value)
    setPage(1)
  }, [])

  // --- Reset all ---
  const resetAll = useCallback(() => {
    setFilters([])
    setSorts(defaultSorts)
    setVisibleColumns(columns.map((c) => c.key))
    setSearch('')
    setPage(1)
    savePreferences([], defaultSorts, columns.map((c) => c.key))
  }, [columns, defaultSorts, savePreferences])

  // --- Data fetching ---
  const queryResult = useQuery({
    queryKey: [tableKey, { page, pageSize, filters, sorts, search }],
    queryFn: () => fetchData({ page, pageSize, filters, sorts, search }),
    enabled: initialized,
    refetchInterval,
    placeholderData: (prev) => prev,
  })

  const data = queryResult.data?.items ?? []
  const total = queryResult.data?.total ?? 0

  // --- TanStack Table column defs ---
  const tanstackColumns = useMemo<ColumnDef<T, unknown>[]>(
    () =>
      columns
        .filter((col) => visibleColumns.includes(col.key))
        .map((col) => ({
          id: col.key,
          header: col.header,
          size: col.size,
          minSize: col.minSize,
          accessorFn: (row: T) => (row as Record<string, unknown>)[col.key],
          cell: col.cell
            ? ({ row }: { row: { original: T } }) => col.cell!(row.original)
            : ({ getValue }: { getValue: () => unknown }) => {
                const val = getValue()
                return val == null ? '-' : String(val)
              },
        })),
    [columns, visibleColumns],
  )

  // --- Column visibility state ---
  const columnVisibility = useMemo<VisibilityState>(() => {
    const vis: VisibilityState = {}
    for (const col of columns) {
      vis[col.key] = visibleColumns.includes(col.key)
    }
    return vis
  }, [columns, visibleColumns])

  // --- TanStack Table instance ---
  const getRowId = useCallback(
    (row: T) => {
      if (typeof rowKey === 'function') return rowKey(row)
      return String((row as Record<string, unknown>)[rowKey])
    },
    [rowKey],
  )

  const table: Table<T> = useReactTable({
    data,
    columns: tanstackColumns,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
    state: { columnVisibility },
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    pageCount: Math.ceil(total / pageSize),
  })

  // --- Toolbar props ---
  const toolbarProps: NotionTableToolbarProps<T> = {
    columns,
    filters,
    sorts,
    visibleColumns,
    search,
    onAddFilter: addFilter,
    onRemoveFilter: removeFilter,
    onUpdateFilter: updateFilter,
    onAddSort: addSort,
    onRemoveSort: removeSort,
    onUpdateSort: updateSort,
    onToggleColumn: toggleColumn,
    onResetColumns: resetColumns,
    onSetSearch: handleSetSearch,
    onResetAll: resetAll,
  }

  return {
    table,
    toolbarProps,
    filters,
    sorts,
    visibleColumns,
    search,
    pagination: { page, pageSize, total },
    isLoading: queryResult.isLoading || queryResult.isFetching,
    addFilter,
    removeFilter,
    updateFilter,
    addSort,
    removeSort,
    toggleColumn,
    setSearch: handleSetSearch,
    setPage,
    resetAll,
    queryResult,
  }
}
