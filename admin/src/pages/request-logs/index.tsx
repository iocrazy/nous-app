import { useState, useEffect, useRef, useCallback } from 'react'
import { formatDateTime } from '../../utils/format'
import {
  Table,
  Tag,
  Modal,
  Descriptions,
  Typography,
  Button,
  Card,
  Space,
  Tabs,
  Switch,
} from '@arco-design/web-react'
import { IconEye, IconCopy, IconExport, IconPause, IconPlayArrow, IconCode } from '@arco-design/web-react/icon'
import { exportToCsv } from '../../utils/csv-export'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import {
  useRequestLogs,
  useFrontendErrors,
  useAppLogs,
  type RequestLog,
  type FrontendError,
  type AppLog,
} from '../../api/endpoints/request-logs'
import { TimeRangeSelector, periodToDateRange } from '../../components/TimeRangeSelector'
import { FilterBuilder, getFilterValue, type FilterField, type FilterCondition } from '../../components/FilterBuilder'
import { PageHeader } from '../../components/PageHeader'
import { EmptyState } from '../../components/EmptyState'
import { supabase } from '../../auth/supabase'

const PAGE_SIZE = 50

// ============================================
// Filter field definitions
// ============================================

const REQUEST_LOG_FIELDS: FilterField[] = [
  {
    key: 'method', label: 'Method', type: 'select',
    options: [
      { value: 'GET', label: 'GET' },
      { value: 'POST', label: 'POST' },
      { value: 'PUT', label: 'PUT' },
      { value: 'PATCH', label: 'PATCH' },
      { value: 'DELETE', label: 'DELETE' },
    ],
  },
  { key: 'path', label: 'Path', type: 'text' },
  {
    key: 'status_group', label: 'Status', type: 'select',
    options: [
      { value: '2xx', label: '2xx Success' },
      { value: '4xx', label: '4xx Client Error' },
      { value: '5xx', label: '5xx Server Error' },
    ],
  },
  { key: 'min_response_time', label: 'Response Time (ms)', type: 'number' },
  { key: 'request_id', label: 'Request ID', type: 'text' },
  { key: 'user_id', label: 'User ID', type: 'text' },
]

const FRONTEND_ERROR_FIELDS: FilterField[] = [
  {
    key: 'error_type', label: 'Error Type', type: 'select',
    options: [
      { value: 'runtime', label: 'Runtime' },
      { value: 'network', label: 'Network' },
      { value: 'unhandled_rejection', label: 'Unhandled Rejection' },
    ],
  },
]

const APP_LOG_FIELDS: FilterField[] = [
  {
    key: 'level', label: 'Level', type: 'select',
    options: [
      { value: 'DEBUG', label: 'DEBUG' },
      { value: 'INFO', label: 'INFO' },
      { value: 'SUCCESS', label: 'SUCCESS' },
      { value: 'WARNING', label: 'WARNING' },
      { value: 'ERROR', label: 'ERROR' },
      { value: 'CRITICAL', label: 'CRITICAL' },
    ],
  },
  { key: 'module', label: 'Module', type: 'text' },
  { key: 'message', label: 'Message', type: 'text' },
]

function getStatusColor(code: number | null): string {
  if (code === null) return 'gray'
  if (code < 300) return 'green'
  if (code < 400) return 'blue'
  if (code < 500) return 'orange'
  return 'red'
}

function getMethodColor(method: string): string {
  switch (method) {
    case 'GET': return 'arcoblue'
    case 'POST': return 'green'
    case 'PUT': return 'orange'
    case 'PATCH': return 'gold'
    case 'DELETE': return 'red'
    default: return 'gray'
  }
}

function formatResponseTime(ms: number | null): string {
  if (ms === null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).catch(() => {})
}

interface TabTimeRange {
  start_date?: string
  end_date?: string
}

// ============================================
// Request Logs Tab
// ============================================

