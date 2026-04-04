import { useState, useMemo, useEffect, useRef, useCallback } from 'react'
import {
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
import {
  IconEye,
  IconCopy,
  IconExport,
  IconPause,
  IconPlayArrow,
} from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import type { RequestLog, FrontendError, AppLog } from '../../api/endpoints/request-logs'
import { exportToCsv } from '../../utils/csv-export'
import { formatDateTime } from '../../utils/format'
import { supabase } from '../../auth/supabase'
import '../../components/notion-table/notion-table.css'

// ============================================
// Helpers
// ============================================

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

// ============================================
// Date filter helper
// ============================================

function buildDateParams(
  filters: { field: string; operator: string; value: string | string[] | number | boolean | null }[],
  dateField: string,
): Record<string, string> {
  const dateFilter = filters.find((f) => f.field === dateField)
  if (!dateFilter) return {}

  const now = new Date()
  if (dateFilter.operator === 'last_7_days') {
    return { start_date: new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString() }
  }
  if (dateFilter.operator === 'last_30_days') {
    return { start_date: new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString() }
  }
  if (dateFilter.operator === 'after' && dateFilter.value) {
    return { start_date: new Date(dateFilter.value as string).toISOString() }
  }
  if (dateFilter.operator === 'before' && dateFilter.value) {
    return { end_date: new Date(dateFilter.value as string).toISOString() }
  }
  return {}
}

// ============================================
// Request Logs Tab
// ============================================

function RequestLogsTab() {
  const [detailModal, setDetailModal] = useState<RequestLog | null>(null)

  const columns = useMemo<NotionColumnDef<RequestLog>[]>(() => [
    {
      key: 'timestamp',
      header: 'Time',
      type: 'date',
      filterable: true,
      sortable: true,
      required: true,
      size: 180,
      cell: (row) => (
        <Typography.Text style={{ fontSize: 13 }}>
          {formatDateTime(row.timestamp)}
        </Typography.Text>
      ),
    },
    {
      key: 'method',
      header: 'Method',
      type: 'select',
      filterable: true,
      size: 90,
      filterOptions: [
        { value: 'GET', label: 'GET' },
        { value: 'POST', label: 'POST' },
        { value: 'PUT', label: 'PUT' },
        { value: 'PATCH', label: 'PATCH' },
        { value: 'DELETE', label: 'DELETE' },
      ],
      cell: (row) => (
        <Tag color={getMethodColor(row.method)} size="small">{row.method}</Tag>
      ),
    },
    {
      key: 'path',
      header: 'Path',
      type: 'text',
      filterable: true,
      required: true,
      minSize: 200,
      cell: (row) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 13 }} ellipsis>
          {row.path}
        </Typography.Text>
      ),
    },
    {
      key: 'user_email',
      header: 'User',
      type: 'text',
      filterable: true,
      size: 180,
      cell: (row) => (
        <div>
          <div>{row.user_email || '-'}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.auth_type}
          </Typography.Text>
        </div>
      ),
    },
    {
      key: 'status_code',
      header: 'Status',
      type: 'select',
      filterable: true,
      size: 80,
      filterOptions: [
        { value: '2xx', label: '2xx Success' },
        { value: '4xx', label: '4xx Client Error' },
        { value: '5xx', label: '5xx Server Error' },
      ],
      cell: (row) => (
        <Tag color={getStatusColor(row.status_code)} size="small">
          {row.status_code ?? '-'}
        </Tag>
      ),
    },
    {
      key: 'response_time_ms',
      header: 'Time',
      type: 'number',
      sortable: true,
      size: 90,
      cell: (row) => (
        <Typography.Text
          style={{
            fontFamily: 'monospace',
            fontSize: 13,
            color: (row.response_time_ms ?? 0) > 1000 ? 'var(--color-danger-6)' : undefined,
          }}
        >
          {formatResponseTime(row.response_time_ms)}
        </Typography.Text>
      ),
    },
    {
      key: 'ip_address',
      header: 'IP',
      type: 'text',
      size: 130,
      cell: (row) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }}>
          {row.ip_address || '-'}
        </Typography.Text>
      ),
    },
    {
      key: 'actions',
      header: '',
      type: 'text',
      required: true,
      size: 48,
      cell: (row) => (
        <Button
          type="text"
          size="mini"
          icon={<IconEye />}
          onClick={(e) => { e.stopPropagation(); setDetailModal(row) }}
        />
      ),
    },
  ], [])

  const { table, toolbarProps, pagination, isLoading, setPage } = useNotionTable<RequestLog>({
    tableKey: 'request-logs',
    columns,
    defaultSorts: [{ field: 'timestamp', direction: 'desc' }],
    defaultPageSize: 50,
    fetchData: async ({ page, pageSize, filters, search }) => {
      const methodFilter = filters.find((f) => f.field === 'method')
      const statusFilter = filters.find((f) => f.field === 'status_code')
      const dateParams = buildDateParams(filters, 'timestamp')

      const { data } = await apiClient.get('/api/v1/admin/logs', {
        params: {
          page,
          pageSize,
          ...(search && { path: search }),
          ...(methodFilter?.value && { method: methodFilter.value }),
          ...(statusFilter?.value && { status_group: statusFilter.value }),
          ...dateParams,
        },
      })
      return { items: data.data, total: data.total }
    },
  })

  const exportButton = (
    <Button
      icon={<IconExport />}
      size="small"
      onClick={() => exportToCsv(
        `request-logs-${new Date().toISOString().slice(0, 10)}.csv`,
        table.getRowModel().rows.map((r) => r.original) as unknown as Record<string, unknown>[],
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
  )

  return (
    <>
      <NotionTable<RequestLog>
        table={table}
        toolbarProps={toolbarProps}
        pagination={pagination}
        onPageChange={setPage}
        isLoading={isLoading}
        toolbarExtra={exportButton}
        emptyText="No request logs found"
        scrollX={1100}
      />

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

function FrontendErrorsTab() {
  const [detailModal, setDetailModal] = useState<FrontendError | null>(null)

  const columns = useMemo<NotionColumnDef<FrontendError>[]>(() => [
    {
      key: 'created_at',
      header: 'Time',
      type: 'date',
      filterable: true,
      sortable: true,
      required: true,
      size: 180,
      cell: (row) => (
        <Typography.Text style={{ fontSize: 13 }}>
          {formatDateTime(row.created_at)}
        </Typography.Text>
      ),
    },
    {
      key: 'error_type',
      header: 'Type',
      type: 'select',
      filterable: true,
      size: 160,
      filterOptions: [
        { value: 'runtime', label: 'Runtime' },
        { value: 'network', label: 'Network' },
        { value: 'unhandled_rejection', label: 'Unhandled Rejection' },
      ],
      cell: (row) => (
        <Tag
          color={row.error_type === 'runtime' ? 'red' : row.error_type === 'network' ? 'orange' : 'purple'}
          size="small"
        >
          {row.error_type}
        </Tag>
      ),
    },
    {
      key: 'message',
      header: 'Message',
      type: 'text',
      filterable: true,
      required: true,
      cell: (row) => (
        <Typography.Text style={{ fontSize: 13 }} ellipsis>
          {row.message || '-'}
        </Typography.Text>
      ),
    },
    {
      key: 'url',
      header: 'Page',
      type: 'text',
      size: 200,
      cell: (row) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }} ellipsis>
          {row.url
            ? (() => { try { return new URL(row.url).pathname } catch { return row.url } })()
            : '-'}
        </Typography.Text>
      ),
    },
    {
      key: 'user_email',
      header: 'User',
      type: 'text',
      size: 160,
      cell: (row) => row.user_email || '-',
    },
    {
      key: 'actions',
      header: '',
      type: 'text',
      required: true,
      size: 48,
      cell: (row) => (
        <Button
          type="text"
          size="mini"
          icon={<IconEye />}
          onClick={(e) => { e.stopPropagation(); setDetailModal(row) }}
        />
      ),
    },
  ], [])

  const { table, toolbarProps, pagination, isLoading, setPage } = useNotionTable<FrontendError>({
    tableKey: 'frontend-errors',
    columns,
    defaultSorts: [{ field: 'created_at', direction: 'desc' }],
    defaultPageSize: 50,
    fetchData: async ({ page, pageSize, filters }) => {
      const errorTypeFilter = filters.find((f) => f.field === 'error_type')
      const dateParams = buildDateParams(filters, 'created_at')

      const { data } = await apiClient.get('/api/v1/admin/logs/frontend-errors', {
        params: {
          page,
          pageSize,
          ...(errorTypeFilter?.value && { error_type: errorTypeFilter.value }),
          ...dateParams,
        },
      })
      return { items: data.data, total: data.total }
    },
  })

  const exportButton = (
    <Button
      icon={<IconExport />}
      size="small"
      onClick={() => exportToCsv(
        `frontend-errors-${new Date().toISOString().slice(0, 10)}.csv`,
        table.getRowModel().rows.map((r) => r.original) as unknown as Record<string, unknown>[],
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
  )

  return (
    <>
      <NotionTable<FrontendError>
        table={table}
        toolbarProps={toolbarProps}
        pagination={pagination}
        onPageChange={setPage}
        isLoading={isLoading}
        toolbarExtra={exportButton}
        emptyText="No frontend errors found"
      />

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
                    <Tag
                      color={detailModal.error_type === 'runtime' ? 'red' : detailModal.error_type === 'network' ? 'orange' : 'purple'}
                    >
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
// Application Logs Tab (Hybrid: NotionTable + Live Tail)
// ============================================

function ApplicationLogsTab() {
  const [detailModal, setDetailModal] = useState<AppLog | null>(null)

  // Live Tail state
  const [liveTail, setLiveTail] = useState(false)
  const [paused, setPaused] = useState(false)
  const [realtimeLogs, setRealtimeLogs] = useState<AppLog[]>([])
  const [newIds, setNewIds] = useState<Set<string>>(new Set())
  const scrollRef = useRef<HTMLDivElement>(null)

  const columns = useMemo<NotionColumnDef<AppLog>[]>(() => [
    {
      key: 'logged_at',
      header: 'Time',
      type: 'date',
      filterable: true,
      sortable: true,
      required: true,
      size: 180,
      cell: (row) => (
        <Typography.Text style={{ fontSize: 13 }}>
          {formatDateTime(row.logged_at)}
        </Typography.Text>
      ),
    },
    {
      key: 'level',
      header: 'Level',
      type: 'select',
      filterable: true,
      size: 100,
      filterOptions: [
        { value: 'DEBUG', label: 'DEBUG' },
        { value: 'INFO', label: 'INFO' },
        { value: 'SUCCESS', label: 'SUCCESS' },
        { value: 'WARNING', label: 'WARNING' },
        { value: 'ERROR', label: 'ERROR' },
        { value: 'CRITICAL', label: 'CRITICAL' },
      ],
      cell: (row) => (
        <Tag color={getLogLevelColor(row.level)} size="small">{row.level}</Tag>
      ),
    },
    {
      key: 'module',
      header: 'Location',
      type: 'text',
      filterable: true,
      size: 200,
      cell: (row) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }} ellipsis>
          {[row.module, row.function, row.line].filter(Boolean).join(':')}
        </Typography.Text>
      ),
    },
    {
      key: 'message',
      header: 'Message',
      type: 'text',
      filterable: true,
      required: true,
      size: 600,
      minSize: 300,
      cell: (row) => {
        const shortModule = row.module
          ? row.module.split('.').pop() || row.module
          : ''
        return (
          <Space size={4} style={{ flexWrap: 'nowrap', maxWidth: '100%' }}>
            {shortModule && (
              <Tag size="small" color="arcoblue" style={{ fontSize: 11, flexShrink: 0 }}>
                {shortModule}
              </Tag>
            )}
            <Typography.Text style={{ fontSize: 13, wordBreak: 'break-all' }} ellipsis={{ rows: 2 }}>
              {row.message}
            </Typography.Text>
          </Space>
        )
      },
    },
    {
      key: 'actions',
      header: '',
      type: 'text',
      required: true,
      size: 48,
      cell: (row) => (
        <Button
          type="text"
          size="mini"
          icon={<IconEye />}
          onClick={(e) => { e.stopPropagation(); setDetailModal(row) }}
        />
      ),
    },
  ], [])

  const { table, toolbarProps, pagination, isLoading, setPage } = useNotionTable<AppLog>({
    tableKey: 'app-logs',
    columns,
    defaultSorts: [{ field: 'logged_at', direction: 'desc' }],
    defaultPageSize: 50,
    fetchData: async ({ page, pageSize, filters }) => {
      const levelFilter = filters.find((f) => f.field === 'level')
      const moduleFilter = filters.find((f) => f.field === 'module')
      const dateParams = buildDateParams(filters, 'logged_at')

      const { data } = await apiClient.get('/api/v1/admin/logs/app-logs', {
        params: {
          page,
          pageSize,
          ...(levelFilter?.value && { level: levelFilter.value }),
          ...(moduleFilter?.value && { module: moduleFilter.value }),
          ...dateParams,
        },
      })
      return { items: data.data, total: data.total }
    },
  })

  // Supabase Realtime subscription for Live Tail
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
          setRealtimeLogs((prev) => [row, ...prev].slice(0, LIVE_TAIL_MAX))
          setNewIds((prev) => {
            const next = new Set(prev)
            next.add(row.id)
            return next
          })
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

  // Build toolbar extra: Live tail toggle + export button (when not in live tail)
  const liveTailControls = (
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
      {!liveTail && (
        <Button
          icon={<IconExport />}
          size="small"
          onClick={() => exportToCsv(
            `app-logs-${new Date().toISOString().slice(0, 10)}.csv`,
            table.getRowModel().rows.map((r) => r.original) as unknown as Record<string, unknown>[],
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
      )}
    </Space>
  )

  // Detail modal (shared between both modes)
  const detailModalElement = (
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
  )

  // When liveTail is on, render a simple live tail view instead of NotionTable
  if (liveTail) {
    return (
      <>
        <Card>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
            {liveTailControls}
          </div>
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
            <div className="notion-table-wrapper">
              <table className="notion-table" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ width: 180 }}>Time</th>
                    <th style={{ width: 100 }}>Level</th>
                    <th style={{ width: 280 }}>Location</th>
                    <th>Message</th>
                    <th style={{ width: 48 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {realtimeLogs.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-3)' }}>
                        Waiting for new logs...
                      </td>
                    </tr>
                  ) : (
                    realtimeLogs.map((log) => (
                      <tr key={log.id} className={newIds.has(log.id) ? 'live-tail-new-row' : ''}>
                        <td>
                          <Typography.Text style={{ fontSize: 13 }}>
                            {formatDateTime(log.logged_at)}
                          </Typography.Text>
                        </td>
                        <td>
                          <Tag color={getLogLevelColor(log.level)} size="small">{log.level}</Tag>
                        </td>
                        <td>
                          <Typography.Text style={{ fontFamily: 'monospace', fontSize: 12 }} ellipsis>
                            {[log.module, log.function, log.line].filter(Boolean).join(':')}
                          </Typography.Text>
                        </td>
                        <td>
                          <Space size={4}>
                            {log.module && (
                              <Tag size="small" color="arcoblue" style={{ fontSize: 11 }}>
                                {log.module.split('.').pop()}
                              </Tag>
                            )}
                            <Typography.Text style={{ fontSize: 13 }} ellipsis>
                              {log.message}
                            </Typography.Text>
                          </Space>
                        </td>
                        <td>
                          <Button type="text" size="mini" icon={<IconEye />} onClick={() => setDetailModal(log)} />
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
        {detailModalElement}
      </>
    )
  }

  return (
    <>
      <NotionTable<AppLog>
        table={table}
        toolbarProps={toolbarProps}
        pagination={pagination}
        onPageChange={setPage}
        isLoading={isLoading}
        toolbarExtra={liveTailControls}
        emptyText="No application logs found"
      />
      {detailModalElement}
    </>
  )
}

// ============================================
// Main Component
// ============================================

export function RequestLogs() {
  return (
    <div>
      <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 16 }}>
        Logs
      </Typography.Title>
      <Tabs defaultActiveTab="requests" type="card-gutter" lazyload destroyOnHide>
        <Tabs.TabPane key="requests" title="Request Logs">
          <RequestLogsTab />
        </Tabs.TabPane>
        <Tabs.TabPane key="errors" title="Frontend Logs">
          <FrontendErrorsTab />
        </Tabs.TabPane>
        <Tabs.TabPane key="app-logs" title="Application Logs">
          <ApplicationLogsTab />
        </Tabs.TabPane>
      </Tabs>
    </div>
  )
}
