import { useState } from 'react'
import {
  Table,
  Input,
  Select,
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
  Switch,
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
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import {
  useTranscodeList,
  useTranscodeStats,
  useRetryTranscode,
  useBatchTranscode,
} from '../../api/endpoints/transcode'
import type { TranscodeVersionData } from '../../api/endpoints/transcode'
import { formatDate, formatBytes } from '../../utils/format'

const PAGE_SIZE = 20

const STATUS_OPTIONS = [
  { value: 'completed', label: 'Completed' },
  { value: 'processing', label: 'Processing' },
  { value: 'failed', label: 'Failed' },
  { value: 'pending', label: 'Pending' },
  { value: 'null', label: 'Not Transcoded' },
]

const SIZE_OPTIONS = [
  { value: '100', label: '> 100 MB' },
  { value: '500', label: '> 500 MB' },
  { value: '1024', label: '> 1 GB' },
]

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

export function TranscodeList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [sizeFilter, setSizeFilter] = useState<string | undefined>(undefined)
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)

  const { data, isLoading } = useTranscodeList({
    page,
    pageSize: PAGE_SIZE,
    search,
    status: statusFilter,
    minSizeMb: sizeFilter ? parseInt(sizeFilter) : undefined,
    sortBy: 'resource_id',
    sortOrder: 'desc',
  }, autoRefresh)
  const { data: stats } = useTranscodeStats(autoRefresh)
  const retryTranscode = useRetryTranscode()
  const batchTranscode = useBatchTranscode()

  const items = data?.items ?? []
  const total = data?.total ?? 0

  const handleSearch = (value: string) => {
    setSearch(value)
    setPage(1)
  }

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

  const handleCopyPath = (path: string) => {
    navigator.clipboard.writeText(path).then(
      () => Message.success('Copied'),
      () => Message.error('Copy failed'),
    )
  }

  const columns: ColumnProps<TranscodeVersionData>[] = [
    {
      title: 'Cover',
      dataIndex: 'cover_url',
      width: 80,
      render: (_: unknown, record: TranscodeVersionData) => {
        const src = getCoverSrc(record)
        const hasPlatformIcon = record.source_platform && KNOWN_PLATFORMS.includes(record.source_platform)
        return (
          <div style={{ position: 'relative', width: 60, height: 60 }}>
            {src ? (
              <Image src={src} width={60} height={60} style={{ objectFit: 'cover', borderRadius: 4 }} />
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
      },
    },
    {
      title: 'Title',
      dataIndex: 'video_title',
      render: (_: unknown, record: TranscodeVersionData) => (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Typography.Text ellipsis style={{ maxWidth: 220 }}>
              {record.video_title || 'Untitled'}
            </Typography.Text>
            <Tag size="small" color="arcoblue" style={{ flexShrink: 0 }}>
              v{record.version_number}
            </Tag>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.filename || record.id.slice(0, 12)}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'File Size',
      dataIndex: 'file_size_bytes',
      width: 100,
      render: (value: number) => (
        <span style={{ fontWeight: value > 100 * 1024 * 1024 ? 700 : 400 }}>
          {formatBytes(value)}
        </span>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'transcode_status',
      width: 140,
      render: (value: string | null) => <TranscodeStatusTag status={value} />,
    },
    {
      title: 'HLS Tiers',
      dataIndex: 'hls_tiers',
      width: 180,
      render: (_: unknown, record: TranscodeVersionData) => {
        if (!record.hls_tiers) {
          return <span style={{ color: 'var(--color-text-3)' }}>-</span>
        }
        return (
          <Space size="mini" wrap>
            {Object.entries(record.hls_tiers).map(([tier, exists]) => (
              <Tag
                key={tier}
                size="small"
                color={exists ? 'green' : 'gray'}
                style={{ margin: 0 }}
              >
                {tier}
              </Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: 'HLS Path',
      dataIndex: 'hls_path',
      width: 200,
      render: (value: string | null) =>
        value ? (
          <Space size="mini">
            <Tooltip content={value}>
              <Typography.Text ellipsis style={{ maxWidth: 160, fontSize: 12 }}>
                {value}
              </Typography.Text>
            </Tooltip>
            <IconCopy
              style={{ cursor: 'pointer', color: 'var(--color-text-3)' }}
              onClick={() => handleCopyPath(value)}
            />
          </Space>
        ) : (
          <span style={{ color: 'var(--color-text-3)' }}>-</span>
        ),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 120,
      render: (value: string | null) => formatDate(value),
    },
    {
      title: 'Completed',
      dataIndex: 'transcode_at',
      width: 120,
      render: (value: string | null) => formatDate(value),
    },
    {
      title: 'Actions',
      width: 80,
      align: 'center',
      render: (_: unknown, record: TranscodeVersionData) => {
        const canRetry = !record.transcode_status || record.transcode_status === 'failed'
        const label = record.transcode_status === 'failed' ? 'Retry Transcode' : 'Start Transcode'
        return canRetry ? (
          <Tooltip content={label}>
            <Button
              type="text"
              icon={<IconRefresh />}
              size="small"
              loading={retryingId === record.id}
              onClick={() => handleRetry(record)}
            />
          </Tooltip>
        ) : null
      },
    },
  ]

  return (
    <div>
      <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 16 }}>
        Transcode
      </Typography.Title>

      {stats && (
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
      )}

      <Card style={{ marginBottom: 16 }}>
        <Space size="medium" wrap>
          <Input.Search
            placeholder="Search by title or filename..."
            onSearch={handleSearch}
            style={{ width: 280 }}
            allowClear
          />
          <Select
            placeholder="All Statuses"
            value={statusFilter}
            onChange={(value) => {
              setStatusFilter(value || undefined)
              setPage(1)
            }}
            allowClear
            style={{ width: 160 }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <Select.Option key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Option>
            ))}
          </Select>
          <Select
            placeholder="Any Size"
            value={sizeFilter}
            onChange={(value) => {
              setSizeFilter(value || undefined)
              setPage(1)
            }}
            allowClear
            style={{ width: 130 }}
          >
            {SIZE_OPTIONS.map((opt) => (
              <Select.Option key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Option>
            ))}
          </Select>
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
          <Space size="mini">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Auto Refresh
            </Typography.Text>
            <Switch checked={autoRefresh} onChange={setAutoRefresh} size="small" />
          </Space>
        </Space>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={items}
          loading={isLoading}
          scroll={{ x: 1200 }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: true,
            sizeCanChange: false,
          }}
          noDataElement="No video versions found"
        />
      </Card>
    </div>
  )
}
