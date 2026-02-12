import { useState } from 'react'
import {
  Table,
  Tag,
  Select,
  Modal,
  Descriptions,
  Typography,
  Button,
  Card,
  Space,
} from '@arco-design/web-react'
import { IconEye } from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { useAuditLogs, type AuditLog } from '../../api/endpoints/audit-logs'

const ACTION_TYPES = [
  { value: '', label: 'All Actions' },
  { value: 'create', label: 'Create' },
  { value: 'update', label: 'Update' },
  { value: 'delete', label: 'Delete' },
  { value: 'ban', label: 'Ban' },
  { value: 'unban', label: 'Unban' },
  { value: 'role_change', label: 'Role Change' },
]

const TARGET_TYPES = [
  { value: '', label: 'All Targets' },
  { value: 'user', label: 'User' },
  { value: 'team', label: 'Team' },
  { value: 'video', label: 'Video' },
  { value: 'tag', label: 'Tag' },
  { value: 'api_key', label: 'API Key' },
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
  return new Date(dateStr).toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
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
  const [actionFilter, setActionFilter] = useState('')
  const [targetFilter, setTargetFilter] = useState('')
  const [detailsModal, setDetailsModal] = useState<AuditLog | null>(null)

  const { data, isLoading } = useAuditLogs({
    page,
    pageSize: PAGE_SIZE,
    action: actionFilter || undefined,
    target_type: targetFilter || undefined,
  })

  const logs = data?.data ?? []
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
      <Card style={{ marginBottom: 16 }}>
        <Space size="medium">
          <Select
            placeholder="All Actions"
            value={actionFilter}
            onChange={(val) => {
              setActionFilter(val)
              setPage(1)
            }}
            style={{ width: 180 }}
            allowClear
            onClear={() => {
              setActionFilter('')
              setPage(1)
            }}
          >
            {ACTION_TYPES.filter((a) => a.value).map((a) => (
              <Select.Option key={a.value} value={a.value}>
                {a.label}
              </Select.Option>
            ))}
          </Select>
          <Select
            placeholder="All Targets"
            value={targetFilter}
            onChange={(val) => {
              setTargetFilter(val)
              setPage(1)
            }}
            style={{ width: 180 }}
            allowClear
            onClear={() => {
              setTargetFilter('')
              setPage(1)
            }}
          >
            {TARGET_TYPES.filter((t) => t.value).map((t) => (
              <Select.Option key={t.value} value={t.value}>
                {t.label}
              </Select.Option>
            ))}
          </Select>
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
          noDataElement="No audit logs found"
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
