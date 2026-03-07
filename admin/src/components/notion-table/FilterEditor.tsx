import { useState, useEffect } from 'react'
import { Select, Input, InputNumber, DatePicker, Button, Space } from '@arco-design/web-react'
import type { NotionColumnDef, TableFilter } from './types'
import { OPERATORS_BY_TYPE } from './types'

interface FilterEditorProps {
  columns: NotionColumnDef<unknown>[]
  initialFilter?: TableFilter
  onSubmit: (filter: TableFilter) => void
  onCancel: () => void
}

export function FilterEditor({ columns, initialFilter, onSubmit, onCancel }: FilterEditorProps) {
  const filterableColumns = columns.filter((c) => c.filterable)

  const [field, setField] = useState(initialFilter?.field ?? '')
  const [operator, setOperator] = useState(initialFilter?.operator ?? '')
  const [value, setValue] = useState<TableFilter['value']>(initialFilter?.value ?? '')

  const selectedColumn = filterableColumns.find((c) => c.key === field)
  const operators = selectedColumn ? OPERATORS_BY_TYPE[selectedColumn.type] : []
  const selectedOp = operators.find((op) => op.value === operator)

  useEffect(() => {
    if (!initialFilter) {
      setOperator('')
      setValue('')
    }
  }, [field, initialFilter])

  const handleSubmit = () => {
    if (!field || !operator) return
    onSubmit({ field, operator, value: selectedOp?.needsValue === false ? null : value })
  }

  const renderValueInput = () => {
    if (!selectedColumn || !selectedOp || selectedOp.needsValue === false) return null

    if (selectedColumn.type === 'select') {
      if (operator === 'is_any_of') {
        return (
          <Select
            mode="multiple"
            placeholder="Select values..."
            value={Array.isArray(value) ? value : []}
            onChange={(v) => setValue(v)}
            style={{ minWidth: 160 }}
            size="small"
          >
            {selectedColumn.filterOptions?.map((opt) => (
              <Select.Option key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Option>
            ))}
          </Select>
        )
      }
      return (
        <Select
          placeholder="Select value..."
          value={typeof value === 'string' ? value : undefined}
          onChange={(v) => setValue(v)}
          style={{ minWidth: 140 }}
          size="small"
        >
          {selectedColumn.filterOptions?.map((opt) => (
            <Select.Option key={opt.value} value={opt.value}>
              {opt.label}
            </Select.Option>
          ))}
        </Select>
      )
    }

    if (selectedColumn.type === 'number') {
      return (
        <InputNumber
          placeholder="Value"
          value={typeof value === 'number' ? value : undefined}
          onChange={(v) => setValue(v ?? null)}
          size="small"
          style={{ width: 120 }}
        />
      )
    }

    if (selectedColumn.type === 'date') {
      return (
        <DatePicker
          size="small"
          style={{ width: 160 }}
          value={typeof value === 'string' ? value : undefined}
          onChange={(dateString) => setValue(dateString)}
        />
      )
    }

    return (
      <Input
        placeholder="Value..."
        value={typeof value === 'string' ? value : ''}
        onChange={(v) => setValue(v)}
        size="small"
        style={{ width: 160 }}
        onPressEnter={handleSubmit}
      />
    )
  }

  return (
    <div style={{ padding: 8, minWidth: 300 }}>
      <Space direction="vertical" style={{ width: '100%' }} size="small">
        <Space size="small" wrap>
          <Select
            placeholder="Field..."
            value={field || undefined}
            onChange={setField}
            size="small"
            style={{ minWidth: 130 }}
          >
            {filterableColumns.map((col) => (
              <Select.Option key={col.key} value={col.key}>
                {col.header}
              </Select.Option>
            ))}
          </Select>
          {field && (
            <Select
              placeholder="Operator..."
              value={operator || undefined}
              onChange={setOperator}
              size="small"
              style={{ minWidth: 120 }}
            >
              {operators.map((op) => (
                <Select.Option key={op.value} value={op.value}>
                  {op.label}
                </Select.Option>
              ))}
            </Select>
          )}
          {renderValueInput()}
        </Space>
        <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
          <Button size="mini" onClick={onCancel}>
            Cancel
          </Button>
          <Button size="mini" type="primary" onClick={handleSubmit} disabled={!field || !operator}>
            Apply
          </Button>
        </Space>
      </Space>
    </div>
  )
}
