import { Checkbox, Button, Space, Typography } from '@arco-design/web-react'
import type { NotionColumnDef } from './types'

interface ColumnVisibilityProps {
  columns: NotionColumnDef<unknown>[]
  visibleColumns: string[]
  onToggle: (key: string) => void
  onReset: () => void
}

export function ColumnVisibility({
  columns,
  visibleColumns,
  onToggle,
  onReset,
}: ColumnVisibilityProps) {
  return (
    <div style={{ padding: 8, minWidth: 180, maxHeight: 320, overflow: 'auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 8,
        }}
      >
        <Typography.Text bold style={{ fontSize: 12 }}>
          Properties
        </Typography.Text>
        <Button size="mini" type="text" onClick={onReset}>
          Reset
        </Button>
      </div>
      <Space direction="vertical" size={2} style={{ width: '100%' }}>
        {columns.map((col) => (
          <div key={col.key} style={{ padding: '2px 0' }}>
            <Checkbox
              checked={visibleColumns.includes(col.key)}
              disabled={col.required}
              onChange={() => onToggle(col.key)}
            >
              <span style={{ fontSize: 13 }}>{col.header}</span>
            </Checkbox>
          </div>
        ))}
      </Space>
    </div>
  )
}
