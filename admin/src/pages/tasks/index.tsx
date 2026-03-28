import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Tag,
  Card,
  Grid,
  Statistic,
  Typography,
  Progress,
  Tooltip,
  Button,
  Space,
  Message,
  Modal,
  Descriptions,
} from '@arco-design/web-react'
import {
  IconCheckCircle,
  IconCloseCircle,
  IconClockCircle,
  IconLoading,
  IconStop,
  IconRefresh,
  IconMinus,
} from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import type { AdminTaskData } from '../../api/endpoints/tasks'
import { useAdminTaskStats, useCancelTask, useRetryTask } from '../../api/endpoints/tasks'
import { formatDateTime } from '../../utils/format'
import { supabase } from '../../auth/supabase'

// --- Constants ---

const STATUS_FILTER_OPTIONS = [
  { label: 'Pending', value: 'pending' },
  { label: 'Processing', value: 'processing' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
  { label: 'Cancelled', value: 'cancelled' },
]

const TYPE_FILTER_OPTIONS = [
  { label: 'Parse', value: 'parse' },
  { label: 'Download', value: 'download' },
  { label: 'Upload', value: 'upload' },
  { label: 'Transcode', value: 'transcode' },
  { label: 'AI Pipeline', value: 'ai_pipeline' },
  { label: 'AI Extract', value: 'ai_extract' },
  { label: 'AI Transcription', value: 'ai_transcription' },
  { label: 'AI Summary', value: 'ai_summary' },
]

const STATUS_TAG_CONFIG: Record<string, { color: string; icon: React.ReactNode }> = {
  pending: { color: 'orange', icon: <IconClockCircle /> },
  processing: { color: 'blue', icon: <IconLoading /> },
  completed: { color: 'green', icon: <IconCheckCircle /> },
  failed: { color: 'red', icon: <IconCloseCircle /> },
  cancelled: { color: 'gray', icon: <IconMinus /> },
}

const TYPE_TAG_COLORS: Record<string, string> = {
  parse: 'purple',
  download: 'blue',
  upload: 'cyan',
  transcode: 'orangered',
  ai_pipeline: 'magenta',
  ai_extract: 'green',
  ai_transcription: 'lime',
  ai_summary: 'gold',
}

// --- Helpers ---

function formatSpeed(speed: number | null): string {
  if (!speed) return ''
  if (speed < 1024) return `${speed} B/s`
  if (speed < 1024 * 1024) return `${(speed / 1024).toFixed(1)} KB/s`
  return `${(speed / 1024 / 1024).toFixed(1)} MB/s`
}

function formatRuntime(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return '-'
  const end = completedAt ? new Date(completedAt) : new Date()
  const diffMs = end.getTime() - new Date(startedAt).getTime()
  if (diffMs < 0) return '-'
  const totalSec = diffMs / 1000
  if (totalSec < 60) return `${totalSec.toFixed(1)}s`
  const min = Math.floor(totalSec / 60)
  const sec = Math.round(totalSec % 60)
  return `${min}m ${sec}s`
}

// --- Sub-components ---

function StatsCards() {
  const { data: stats } = useAdminTaskStats()
  if (!stats) return null

  return (
    <Grid.Row gutter={16} style={{ marginBottom: 16 }}>
      <Grid.Col span={4}>
        <Card>
          <Statistic title="Total" value={stats.total} />
        </Card>
      </Grid.Col>
      <Grid.Col span={4}>
        <Card>
          <Statistic
            title="Processing"
            value={stats.processing}
            styleValue={{ color: 'rgb(var(--blue-6))' }}
          />
        </Card>
      </Grid.Col>
      <Grid.Col span={4}>
        <Card>
          <Statistic
            title="Pending"
            value={stats.pending}
            styleValue={{ color: 'rgb(var(--orange-6))' }}
          />
        </Card>
      </Grid.Col>
      <Grid.Col span={4}>
        <Card>
          <Statistic
            title="Completed"
            value={stats.completed}
            styleValue={{ color: 'rgb(var(--green-6))' }}
          />
        </Card>
      </Grid.Col>
      <Grid.Col span={4}>
        <Card>
          <Statistic
            title="Failed"
            value={stats.failed}
            styleValue={{ color: 'rgb(var(--red-6))' }}
          />
        </Card>
      </Grid.Col>
      <Grid.Col span={4}>
        <Card>
          <Statistic
            title="Cancelled"
            value={stats.cancelled}
            styleValue={{ color: 'var(--color-text-3)' }}
          />
        </Card>
      </Grid.Col>
    </Grid.Row>
  )
}

const ellipsisStyle: React.CSSProperties = {
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap' as const,
}

function TitleCell({ record }: { record: AdminTaskData }) {
  return (
    <div style={{ lineHeight: 1.4 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ ...ellipsisStyle, maxWidth: 220, flexShrink: 0 }}>
          {record.title || 'Untitled'}
        </span>
        {record.subtitle && (
          <span style={{ ...ellipsisStyle, fontSize: 11, color: 'var(--color-text-3)', minWidth: 0 }}>
            {record.subtitle}
          </span>
        )}
      </div>
      {record.error_msg && (
        <Tooltip content={record.error_msg}>
          <div style={{ ...ellipsisStyle, fontSize: 11, color: 'rgb(var(--red-6))', marginTop: 1 }}>
            {record.error_msg}
          </div>
        </Tooltip>
      )}
    </div>
  )
}

function ProgressCell({ record }: { record: AdminTaskData }) {
  if (record.status !== 'processing') return null
  return (
    <div>
      <Progress percent={record.progress} size="small" status="normal" style={{ width: 120 }} />
      {record.speed ? (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {formatSpeed(record.speed)}
        </Typography.Text>
      ) : null}
    </div>
  )
}

function ActionsCell({
  record,
  actionLoadingId,
  onCancel,
  onRetry,
}: {
  record: AdminTaskData
  actionLoadingId: string | null
  onCancel: (record: AdminTaskData) => void
  onRetry: (record: AdminTaskData) => void
}) {
  const isLoading = actionLoadingId === record.id
  const canCancel = record.status === 'pending' || record.status === 'processing'
  const canRetry = record.status === 'failed'

  return (
    <Space size="mini">
      {canCancel && (
        <Tooltip content="Cancel">
          <Button
            type="text"
            icon={<IconStop />}
            size="small"
            status="danger"
            loading={isLoading}
            onClick={() => onCancel(record)}
          />
        </Tooltip>
      )}
      {canRetry && (
        <Tooltip content="Retry">
          <Button
            type="text"
            icon={<IconRefresh />}
            size="small"
            loading={isLoading}
            onClick={() => onRetry(record)}
          />
        </Tooltip>
      )}
    </Space>
  )
}

// --- Main component ---

export function TaskCenter() {
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const cancelTask = useCancelTask()
  const retryTask = useRetryTask()

  // Supabase Realtime: auto-refresh on unified_tasks changes (debounced)
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const debouncedInvalidate = useCallback(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    }, 1000)
  }, [queryClient])

  useEffect(() => {
    const channel = supabase
      .channel('admin-tasks')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'unified_tasks' },
        debouncedInvalidate,
      )
      .subscribe()
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
      supabase.removeChannel(channel)
    }
  }, [debouncedInvalidate])

  const handleCancel = (record: AdminTaskData) => {
    Modal.confirm({
      title: 'Cancel Task',
      content: `Cancel task "${record.title}"?`,
      onOk: () => {
        setActionLoadingId(record.id)
        cancelTask.mutate(record.id, {
          onSuccess: () => Message.success('Task cancelled'),
          onError: (err) => Message.error(err.message),
          onSettled: () => setActionLoadingId(null),
        })
      },
    })
  }

  const handleRetry = (record: AdminTaskData) => {
    setActionLoadingId(record.id)
    retryTask.mutate(record.id, {
      onSuccess: () => Message.success('Task reset to pending'),
      onError: (err) => Message.error(err.message),
      onSettled: () => setActionLoadingId(null),
    })
  }

  const columns = useMemo<NotionColumnDef<AdminTaskData>[]>(
    () => [
      {
        key: 'title',
        header: 'Title',
        type: 'text',
        filterable: true,
        sortable: true,
        required: true,
        minSize: 200,
        cell: (row) => <TitleCell record={row} />,
      },
      {
        key: 'user_email',
        header: 'User',
        type: 'text',
        filterable: true,
        size: 160,
        cell: (row) => (
          <Typography.Text ellipsis style={{ maxWidth: 140 }}>
            {row.user_email || '-'}
          </Typography.Text>
        ),
      },
      {
        key: 'task_type',
        header: 'Type',
        type: 'select',
        filterable: true,
        size: 120,
        filterOptions: TYPE_FILTER_OPTIONS,
        cell: (row) => (
          <Tag size="small" color={TYPE_TAG_COLORS[row.task_type] || 'gray'}>
            {row.task_type}
          </Tag>
        ),
      },
      {
        key: 'status',
        header: 'Status',
        type: 'select',
        filterable: true,
        sortable: true,
        size: 130,
        filterOptions: STATUS_FILTER_OPTIONS,
        cell: (row) => {
          const config = STATUS_TAG_CONFIG[row.status] || { color: 'gray', icon: null }
          return (
            <Tag icon={config.icon} color={config.color}>
              {row.status.charAt(0).toUpperCase() + row.status.slice(1)}
            </Tag>
          )
        },
      },
      {
        key: 'runtime',
        header: 'Runtime',
        type: 'text',
        size: 90,
        cell: (row) => {
          const isRunning = row.status === 'processing'
          return (
            <Typography.Text type={isRunning ? undefined : 'secondary'} style={{ fontSize: 12 }}>
              {formatRuntime(row.started_at, row.completed_at)}
            </Typography.Text>
          )
        },
      },
      {
        key: 'progress',
        header: 'Progress',
        type: 'number',
        size: 160,
        cell: (row) => <ProgressCell record={row} />,
      },
      {
        key: 'created_at',
        header: 'Created',
        type: 'date',
        filterable: true,
        sortable: true,
        size: 140,
        cell: (row) => formatDateTime(row.created_at),
      },
      {
        key: 'actions',
        header: 'Actions',
        type: 'text',
        required: true,
        size: 90,
        cell: (row) => (
          <ActionsCell
            record={row}
            actionLoadingId={actionLoadingId}
            onCancel={handleCancel}
            onRetry={handleRetry}
          />
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [actionLoadingId],
  )

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
  } = useNotionTable<AdminTaskData>({
    tableKey: 'tasks',
    columns,
    defaultSorts: [{ field: 'created_at', direction: 'desc' }],
    fetchData: async ({ page, pageSize, filters, sorts, search }) => {
      const statusFilter = filters.find((f) => f.field === 'status')
      const typeFilter = filters.find((f) => f.field === 'task_type')
      const sortBy = sorts[0]?.field
      const sortOrder = sorts[0]?.direction

      const { data } = await apiClient.get('/api/v1/admin/tasks', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(statusFilter?.value && { status: statusFilter.value }),
          ...(typeFilter?.value && { task_type: typeFilter.value }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  const expandedRowRender = (row: AdminTaskData) => {
    const statusConfig = STATUS_TAG_CONFIG[row.status] || { color: 'gray', icon: null }
    const meta = row.metadata || {}
    const descData = [
      { label: 'Task ID', value: <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{row.id}</span> },
      { label: 'Type', value: (
        <Tag size="small" color={TYPE_TAG_COLORS[row.task_type] || 'gray'}>{row.task_type}</Tag>
      )},
      { label: 'Status', value: (
        <Tag icon={statusConfig.icon} color={statusConfig.color}>{row.status}</Tag>
      )},
      { label: 'User', value: row.user_email || '-' },
      { label: 'Phase', value: row.phase || '-' },
      { label: 'Runtime', value: formatRuntime(row.started_at, row.completed_at) },
      { label: 'Created', value: formatDateTime(row.created_at) },
      { label: 'Started', value: row.started_at ? formatDateTime(row.started_at) : '-' },
      { label: 'Completed', value: row.completed_at ? formatDateTime(row.completed_at) : '-' },
      { label: 'Celery Task ID', value: <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{row.celery_task_id || '-'}</span> },
      { label: 'Resource ID', value: row.resource_id || '-' },
      { label: 'Media ID', value: row.media_id || '-' },
    ]
    // Add metadata fields for parse tasks
    if (meta.original_url) {
      descData.push({ label: 'Original URL', value: <span style={{ fontSize: 12, wordBreak: 'break-all' }}>{meta.original_url as string}</span> })
    }
    if (meta.parse_method) {
      const methodLabels: Record<string, string> = { ytdlp: 'yt-dlp', lightweight: 'Lightweight', drissionpage: 'DrissionPage' }
      descData.push({ label: 'Parse Method', value: (
        <Tag size="small" color="cyan">{methodLabels[meta.parse_method as string] || (meta.parse_method as string)}</Tag>
      )})
    }

    return (
      <div style={{ padding: '12px 16px' }}>
        <Descriptions column={3} size="small" data={descData} />
        {row.subtitle && (
          <div style={{ marginTop: 8 }}>
            <Typography.Text bold style={{ fontSize: 12 }}>Subtitle: </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.subtitle}</Typography.Text>
          </div>
        )}
        {row.error_msg && (
          <div style={{ marginTop: 8 }}>
            <Typography.Text bold style={{ fontSize: 12 }}>Error: </Typography.Text>
            <Typography.Text type="error" style={{ fontSize: 12 }}>{row.error_msg}</Typography.Text>
            {row.error_code && (
              <Tag size="small" color="red" style={{ marginLeft: 8 }}>{row.error_code}</Tag>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <NotionTable<AdminTaskData>
      table={table}
      toolbarProps={toolbarProps}
      pagination={pagination}
      onPageChange={setPage}
      isLoading={isLoading}
      title="Tasks"
      headerContent={<StatsCards />}
      emptyText="No tasks found"
      scrollX={1100}
      expandedRowRender={expandedRowRender}
    />
  )
}
