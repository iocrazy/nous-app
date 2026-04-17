import { useState, useCallback } from 'react'
import type { TableFilter } from '../../components/notion-table'
import { exportToCsv } from '../../utils/csv-export'

interface CsvColumn {
  key: string
  label: string
}

interface UseLogsToolbarConfig {
  dateField: string
  filters: TableFilter[]
  addFilter: (filter: TableFilter) => void
  updateFilter: (index: number, filter: TableFilter) => void
  getData: () => Record<string, unknown>[]
  csvColumns: CsvColumn[]
  filePrefix: string
  onRefresh: () => void
}

export function useLogsToolbar(config: UseLogsToolbarConfig) {
  const {
    dateField,
    filters,
    addFilter,
    updateFilter,
    getData,
    csvColumns,
    filePrefix,
    onRefresh,
  } = config

  const [selectedRange, setSelectedRange] = useState('1h')

  const handleRangeChange = useCallback(
    (range: string, startDate: string) => {
      setSelectedRange(range)
      const existingIndex = filters.findIndex((f) => f.field === dateField)
      const filter: TableFilter = {
        field: dateField,
        operator: 'after',
        value: startDate,
      }
      if (existingIndex >= 0) {
        updateFilter(existingIndex, filter)
      } else {
        addFilter(filter)
      }
    },
    [dateField, filters, addFilter, updateFilter],
  )

  const handleCustomRange = useCallback(
    (start: string, _end: string) => {
      setSelectedRange('custom')
      const existingIndex = filters.findIndex((f) => f.field === dateField)
      const filter: TableFilter = {
        field: dateField,
        operator: 'after',
        value: start,
      }
      if (existingIndex >= 0) {
        updateFilter(existingIndex, filter)
      } else {
        addFilter(filter)
      }
    },
    [dateField, filters, addFilter, updateFilter],
  )

  const handleExportCsv = useCallback(() => {
    exportToCsv(
      `${filePrefix}-${new Date().toISOString().slice(0, 10)}.csv`,
      getData(),
      csvColumns,
    )
  }, [filePrefix, getData, csvColumns])

  const handleExportJson = useCallback(() => {
    const data = getData()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${filePrefix}-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [filePrefix, getData])

  return {
    selectedRange,
    handleRangeChange,
    handleCustomRange,
    handleExportCsv,
    handleExportJson,
    handleRefresh: onRefresh,
  }
}
