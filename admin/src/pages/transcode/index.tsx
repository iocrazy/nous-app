import { useState, useEffect, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Tag,
  Card,
  Space,
  Typography,
  Modal,
  Message,
  Image,
  Statistic,
  Grid,
  Button,
  Tooltip,
} from '@arco-design/web-react'
import {
  IconRefresh,
  IconCheckCircle,
  IconCloseCircle,
  IconClockCircle,
  IconLoading,
  IconMinus,
  IconCopy,
} from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import {
  useTranscodeStats,
  useRetryTranscode,
  useBatchTranscode,
} from '../../api/endpoints/transcode'
import type { TranscodeVersionData } from '../../api/endpoints/transcode'
import { formatDate, formatBytes } from '../../utils/format'
import { supabase } from '../../auth/supabase'

const API_URL = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_URL || '')

const KNOWN_PLATFORMS = ['douyin', 'bilibili', 'youtube', 'tiktok', 'xiaohongshu', 'twitter']

function getCoverSrc(record: TranscodeVersionData): string | null {
  if (record.cover_download_path) {
    const path = record.cover_download_path.startsWith('/')
      ? record.cover_download_path
      : `/${record.cover_download_path}`
    return `${API_URL}/media${path}`
  }
  return record.cover_url || null
}

function TranscodeStatusTag({ status }: { status: string | null }) {
  const config: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
    completed: { color: 'green', icon: <IconCheckCircle />, label: 'Completed' },
    failed: { color: 'red', icon: <IconCloseCircle />, label: 'Failed' },
    pending: { color: 'orange', icon: <IconClockCircle />, label: 'Pending' },
    processing: { color: 'blue', icon: <IconLoading />, label: 'Processing' },
  }
  if (!status) {
    return (
      <Tag icon={<IconMinus />} color="gray">
        Not Transcoded
      </Tag>
    )
  }
  const { color, icon, label } = config[status] || { color: 'gray', icon: null, label: status }
  return (
    <Tag icon={icon} color={color}>
      {label}
    </Tag>
  )
}

function StatsCards() {
  const { data: stats } = useTranscodeStats()
  if (!stats) return null

  return (
    <Grid.Row gutter={16} style={{ marginBottom: 16 }}>
      <Grid.Col span={4}>
        <Card>
          <Statistic title="Total Videos" value={stats.total_video_versions} />
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
            title="Processing"
            value={stats.processing}
            styleValue={{ color: 'rgb(var(--blue-6))' }}
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
            title="Pending"
            value={stats.pending}
            styleValue={{ color: 'rgb(var(--orange-6))' }}
          />
        </Card>
      </Grid.Col>
      <Grid.Col span={4}>
        <Card>
          <Statistic
            title="Untranscoded"
            value={stats.not_transcoded}
            styleValue={{ color: 'var(--color-text-3)' }}
          />
        </Card>
      </Grid.Col>
    </Grid.Row>
  )
}

function CoverCell({ record }: { record: TranscodeVersionData }) {
  const src = getCoverSrc(record)
  const hasPlatformIcon =
    record.source_platform && KNOWN_PLATFORMS.includes(record.source_platform)

  return (
    <div style={{ position: 'relative', width: 60, height: 60 }}>
      {src ? (
        <Image
          src={src}
          width={60}
          height={60}
          style={{ objectFit: 'cover', borderRadius: 4 }}
        />
      ) : (
        <div
          style={{
            width: 60,
            height: 60,
            background: 'var(--color-fill-2)',
            borderRadius: 4,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--color-text-3)',
            fontSize: 12,
          }}
        >
          No cover
        </div>
      )}
      {hasPlatformIcon && (
        <div
          style={{
            position: 'absolute',
            top: 2,
            left: 2,
            background: 'rgba(0,0,0,0.6)',
            borderRadius: '50%',
            padding: 3,
            lineHeight: 0,
          }}
        >
          <img src={`/icons/${record.source_platform}.svg`} alt="" width={14} height={14} />
        </div>
      )}
    </div>
  )
}

function HlsTiersCell({ tiers }: { tiers: Record<string, boolean> | null }) {
  if (!tiers) {
    return <span style={{ color: 'var(--color-text-3)' }}>-</span>
  }
  return (
    <Space size="mini" wrap>
      {Object.entries(tiers).map(([tier, exists]) => (
        <Tag key={tier} size="small" color={exists ? 'green' : 'gray'} style={{ margin: 0 }}>
          {tier}
        </Tag>
      ))}
    </Space>
  )
}

function HlsPathCell({ path }: { path: string | null }) {
  if (!path) {
    return <span style={{ color: 'var(--color-text-3)' }}>-</span>
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(path).then(
      () => Message.success('Copied'),
      () => Message.error('Copy failed'),
    )
  }

  return (
    <Space size="mini">
      <Tooltip content={path}>
        <Typography.Text ellipsis style={{ maxWidth: 160, fontSize: 12 }}>
          {path}
        </Typography.Text>
      </Tooltip>
      <IconCopy
        style={{ cursor: 'pointer', color: 'var(--color-text-3)' }}
        onClick={handleCopy}
      />
    </Space>
  )
}

