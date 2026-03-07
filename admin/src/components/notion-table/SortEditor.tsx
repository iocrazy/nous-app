import { useState } from 'react'
import { Select, Button, Space, Tag, Radio } from '@arco-design/web-react'
import { IconClose } from '@arco-design/web-react/icon'
import type { NotionColumnDef, TableSort } from './types'

interface SortEditorContentProps {
  columns: NotionColumnDef<unknown>[]
  existingSortFields: string[]
  onSubmit: (sort: TableSort) => void
  onCancel: () => void
}

export function SortEditorContent({
  columns,
  existingSortFields,
  onSubmit,
  onCancel,
}: SortEditorContentProps) {
  const sortableColumns = columns.filter(
    (c) => c.sortable && !existingSortFields.includes(c.key),
  )
  const [field, setField] = useState('')
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc')

  return (
    <div style={{ padding: 8, minWidth: 240 }}>
      <Space direction="vertical" style={{ width: '100%' }} size="small">
        <Select
          placeholder="Field..."
          value={field || undefined}
          onChange={setField}
          size="small"
          style={{ width: '100%' }}
        >
          {sortableColumns.map((col) => (
            <Select.Option key={col.key} value={col.key}>
              {col.header}
            </Select.Option>
          ))}
        </Select>
        <Radio.Group value={direction} onChange={setDirection} size="small">
          <Radio value="asc">Ascending</Radio>
          <Radio value="desc">Descending</Radio>
        </Radio.Group>
        <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
          <Button size="mini" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="mini"
            type="primary"
            disabled={!field}
            onClick={() => onSubmit({ field, direction })}
          >
            Apply
          </Button>
        </Space>
      </Space>
    </div>
  )
}

interface SortChipProps {
  sort: TableSort
  index: number
  columns: NotionColumnDef<unknown>[]
  onRemove: (index: number) => void
  onUpdate: (index: number, sort: TableSort) => void
}

export function SortChip({ sort, index, columns, onRemove, onUpdate }: SortChipProps) {
  const col = columns.find((c) => c.key === sort.field)
  const arrow = sort.direction === 'asc' ? '\u2191' : '\u2193'

  return (
    <Tag
      closable
      onClose={(e) => {
        e.stopPropagation()
        onRemove(index)
      }}
      closeIcon={<IconClose style={{ fontSize: 10 }} />}
      style={{ cursor: 'pointer', marginRight: 4, marginBottom: 4 }}
      color="green"
      size="small"
      onClick={() => {
        onUpdate(index, { ...sort, direction: sort.direction === 'asc' ? 'desc' : 'asc' })
      }}
    >
      {col?.header ?? sort.field} {arrow}
    </Tag>
  )
}