function RequestLogsTab({ start_date, end_date }: TabTimeRange) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<FilterCondition[]>([])
  const [detailModal, setDetailModal] = useState<RequestLog | null>(null)

  const { data, isLoading } = useRequestLogs({
    page,
    pageSize: PAGE_SIZE,
    method: getFilterValue(filters, 'method'),
    path: getFilterValue(filters, 'path'),
    status_group: getFilterValue(filters, 'status_group'),
    min_response_time: getFilterValue(filters, 'min_response_time')
      ? Number(getFilterValue(filters, 'min_response_time'))
      : undefined,
    request_id: getFilterValue(filters, 'request_id'),
    user_id: getFilterValue(filters, 'user_id'),
    start_date,
    end_date,
  })

  const logs = data?.data ?? []
  const total = data?.total ?? 0

  const columns: ColumnProps<RequestLog>[] = [
    {
      title: 'Time',
      dataIndex: 'timestamp',
      width: 180,
      render: (_, record) => (
        <Typography.Text style={{ fontSize: 13 }}>
          {formatDateTime(record.timestamp)}
        </Typography.Text>
      ),
    },
    {
      title: 'Method & Path',
      dataIndex: 'path',
      render: (_, record) => (
        <div>
          <Space size={4}>
            <Tag color={getMethodColor(record.method)} size="small">
              {record.method}
            </Tag>
            <Typography.Text style={{ fontFamily: 'monospace', fontSize: 13 }} ellipsis>
              {record.path}
            </Typography.Text>
          </Space>
        </div>
      ),
    },
    {
      title: 'User',
      dataIndex: 'user_email',
      width: 180,
      render: (_, record) => (
        <div>
          <div>{record.user_email || '-'}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.auth_type}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status_code',
      width: 80,
      render: (_, record) => (
        <Tag color={getStatusColor(record.status_code)} size="small">
          {record.status_code ?? '-'}
        </Tag>
      ),
    },
    {
      title: 'Time',
      dataIndex: 'response_time_ms',
      width: 90,
      render: (_, record) => (
        <Typography.Text
          style={{
            fontFamily: 'monospace',
            fontSize: 13,
            color: (record.response_time_ms ?? 0) > 1000 ? 'var(--color-danger-6)' : undefined,
          }}
        >
          {formatResponseTime(record.response_time_ms)}
        </Typography.Text>
      ),
    },
    {
      title: 'IP',
      dataIndex: 'ip_address',
      width: 130,
      render: (_, record) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }}>
          {record.ip_address || '-'}
        </Typography.Text>
      ),
    },
    {
      title: '',
      width: 48,
      render: (_, record) => (
        <Button
          type="text"
          size="mini"
          icon={<IconEye />}
          onClick={() => setDetailModal(record)}
        />
      ),
    },
  ]

  return (
    <>
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <FilterBuilder
            fields={REQUEST_LOG_FIELDS}
            filters={filters}
            onChange={(f) => { setFilters(f); setPage(1) }}
          />
          <Button
            icon={<IconExport />}
            size="small"
            style={{ flexShrink: 0, marginLeft: 16 }}
            onClick={() => exportToCsv(
              `request-logs-${new Date().toISOString().slice(0, 10)}.csv`,
              logs as unknown as Record<string, unknown>[],
              [
                { key: 'timestamp', label: 'Time' },
                { key: 'method', label: 'Method' },
                { key: 'path', label: 'Path' },
                { key: 'status_code', label: 'Status' },
                { key: 'response_time_ms', label: 'Response Time (ms)' },
                { key: 'user_email', label: 'User' },
                { key: 'ip_address', label: 'IP Address' },
                { key: 'request_id', label: 'Request ID' },
              ],
            )}
          >
            Export
          </Button>
        </div>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={logs}
          loading={isLoading}
          scroll={{ x: 1100 }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: (t) => `Total ${t} entries`,
            sizeCanChange: false,
          }}
          noDataElement={<EmptyState description="No request logs found" />}
        />
      </Card>

      <Modal
        title="Request Log Details"
        visible={!!detailModal}
        onCancel={() => setDetailModal(null)}
        footer={<Button onClick={() => setDetailModal(null)}>Close</Button>}
        style={{ width: 720 }}
      >
        {detailModal && (
          <>
            <Descriptions
              column={2}
              data={[
                { label: 'Time', value: formatDateTime(detailModal.timestamp) },
                {
                  label: 'Request ID',
                  value: (
                    <Space size={4}>
                      <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }}>
                        {detailModal.request_id}
                      </Typography.Text>
                      <Button
                        type="text"
                        size="mini"
                        icon={<IconCopy />}
                        onClick={() => copyToClipboard(detailModal.request_id)}
                      />
                    </Space>
                  ),
                },
                {
                  label: 'Method & Path',
                  value: (
                    <Space size={4}>
                      <Tag color={getMethodColor(detailModal.method)} size="small">
                        {detailModal.method}
                      </Tag>
                      <span style={{ fontFamily: 'monospace' }}>{detailModal.path}</span>
                    </Space>
                  ),
                },
                {
                  label: 'Status',
                  value: (
                    <Tag color={getStatusColor(detailModal.status_code)}>
                      {detailModal.status_code ?? '-'}
                    </Tag>
                  ),
                },
                { label: 'Response Time', value: formatResponseTime(detailModal.response_time_ms) },
                { label: 'User', value: detailModal.user_email || '-' },
                { label: 'Auth Type', value: detailModal.auth_type },
                { label: 'IP Address', value: detailModal.ip_address || '-' },
              ]}
              style={{ marginBottom: 16 }}
            />

            {detailModal.query_params && (
              <div style={{ marginBottom: 12 }}>
                <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                  Query Parameters
                </Typography.Text>
                <pre style={preStyle}>
                  {JSON.stringify(detailModal.query_params, null, 2)}
                </pre>
              </div>
            )}

            {detailModal.request_body && (
              <div style={{ marginBottom: 12 }}>
                <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                  Request Body
                </Typography.Text>
                <pre style={preStyle}>
                  {JSON.stringify(detailModal.request_body, null, 2)}
                </pre>
              </div>
            )}

            {detailModal.error_detail && (
              <div>
                <Typography.Text bold style={{ marginBottom: 8, display: 'block', color: 'var(--color-danger-6)' }}>
                  Error Detail
                </Typography.Text>
                <pre style={{ ...preStyle, borderLeft: '3px solid var(--color-danger-6)' }}>
                  {detailModal.error_detail}
                </pre>
              </div>
            )}
          </>
        )}
      </Modal>
    </>
  )
}

