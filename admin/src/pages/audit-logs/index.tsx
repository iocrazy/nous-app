import { useState, useMemo } from 'react'
import {
  Tag,
  Modal,
  Descriptions,
  Typography,
  Button,
  Space,
} from '@arco-design/web-react'
import { IconEye, IconExport } from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import type { AuditLog } from '../../api/endpoints/audit-logs'
import { formatDateTime } from '../../utils/format'
import { exportToCsv } from '../../utils/csv-export'

// --- Helpers ---

function getActionColor(action: string): string {
  const a = action.toLowerCase()
  if (a.includes('delete')) return 'red'
  if (a.includes('unban')) return 'cyan'
  if (a.includes('ban')) return 'orange'
  if (a.includes('create')) return 'green'
  if (a.includes('update') || a.includes('change')) return 'blue'
  return 'gray'
}

function formatAction(action: string): string {
  return action
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

function formatTargetType(type: string): string {
  return type
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

function truncateId(id: string): string {
  if (id.length <= 12) return id
  return `${id.slice(0, 8)}...`
}

function renderDetailsPreview(details: Record<string, unknown> | null): string {
  if (!details) return '-'
  const str = JSON.stringify(details)
  if (str.length <= 50) return str
  return str.slice(0, 47) + '...'
}

// --- Main component ---

export function AuditLogList() {
  const [detailsModal, setDetailsModal] = useState<AuditLog | null>(null)

  const columns = useMemo<NotionColumnDef<AuditLog>[]>(
    () => [
      {
        key: 'created_at',
        header: 'Time',
        type: 'date',
        filterable: true,
        sortable: true,
        required: true,
        size: 200,
        cell: (row) => formatDateTime(row.created_at),
      },
      {
        key: 'admin_email',
        header: 'Admin',
        type: 'text',
        filterable: true,
        size: 200,
        cell: (row) => (
          <div>
            <div>{row.admin_email || 'Unknown'}</div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {truncateId(row.admin_id)}
            </Typography.Text>
          </div>
        ),
      },
      {
        key: 'action',
        header: 'Action',
        type: 'select',
        filterable: true,
        size: 140,
        filterOptions: [
          { value: 'create', label: 'Create' },
          { value: 'update', label: 'Update' },
          { value: 'delete', label: 'Delete' },
          { value: 'ban', label: 'Ban' },
          { value: 'unban', label: 'Unban' },
          { value: 'role_change', label: 'Role Change' },
        ],
        cell: (row) => (
          <Tag color={getActionColor(row.action)}>
            {formatAction(row.action)}
          </Tag>
        ),
      },
      {
        key: 'target_type',
        header: 'Target',
        type: 'select',
        filterable: true,
        size: 180,
        filterOptions: [
          { value: 'user', label: 'User' },
          { value: 'team', label: 'Team' },
          { value: 'video', label: 'Video' },
          { value: 'tag', label: 'Tag' },
          { value: 'api_key', label: 'API Key' },
        ],
        cell: (row) => (
          <div>
            <div>{formatTargetType(row.target_type)}</div>
            <Typography.Text
              type="secondary"
              style={{ fontSize: 12, fontFamily: 'monospace' }}
            >
              {truncateId(row.target_id)}
            </Typography.Text>
          </div>
        ),
      },
      {
        key: 'details',
        header: 'Details',
        type: 'text',
        cell: (row) =>
          row.details ? (
            <Space>
              <Typography.Text
                type="secondary"
                style={{ fontFamily: 'monospace', fontSize: 12, maxWidth: 200 }}
                ellipsis
              >
                {renderDetailsPreview(row.details)}
              </Typography.Text>
              <Button
                type="text"
                size="mini"
                icon={<IconEye />}
                onClick={(e) => {
                  e.stopPropagation()
                  setDetailsModal(row)
                }}
              />
            </Space>
          ) : (
            <Typography.Text type="secondary">-</Typography.Text>
          ),
      },
      {
        key: 'ip_address',
        header: 'IP Address',
        type: 'text',
        size: 140,
        cell: (row) => (
          <Typography.Text style={{ fontFamily: 'monospace' }}>
            {row.ip_address || '-'}
          </Typography.Text>
        ),
      },
    ],
    [],
  )

  const { table, toolbarProps, pagination, isLoading, setPage } =
    useNotionTable<AuditLog>({
      tableKey: 'audit-logs',
      columns,
      defaultSorts: [{ field: 'created_at', direction: 'desc' }],
      defaultPageSize: 50,
      fetchData: async ({ page, pageSize, filters, sorts, search }) => {
        const actionFilter = filters.find((f) => f.field === 'action')
        const targetTypeFilter = filters.find((f) => f.field === 'target_type')
        const dateFilter = filters.find((f) => f.field === 'created_at')
        const sortBy = sorts[0]?.field
        const sortOrder = sorts[0]?.direction

        // Map date filter operators to start_date/end_date params
        const dateParams: Record<string, string> = {}
        if (dateFilter) {
          const now = new Date()
          if (dateFilter.operator === 'last_7_days') {
            dateParams.start_date = new Date(
              now.getTime() - 7 * 24 * 60 * 60 * 1000,
            ).toISOString()
          } else if (dateFilter.operator === 'last_30_days') {
            dateParams.start_date = new Date(
              now.getTime() - 30 * 24 * 60 * 60 * 1000,
            ).toISOString()
          } else if (dateFilter.operator === 'after' && dateFilter.value) {
            dateParams.start_date = new Date(
              dateFilter.value as string,
            ).toISOString()
          } else if (dateFilter.operator === 'before' && dateFilter.value) {
            dateParams.end_date = new Date(
              dateFilter.value as string,
            ).toISOString()
          }
        }

        const { data } = await apiClient.get('/api/v1/admin/audit-logs', {
          params: {
            page,
            pageSize,
            ...(search && { search }),
            ...(actionFilter?.value && { action: actionFilter.value }),
            ...(targetTypeFilter?.value && {
              target_type: targetTypeFilter.value,
            }),
            ...(sortBy && { sort_by: sortBy }),
            ...(sortOrder && { sort_order: sortOrder }),
            ...dateParams,
          },
        })
        return { items: data.items, total: data.total }
      },
    })

  const exportButton = (
    <Button
      icon={<IconExport />}
      size="small"
      onClick={() =>
        exportToCsv(
          `audit-logs-${new Date().toISOString().slice(0, 10)}.csv`,
          table
            .getRowModel()
            .rows.map((r) => r.original) as unknown as Record<
            string,
            unknown
          >[],
          [
            { key: 'created_at', label: 'Time' },
            { key: 'admin_email', label: 'Admin' },
            { key: 'action', label: 'Action' },
            { key: 'target_type', label: 'Target Type' },
            { key: 'target_id', label: 'Target ID' },
            { key: 'ip_address', label: 'IP Address' },
            { key: 'details', label: 'Details' },
          ],
        )
      }
    >
      Export
    </Button>
  )

  return (
    <div>
      <NotionTable<AuditLog>
        table={table}
        toolbarProps={toolbarProps}
        pagination={pagination}
        onPageChange={setPage}
        isLoading={isLoading}
        title="Audit Logs"
        toolbarExtra={exportButton}
        emptyText="No audit logs found"
        scrollX={1100}
      />

      <Modal
        title="Audit Log Details"
        visible={!!detailsModal}
        onCancel={() => setDetailsModal(null)}
        footer={
          <Button onClick={() => setDetailsModal(null)}>Close</Button>
        }
        style={{ width: 680 }}
      >
        {detailsModal && (
          <>
            <Descriptions
              column={2}
              data={[
                { label: 'Time', value: formatDateTime(detailsModal.created_at) },
                {
                  label: 'Admin',
                  value: detailsModal.admin_email || 'Unknown',
                },
                {
                  label: 'Action',
                  value: (
                    <Tag color={getActionColor(detailsModal.action)}>
                      {formatAction(detailsModal.action)}
                    </Tag>
                  ),
                },
                {
                  label: 'Target',
                  value: (
                    <span>
                      {formatTargetType(detailsModal.target_type)} -{' '}
                      <Typography.Paragraph
                        copyable
                        style={{
                          fontFamily: 'monospace',
                          display: 'inline',
                          margin: 0,
                        }}
                      >
                        {detailsModal.target_id}
                      </Typography.Paragraph>
                    </span>
                  ),
                },
                {
                  label: 'IP Address',
                  value: (
                    <Typography.Text style={{ fontFamily: 'monospace' }}>
                      {detailsModal.ip_address || '-'}
                    </Typography.Text>
                  ),
                },
              ]}
              style={{ marginBottom: 16 }}
            />
            <div>
              <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                Details (JSON)
              </Typography.Text>
              <pre
                style={{
                  padding: 16,
                  background: 'var(--color-fill-2)',
                  borderRadius: 4,
                  overflow: 'auto',
                  fontSize: 13,
                  fontFamily: 'monospace',
                  margin: 0,
                }}
              >
                {detailsModal.details
                  ? JSON.stringify(detailsModal.details, null, 2)
                  : 'No details'}
              </pre>
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}
