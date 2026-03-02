import { useState } from 'react'
import {
  Table,
  Tag,
  Modal,
  Descriptions,
  Typography,
  Button,
  Card,
  Space,
} from '@arco-design/web-react'
import { IconEye, IconExport, IconFile } from '@arco-design/web-react/icon'
import { exportToCsv } from '../../utils/csv-export'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { useAuditLogs, type AuditLog } from '../../api/endpoints/audit-logs'
import { TimeRangeSelector, periodToDateRange } from '../../components/TimeRangeSelector'
import {
  FilterBuilder,
  getFilterValue,
  type FilterField,
  type FilterCondition,
} from '../../components/FilterBuilder'
import { PageHeader } from '../../components/PageHeader'
import { EmptyState } from '../../components/EmptyState'

const AUDIT_LOG_FIELDS: FilterField[] = [
  {
    key: 'action',
    label: 'Action',
    type: 'select',
    options: [
      { value: 'create', label: 'Create' },
      { value: 'update', label: 'Update' },
      { value: 'delete', label: 'Delete' },
      { value: 'ban', label: 'Ban' },
      { value: 'unban', label: 'Unban' },
      { value: 'role_change', label: 'Role Change' },
    ],
  },
  {
    key: 'target_type',
    label: 'Target Type',
    type: 'select',
    options: [
      { value: 'user', label: 'User' },
      { value: 'team', label: 'Team' },
      { value: 'video', label: 'Video' },
      { value: 'tag', label: 'Tag' },
      { value: 'api_key', label: 'API Key' },
    ],
  },
]

const PAGE_SIZE = 50

function getActionColor(action: string): string {
  const a = action.toLowerCase()
  if (a.includes('delete')) return 'red'
  if (a.includes('unban')) return 'cyan'
  if (a.includes('ban')) return 'orange'
  if (a.includes('create')) return 'green'
  if (a.includes('update') || a.includes('change')) return 'blue'
  return 'gray'
}

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
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

export function AuditLogList() {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<FilterCondition[]>([])
  const [detailsModal, setDetailsModal] = useState<AuditLog | null>(null)
  const [period, setPeriod] = useState('24h')
  const [dateRange, setDateRange] = useState<[string, string] | null>(null)

  const timeRange = periodToDateRange(period, dateRange)

  const { data, isLoading } = useAuditLogs({
    page,
    pageSize: PAGE_SIZE,
    action: getFilterValue(filters, 'action'),
    target_type: getFilterValue(filters, 'target_type'),
    ...timeRange,
  })

  const logs = data?.items ?? []
  const total = data?.total ?? 0

  const columns: ColumnProps<AuditLog>[] = [
    {
      title: 'Time',
      dataIndex: 'created_at',
      width: 200,
      render: (_, record) => formatDateTime(record.created_at),
    },
    {
      title: 'Admin',
      dataIndex: 'admin_email',
      width: 200,
      render: (_, record) => (
        <div>
          <div>{record.admin_email || 'Unknown'}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {truncateId(record.admin_id)}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Action',
      dataIndex: 'action',
      width: 140,
      render: (_, record) => (
        <Tag color={getActionColor(record.action)}>
          {formatAction(record.action)}
        </Tag>
      ),
    },
    {
      title: 'Target',
      dataIndex: 'target_type',
      width: 180,
      render: (_, record) => (
        <div>
          <div>{formatTargetType(record.target_type)}</div>
          <Typography.Text
            type="secondary"
            style={{ fontSize: 12, fontFamily: 'monospace' }}
          >
            {truncateId(record.target_id)}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Details',
      dataIndex: 'details',
      render: (_, record) =>
        record.details ? (
          <Space>
            <Typography.Text
              type="secondary"
              style={{ fontFamily: 'monospace', fontSize: 12, maxWidth: 200 }}
              ellipsis
            >
              {renderDetailsPreview(record.details)}
            </Typography.Text>
            <Button
              type="text"
              size="mini"
              icon={<IconEye />}
              onClick={() => setDetailsModal(record)}
            />
          </Space>
        ) : (
          <Typography.Text type="secondary">-</Typography.Text>
        ),
    },
    {
      title: 'IP Address',
      dataIndex: 'ip_address',
      width: 140,
      render: (_, record) => (
        <Typography.Text style={{ fontFamily: 'monospace' }}>
          {record.ip_address || '-'}
        </Typography.Text>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Audit Logs"
        subtitle="Track admin actions — create, update, delete, and role changes"
        icon={<IconFile />}
        breadcrumb={['Logs & Monitoring', 'Audit Logs']}
      />
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size="medium">
          <TimeRangeSelector
            period={period}
            onPeriodChange={setPeriod}
            dateRange={dateRange}
            onDateRangeChange={setDateRange}
          />
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <FilterBuilder
            fields={AUDIT_LOG_FIELDS}
            filters={filters}
            onChange={(f) => { setFilters(f); setPage(1) }}
          />
          <Button
            icon={<IconExport />}
            size="small"
            style={{ flexShrink: 0 }}
            onClick={() => exportToCsv(
              `audit-logs-${new Date().toISOString().slice(0, 10)}.csv`,
              logs as unknown as Record<string, unknown>[],
              [
                { key: 'created_at', label: 'Time' },
                { key: 'admin_email', label: 'Admin' },
                { key: 'action', label: 'Action' },
                { key: 'target_type', label: 'Target Type' },
                { key: 'target_id', label: 'Target ID' },
                { key: 'ip_address', label: 'IP Address' },
                { key: 'details', label: 'Details' },
              ],
            )}
          >
            Export
          </Button>
        </div>
        </Space>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={logs}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: (t) => `Total ${t} entries`,
            sizeCanChange: false,
          }}
          noDataElement={<EmptyState description="No audit logs found" />}
        />
      </Card>

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
