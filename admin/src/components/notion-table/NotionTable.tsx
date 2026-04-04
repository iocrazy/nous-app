import { type ReactNode, Fragment, useState, useCallback } from 'react'
import { Spin, Pagination, Empty, Card, Typography } from '@arco-design/web-react'
import { flexRender, type Table } from '@tanstack/react-table'
import type { NotionTableToolbarProps } from './types'
import { NotionTableToolbar } from './NotionTableToolbar'
import './notion-table.css'

interface NotionTableProps<T> {
  table: Table<T>
  toolbarProps: NotionTableToolbarProps<T>
  pagination: { page: number; pageSize: number; total: number }
  onPageChange: (page: number) => void
  isLoading?: boolean
  title?: string
  headerContent?: ReactNode
  toolbarExtra?: ReactNode
  emptyText?: string
  onRowClick?: (row: T) => void
  expandedRowRender?: (row: T) => ReactNode
  scrollX?: number
}

export function NotionTable<T>({
  table,
  toolbarProps,
  pagination,
  onPageChange,
  isLoading = false,
  title,
  headerContent,
  toolbarExtra,
  emptyText = 'No data found',
  onRowClick,
  expandedRowRender,
  scrollX,
}: NotionTableProps<T>) {
  const rows = table.getRowModel().rows
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const toggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleRowClick = (row: T, rowId: string) => {
    if (expandedRowRender) toggleExpand(rowId)
    onRowClick?.(row)
  }

  return (
    <div>
      {title && (
        <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 16 }}>
          {title}
        </Typography.Title>
      )}
      {headerContent}
      <Card>
        <NotionTableToolbar {...toolbarProps} extra={toolbarExtra} />
        <Spin loading={isLoading} style={{ display: 'block' }}>
          <div
            className="notion-table-wrapper"
            style={{ overflowX: scrollX ? 'auto' : undefined }}
          >
            <table className="notion-table" style={{ minWidth: scrollX }}>
              <thead>
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <th
                        key={header.id}
                        style={{
                          width: header.getSize(),
                          minWidth: header.column.columnDef.minSize,
                        }}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                        {header.column.getCanResize() && (
                          <div
                            onMouseDown={header.getResizeHandler()}
                            onTouchStart={header.getResizeHandler()}
                            className={`notion-table-resize-handle ${
                              header.column.getIsResizing() ? 'is-resizing' : ''
                            }`}
                          />
                        )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td
                      colSpan={table.getVisibleFlatColumns().length}
                      style={{ textAlign: 'center', padding: 40 }}
                    >
                      <Empty description={emptyText} />
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => (
                    <Fragment key={row.id}>
                      <tr
                        onClick={() => handleRowClick(row.original, row.id)}
                        style={
                          onRowClick || expandedRowRender ? { cursor: 'pointer' } : undefined
                        }
                      >
                        {row.getVisibleCells().map((cell) => (
                          <td key={cell.id}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>
                      {expandedRowRender && expandedIds.has(row.id) && (
                        <tr className="notion-table-expanded-row">
                          <td colSpan={row.getVisibleCells().length} style={{ padding: 0 }}>
                            {expandedRowRender(row.original)}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Spin>

        {pagination.total > 0 && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 16,
            }}
          >
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              Total {pagination.total}
            </Typography.Text>
            <Pagination
              current={pagination.page}
              pageSize={pagination.pageSize}
              total={pagination.total}
              onChange={onPageChange}
              size="small"
              showTotal={false}
            />
          </div>
        )}
      </Card>
    </div>
  )
}