export function TranscodeList() {
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const { data: stats } = useTranscodeStats()
  const retryTranscode = useRetryTranscode()
  const batchTranscode = useBatchTranscode()

  // Supabase Realtime: auto-refresh on resource_versions changes
  useEffect(() => {
    const channel = supabase
      .channel('admin-transcode')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'resource_versions' },
        () => {
          queryClient.invalidateQueries({ queryKey: ['transcode'] })
        },
      )
      .subscribe()
    return () => {
      supabase.removeChannel(channel)
    }
  }, [queryClient])

  const handleRetry = (record: TranscodeVersionData) => {
    setRetryingId(record.id)
    retryTranscode.mutate(record.id, {
      onSuccess: () => Message.success('Transcode queued'),
      onError: (err) => Message.error(err.message),
      onSettled: () => setRetryingId(null),
    })
  }

  const handleBatchRetryFailed = () => {
    Modal.confirm({
      title: 'Retry All Failed',
      content: `This will retry all ${stats?.failed ?? 0} failed transcode tasks. Continue?`,
      onOk: () =>
        batchTranscode.mutateAsync('retry_failed', {
          onSuccess: (res: { queued?: number }) =>
            Message.success(`${res.queued ?? 0} transcode tasks queued`),
          onError: (err: Error) => Message.error(err.message),
        }),
    })
  }

  const handleBatchTranscodeNew = () => {
    Modal.confirm({
      title: 'Transcode New',
      content: `This will queue ${stats?.not_transcoded ?? 0} untranscoded video versions for transcoding. Continue?`,
      onOk: () =>
        batchTranscode.mutateAsync('transcode_new', {
          onSuccess: (res: { queued?: number }) =>
            Message.success(`${res.queued ?? 0} transcode tasks queued`),
          onError: (err: Error) => Message.error(err.message),
        }),
    })
  }

  const columns = useMemo<NotionColumnDef<TranscodeVersionData>[]>(
    () => [
      {
        key: 'cover_url',
        header: 'Cover',
        type: 'text',
        required: true,
        size: 80,
        cell: (row) => <CoverCell record={row} />,
      },
      {
        key: 'video_title',
        header: 'Title',
        type: 'text',
        filterable: true,
        sortable: true,
        required: true,
        minSize: 200,
        cell: (row) => (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Typography.Text ellipsis style={{ maxWidth: 220 }}>
                {row.video_title || 'Untitled'}
              </Typography.Text>
              <Tag size="small" color="arcoblue" style={{ flexShrink: 0 }}>
                v{row.version_number}
              </Tag>
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.filename || row.id.slice(0, 12)}
            </Typography.Text>
          </div>
        ),
      },
      {
        key: 'file_size_bytes',
        header: 'File Size',
        type: 'number',
        sortable: true,
        size: 100,
        cell: (row) => (
          <span style={{ fontWeight: row.file_size_bytes > 100 * 1024 * 1024 ? 700 : 400 }}>
            {formatBytes(row.file_size_bytes)}
          </span>
        ),
      },
      {
        key: 'transcode_status',
        header: 'Status',
        type: 'select',
        filterable: true,
        sortable: true,
        size: 140,
        filterOptions: [
          { label: 'Completed', value: 'completed' },
          { label: 'Processing', value: 'processing' },
          { label: 'Failed', value: 'failed' },
          { label: 'Pending', value: 'pending' },
          { label: 'Not Transcoded', value: 'null' },
        ],
        cell: (row) => <TranscodeStatusTag status={row.transcode_status} />,
      },
      {
        key: 'hls_tiers',
        header: 'HLS Tiers',
        type: 'text',
        size: 180,
        cell: (row) => <HlsTiersCell tiers={row.hls_tiers} />,
      },
      {
        key: 'hls_path',
        header: 'HLS Path',
        type: 'text',
        size: 200,
        cell: (row) => <HlsPathCell path={row.hls_path} />,
      },
      {
        key: 'created_at',
        header: 'Created',
        type: 'date',
        filterable: true,
        sortable: true,
        size: 120,
        cell: (row) => formatDate(row.created_at),
      },
      {
        key: 'transcode_at',
        header: 'Completed',
        type: 'date',
        sortable: true,
        size: 120,
        cell: (row) => formatDate(row.transcode_at),
      },
      {
        key: 'actions',
        header: 'Actions',
        type: 'text',
        required: true,
        size: 80,
        cell: (row) => {
          const canRetry = !row.transcode_status || row.transcode_status === 'failed'
          const label = row.transcode_status === 'failed' ? 'Retry Transcode' : 'Start Transcode'
          return canRetry ? (
            <Tooltip content={label}>
              <Button
                type="text"
                icon={<IconRefresh />}
                size="small"
                loading={retryingId === row.id}
                onClick={() => handleRetry(row)}
              />
            </Tooltip>
          ) : null
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [retryingId],
  )

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
  } = useNotionTable<TranscodeVersionData>({
    tableKey: 'transcode',
    columns,
    defaultSorts: [{ field: 'created_at', direction: 'desc' }],
    fetchData: async ({ page, pageSize, filters, sorts, search }) => {
      const statusFilter = filters.find((f) => f.field === 'transcode_status')
      const sortBy = sorts[0]?.field === 'created_at' ? 'resource_id' : sorts[0]?.field
      const sortOrder = sorts[0]?.direction

      const { data } = await apiClient.get('/api/v1/admin/transcode', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(statusFilter?.value && { status: statusFilter.value }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  const batchButtons = (
    <Space size="mini">
      <Button
        type="outline"
        status="danger"
        loading={batchTranscode.isPending}
        onClick={handleBatchRetryFailed}
        disabled={!stats?.failed}
      >
        Retry All Failed
      </Button>
      <Button
        type="outline"
        loading={batchTranscode.isPending}
        onClick={handleBatchTranscodeNew}
        disabled={!stats?.not_transcoded}
      >
        Transcode New
      </Button>
    </Space>
  )

  return (
    <NotionTable<TranscodeVersionData>
      table={table}
      toolbarProps={toolbarProps}
      pagination={pagination}
      onPageChange={setPage}
      isLoading={isLoading}
      title="Transcode"
      headerContent={<StatsCards />}
      toolbarExtra={batchButtons}
      emptyText="No video versions found"
      scrollX={1200}
    />
  )
}