// ============================================
// Frontend Errors Tab
// ============================================

function FrontendErrorsTab({ start_date, end_date }: TabTimeRange) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<FilterCondition[]>([])
  const [detailModal, setDetailModal] = useState<FrontendError | null>(null)

  const { data, isLoading } = useFrontendErrors({
    page,
    pageSize: PAGE_SIZE,
    error_type: getFilterValue(filters, 'error_type'),
    start_date,
    end_date,
  })

  const errors = data?.data ?? []
  const total = data?.total ?? 0

  const columns: ColumnProps<FrontendError>[] = [
    {
      title: 'Time',
      dataIndex: 'created_at',
      width: 180,
      render: (_, record) => (
        <Typography.Text style={{ fontSize: 13 }}>
          {formatDateTime(record.created_at)}
        </Typography.Text>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'error_type',
      width: 160,
      render: (_, record) => (
        <Tag color={record.error_type === 'runtime' ? 'red' : record.error_type === 'network' ? 'orange' : 'purple'} size="small">
          {record.error_type}
        </Tag>
      ),
    },
    {
      title: 'Message',
      dataIndex: 'message',
      render: (_, record) => (
        <Typography.Text style={{ fontSize: 13 }} ellipsis>
          {record.message || '-'}
        </Typography.Text>
      ),
    },
    {
      title: 'Page',
      dataIndex: 'url',
      width: 200,
      render: (_, record) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }} ellipsis>
          {record.url ? new URL(record.url).pathname : '-'}
        </Typography.Text>
      ),
    },
    {
      title: 'User',
      dataIndex: 'user_email',
      width: 160,
      render: (_, record) => record.user_email || '-',
    },
    {
      title: '',
      width: 48,
      render: (_, record) => (
        <Button
          type="text"
          size="mini"
          icon={<IconEye />}
          onClick={() => setDetailModal(record)}
        />
      ),
    },
  ]

  return (
    <>
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <FilterBuilder
            fields={FRONTEND_ERROR_FIELDS}
            filters={filters}
            onChange={(f) => { setFilters(f); setPage(1) }}
          />
          <Button
            icon={<IconExport />}
            size="small"
            style={{ flexShrink: 0, marginLeft: 16 }}
            onClick={() => exportToCsv(
              `frontend-errors-${new Date().toISOString().slice(0, 10)}.csv`,
              errors as unknown as Record<string, unknown>[],
              [
                { key: 'created_at', label: 'Time' },
                { key: 'error_type', label: 'Type' },
                { key: 'message', label: 'Message' },
                { key: 'url', label: 'URL' },
                { key: 'user_email', label: 'User' },
                { key: 'stack', label: 'Stack Trace' },
              ],
            )}
          >
            Export
          </Button>
        </div>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={errors}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: (t) => `Total ${t} entries`,
            sizeCanChange: false,
          }}
          noDataElement={<EmptyState description="No frontend errors found" />}
        />
      </Card>

      <Modal
        title="Frontend Error Details"
        visible={!!detailModal}
        onCancel={() => setDetailModal(null)}
        footer={<Button onClick={() => setDetailModal(null)}>Close</Button>}
        style={{ width: 720 }}
      >
        {detailModal && (
          <>
            <Descriptions
              column={2}
              data={[
                { label: 'Time', value: formatDateTime(detailModal.created_at) },
                {
                  label: 'Type',
                  value: (
                    <Tag color={detailModal.error_type === 'runtime' ? 'red' : detailModal.error_type === 'network' ? 'orange' : 'purple'}>
                      {detailModal.error_type}
                    </Tag>
                  ),
                },
                { label: 'User', value: detailModal.user_email || '-' },
                { label: 'Session', value: detailModal.session_id || '-' },
                { label: 'Page URL', value: detailModal.url || '-' },
                { label: 'Component', value: detailModal.component || '-' },
              ]}
              style={{ marginBottom: 16 }}
            />

            <div style={{ marginBottom: 12 }}>
              <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                Message
              </Typography.Text>
              <pre style={preStyle}>{detailModal.message || 'No message'}</pre>
            </div>

            {detailModal.stack && (
              <div style={{ marginBottom: 12 }}>
                <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                  Stack Trace
                </Typography.Text>
                <pre style={{ ...preStyle, maxHeight: 300, overflow: 'auto' }}>
                  {detailModal.stack}
                </pre>
              </div>
            )}

            {detailModal.metadata && (
              <div>
                <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                  Metadata
                </Typography.Text>
                <pre style={preStyle}>
                  {JSON.stringify(detailModal.metadata, null, 2)}
                </pre>
              </div>
            )}
          </>
        )}
      </Modal>
    </>
  )
}

