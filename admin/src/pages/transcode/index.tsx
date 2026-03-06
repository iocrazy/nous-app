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

function formatDate(dateStr: string | null) {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function formatBytes(bytes: number) {
  if (bytes === 0) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return `${size.toFixed(1)} ${units[i]}`
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

  const { data, isLoading } = useTranscodeList({
    page,
    pageSize: PAGE_SIZE,
    search,
    status: statusFilter,
    minSizeMb: sizeFilter ? parseInt(sizeFilter) : undefined,
  })
  const { data: stats } = useTranscodeStats()
  const retryTranscode = useRetryTranscode()
  const batchTranscode = useBatchTranscode()

  const items = data?.items ?? []
  const total = data?.total ?? 0

  const handleSearch = (value: string) => {
    setSearch(value)
    setPage(1)
  }

  const handleRetry = (record: TranscodeVersionData) => {
    retryTranscode.mutate(record.id, {
      onSuccess: () => Message.success('Transcode retry queued'),
      onError: (err) => Message.error(err.message),
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
      render: (value: string | null) =>
        value ? (
          <Image src={value} width={60} height={60} style={{ objectFit: 'cover', borderRadius: 4 }} />
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
        ),
    },
    {
      title: 'Title',
      dataIndex: 'video_title',
      render: (_: unknown, record: TranscodeVersionData) => (
        <div>
          <Typography.Text ellipsis style={{ maxWidth: 240 }}>
            {record.video_title || 'Untitled'}
          </Typography.Text>
          <br />
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
      title: 'Completed',
      dataIndex: 'transcode_at',
      width: 140,
      render: (value: string | null) => formatDate(value),
    },
    {
      title: 'Actions',
      width: 80,
      align: 'center',
      render: (_: unknown, record: TranscodeVersionData) => {
        const canRetry = !record.transcode_status || record.transcode_status === 'failed'
        return canRetry ? (
          <Tooltip content="Retry Transcode">
            <Button
              type="text"
              icon={<IconRefresh />}
              size="small"
              loading={retryTranscode.isPending}
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
        </Space>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={items}
          loading={isLoading}
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
