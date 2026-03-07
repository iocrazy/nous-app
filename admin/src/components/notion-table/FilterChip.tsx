import { useState } from 'react'
import { Tag, Popover } from '@arco-design/web-react'
import { IconClose } from '@arco-design/web-react/icon'
import type { TableFilter, NotionColumnDef } from './types'
import { OPERATORS_BY_TYPE } from './types'
import { FilterEditor } from './FilterEditor'

interface FilterChipProps {
  filter: TableFilter
  index: number
  columns: NotionColumnDef<unknown>[]
  onUpdate: (index: number, filter: TableFilter) => void
  onRemove: (index: number) => void
}

function formatFilterValue(value: TableFilter['value']): string {
  if (value == null) return ''
  if (Array.isArray(value)) return value.join(', ')
  return String(value)
}

export function FilterChip({ filter, index, columns, onUpdate, onRemove }: FilterChipProps) {
  const [editing, setEditing] = useState(false)

  const col = columns.find((c) => c.key === filter.field)
  const operators = col ? OPERATORS_BY_TYPE[col.type] : []
  const op = operators.find((o) => o.value === filter.operator)

  const label = `${col?.header ?? filter.field} ${op?.label ?? filter.operator}${
    op?.needsValue !== false && filter.value != null ? ` ${formatFilterValue(filter.value)}` : ''
  }`

  return (
    <Popover
      trigger="click"
      popupVisible={editing}
      onVisibleChange={setEditing}
      content={
        <FilterEditor
          columns={columns}
          initialFilter={filter}
          onSubmit={(updated) => {
            onUpdate(index, updated)
            setEditing(false)
          }}
          onCancel={() => setEditing(false)}
        />
      }
    >
      <Tag
        closable
        onClose={(e) => {
          e.stopPropagation()
          onRemove(index)
        }}
        closeIcon={<IconClose style={{ fontSize: 10 }} />}
        style={{ cursor: 'pointer', marginRight: 4, marginBottom: 4 }}
        color="arcoblue"
        size="small"
      >
        {label}
      </Tag>
    </Popover>
  )
}