// ============================================
// Application Logs Tab
// ============================================

function getLogLevelColor(level: string): string {
  switch (level) {
    case 'DEBUG': return 'gray'
    case 'INFO': return 'arcoblue'
    case 'SUCCESS': return 'green'
    case 'WARNING': return 'gold'
    case 'ERROR': return 'orange'
    case 'CRITICAL': return 'red'
    default: return 'gray'
  }
}

const LIVE_TAIL_MAX = 200

function ApplicationLogsTab({ start_date, end_date }: TabTimeRange) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<FilterCondition[]>([])
  const [detailModal, setDetailModal] = useState<AppLog | null>(null)

  // Live Tail state
  const [liveTail, setLiveTail] = useState(false)
  const [paused, setPaused] = useState(false)
  const [realtimeLogs, setRealtimeLogs] = useState<AppLog[]>([])
  const [newIds, setNewIds] = useState<Set<string>>(new Set())
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data, isLoading } = useAppLogs({
    page,
    pageSize: PAGE_SIZE,
    level: getFilterValue(filters, 'level'),
    module: getFilterValue(filters, 'module'),
    message: getFilterValue(filters, 'message'),
    start_date,
    end_date,
  })

  const logs = data?.data ?? []
  const total = data?.total ?? 0

  // Supabase Realtime subscription
  useEffect(() => {
    if (!liveTail) return

    setRealtimeLogs([])
    setNewIds(new Set())

    const channel = supabase
      .channel('live-tail-app-logs')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'application_logs' },
        (payload) => {
          const row = payload.new as AppLog
          setRealtimeLogs((prev) => {
            const next = [row, ...prev]
            return next.slice(0, LIVE_TAIL_MAX)
          })
          setNewIds((prev) => {
            const next = new Set(prev)
            next.add(row.id)
            return next
          })
          // Clear highlight after animation
          setTimeout(() => {
            setNewIds((prev) => {
              const next = new Set(prev)
              next.delete(row.id)
              return next
            })
          }, 2000)
        },
      )
      .subscribe()

    return () => {
      supabase.removeChannel(channel)
    }
  }, [liveTail])

  // Auto-scroll when not paused
  useEffect(() => {
    if (liveTail && !paused && scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [realtimeLogs, liveTail, paused])

  const handleToggleLiveTail = useCallback((checked: boolean) => {
    setLiveTail(checked)
    if (!checked) {
      setPaused(false)
      setRealtimeLogs([])
      setNewIds(new Set())
    }
  }, [])

  const displayLogs = liveTail ? realtimeLogs : logs

  const columns: ColumnProps<AppLog>[] = [
    {
      title: 'Time',
      dataIndex: 'logged_at',
      width: 180,
      render: (_, record) => (
        <Typography.Text style={{ fontSize: 13 }}>
          {formatDateTime(record.logged_at)}
        </Typography.Text>
      ),
    },
    {
      title: 'Level',
      dataIndex: 'level',
      width: 100,
      render: (_, record) => (
        <Tag color={getLogLevelColor(record.level)} size="small">
          {record.level}
        </Tag>
      ),
    },
    {
      title: 'Location',
      dataIndex: 'module',
      width: 280,
      render: (_, record) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }} ellipsis>
          {[record.module, record.function, record.line].filter(Boolean).join(':')}
        </Typography.Text>
      ),
    },
    {
      title: 'Message',
      dataIndex: 'message',
      render: (_, record) => {
        const shortModule = record.module
          ? record.module.split('.').pop() || record.module
          : ''
        return (
          <Space size={4}>
            {shortModule && (
              <Tag size="small" color="arcoblue" style={{ fontSize: 11, flexShrink: 0 }}>
                {shortModule}
              </Tag>
            )}
            <Typography.Text style={{ fontSize: 13 }} ellipsis>
              {record.message}
            </Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '',
      width: 48,
      render: (_, record) => (
        <Button
          type="text"
          size="mini"
          icon={<IconEye />}
          onClick={() => setDetailModal(record)}
        />
      ),
    },
  ]

  return (
    <>
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size="medium">
          <Space size={8}>
            <Switch
              checked={liveTail}
              onChange={handleToggleLiveTail}
              checkedText="Live"
              uncheckedText="Live"
            />
            {liveTail && (
              <>
                <Tag color="green" size="small">{realtimeLogs.length} entries</Tag>
                <Button
                  type="text"
                  size="mini"
                  icon={paused ? <IconPlayArrow /> : <IconPause />}
                  onClick={() => setPaused((p) => !p)}
                >
                  {paused ? 'Resume' : 'Pause'}
                </Button>
              </>
            )}
          </Space>
          {!liveTail && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <FilterBuilder
                fields={APP_LOG_FIELDS}
                filters={filters}
                onChange={(f) => { setFilters(f); setPage(1) }}
              />
              <Button
                icon={<IconExport />}
                size="small"
                style={{ flexShrink: 0, marginLeft: 16 }}
                onClick={() => exportToCsv(
                  `app-logs-${new Date().toISOString().slice(0, 10)}.csv`,
                  logs as unknown as Record<string, unknown>[],
                  [
                    { key: 'logged_at', label: 'Time' },
                    { key: 'level', label: 'Level' },
                    { key: 'module', label: 'Module' },
                    { key: 'function', label: 'Function' },
                    { key: 'message', label: 'Message' },
                    { key: 'exception', label: 'Exception' },
                  ],
                )}
              >
                Export
              </Button>
            </div>
          )}
        </Space>
      </Card>

      <Card>
        <div ref={scrollRef}>
          <style>{`
            @keyframes liveTailHighlight {
              from { background-color: var(--color-primary-1); }
              to { background-color: transparent; }
            }
            .live-tail-new-row td {
              animation: liveTailHighlight 2s ease-out;
            }
          `}</style>
          <Table
            rowKey="id"
            columns={columns}
            data={displayLogs}
            loading={!liveTail && isLoading}
            rowClassName={(record) => newIds.has(record.id) ? 'live-tail-new-row' : ''}
            pagination={liveTail ? false : {
              current: page,
              pageSize: PAGE_SIZE,
              total,
              onChange: setPage,
              showTotal: (t: number) => `Total ${t} entries`,
              sizeCanChange: false,
            }}
            noDataElement={<EmptyState description={liveTail ? 'Waiting for new logs...' : 'No application logs found'} />}
          />
        </div>
      </Card>

      <Modal
        title="Application Log Details"
        visible={!!detailModal}
        onCancel={() => setDetailModal(null)}
        footer={<Button onClick={() => setDetailModal(null)}>Close</Button>}
        style={{ width: 720 }}
      >
        {detailModal && (
          <>
            <Descriptions
              column={2}
              data={[
                { label: 'Time', value: formatDateTime(detailModal.logged_at) },
                {
                  label: 'Level',
                  value: (
                    <Tag color={getLogLevelColor(detailModal.level)}>
                      {detailModal.level}
                    </Tag>
                  ),
                },
                { label: 'Module', value: detailModal.module || '-' },
                { label: 'Function', value: detailModal.function || '-' },
                { label: 'Line', value: detailModal.line ?? '-' },
                { label: 'File', value: detailModal.file_path || '-' },
              ]}
              style={{ marginBottom: 16 }}
            />

            <div style={{ marginBottom: 12 }}>
              <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                Message
              </Typography.Text>
              <pre style={preStyle}>{detailModal.message}</pre>
            </div>

            {detailModal.exception && (
              <div style={{ marginBottom: 12 }}>
                <Typography.Text bold style={{ marginBottom: 8, display: 'block', color: 'var(--color-danger-6)' }}>
                  Exception
                </Typography.Text>
                <pre style={{ ...preStyle, borderLeft: '3px solid var(--color-danger-6)', maxHeight: 300, overflow: 'auto' }}>
                  {detailModal.exception}
                </pre>
              </div>
            )}

            {detailModal.extra && Object.keys(detailModal.extra).length > 0 && (
              <div>
                <Typography.Text bold style={{ marginBottom: 8, display: 'block' }}>
                  Extra
                </Typography.Text>
                <pre style={preStyle}>
                  {JSON.stringify(detailModal.extra, null, 2)}
                </pre>
              </div>
            )}
          </>
        )}
      </Modal>
    </>
  )
}

// ============================================
// Main Component
// ============================================

const preStyle: React.CSSProperties = {
  padding: 16,
  background: 'var(--color-fill-2)',
  borderRadius: 4,
  overflow: 'auto',
  fontSize: 13,
  fontFamily: 'monospace',
  margin: 0,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
}

export function RequestLogs() {
  const [period, setPeriod] = useState('24h')
  const [dateRange, setDateRange] = useState<[string, string] | null>(null)

  const timeRange = periodToDateRange(period, dateRange)

  return (
    <div>
      <PageHeader
        title="Request Logs"
        subtitle="View API request logs, frontend errors, and application logs"
        icon={<IconCode />}
        breadcrumb={['Logs & Monitoring', 'Request Logs']}
      />
      <Card style={{ marginBottom: 16 }}>
        <TimeRangeSelector
          period={period}
          onPeriodChange={setPeriod}
          dateRange={dateRange}
          onDateRangeChange={setDateRange}
        />
      </Card>
      <Tabs defaultActiveTab="requests" type="card-gutter">
        <Tabs.TabPane key="requests" title="Request Logs">
          <RequestLogsTab {...timeRange} />
        </Tabs.TabPane>
        <Tabs.TabPane key="errors" title="Frontend Logs">
          <FrontendErrorsTab {...timeRange} />
        </Tabs.TabPane>
        <Tabs.TabPane key="app-logs" title="Application Logs">
          <ApplicationLogsTab {...timeRange} />
        </Tabs.TabPane>
      </Tabs>
    </div>
  )
}
