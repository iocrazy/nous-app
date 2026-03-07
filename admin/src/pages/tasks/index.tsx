import { useState } from 'react'
import {
  Table,
  Input,
  Select,
  Tag,
  Card,
  Space,
  Typography,
  Statistic,
  Grid,
  Button,
  Tooltip,
  Progress,
  Message,
  Modal,
  Switch,
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
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import {
  useAdminTasks,
  useAdminTaskStats,
  useCancelTask,
  useRetryTask,
} from '../../api/endpoints/tasks'
import type { AdminTaskData } from '../../api/endpoints/tasks'
import { formatDateTime } from '../../utils/format'

const PAGE_SIZE = 20

const STATUS_OPTIONS = [
  { value: 'pending', label: 'Pending' },
  { value: 'processing', label: 'Processing' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const TASK_TYPE_OPTIONS = [
  { value: 'parse', label: 'Parse' },
  { value: 'download', label: 'Download' },
  { value: 'upload', label: 'Upload' },
  { value: 'transcode', label: 'Transcode' },
  { value: 'ai_pipeline', label: 'AI Pipeline' },
  { value: 'ai_extract', label: 'AI Extract' },
  { value: 'ai_transcription', label: 'AI Transcription' },
  { value: 'ai_summary', label: 'AI Summary' },
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

function formatSpeed(speed: number | null) {
  if (!speed) return ''
  if (speed < 1024) return `${speed} B/s`
  if (speed < 1024 * 1024) return `${(speed / 1024).toFixed(1)} KB/s`
  return `${(speed / 1024 / 1024).toFixed(1)} MB/s`
}

export function TaskCenter() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null)

  const { data, isLoading } = useAdminTasks(
    {
      page,
      pageSize: PAGE_SIZE,
      search,
      status: statusFilter,
      taskType: typeFilter,
    },
    autoRefresh,
  )
  const { data: stats } = useAdminTaskStats()
  const cancelTask = useCancelTask()
  const retryTask = useRetryTask()

  const items = data?.items ?? []
  const total = data?.total ?? 0

  const handleSearch = (value: string) => {
    setSearch(value)
    setPage(1)
  }

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

  const columns: ColumnProps<AdminTaskData>[] = [
    {
      title: 'Title',
      dataIndex: 'title',
      render: (_: unknown, record: AdminTaskData) => (
        <div>
          <Typography.Text ellipsis style={{ maxWidth: 260 }}>
            {record.title || 'Untitled'}
          </Typography.Text>
          {record.subtitle && (
            <>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                {record.subtitle}
              </Typography.Text>
            </>
          )}
          {record.error_msg && (
            <>
              <br />
              <Tooltip content={record.error_msg}>
                <Typography.Text type="error" style={{ fontSize: 12 }} ellipsis={{ rows: 1 }}>
                  {record.error_msg}
                </Typography.Text>
              </Tooltip>
            </>
          )}
        </div>
      ),
    },
    {
      title: 'User',
      dataIndex: 'user_email',
      width: 160,
      render: (value: string | null) => (
        <Typography.Text ellipsis style={{ maxWidth: 140 }}>
          {value || '-'}
        </Typography.Text>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'task_type',
      width: 120,
      render: (value: string) => (
        <Tag size="small" color={TYPE_TAG_COLORS[value] || 'gray'}>
          {value}
        </Tag>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 130,
      render: (value: string) => {
        const config = STATUS_TAG_CONFIG[value] || { color: 'gray', icon: null }
        return (
          <Tag icon={config.icon} color={config.color}>
            {value.charAt(0).toUpperCase() + value.slice(1)}
          </Tag>
        )
      },
    },
    {
      title: 'Progress',
      dataIndex: 'progress',
      width: 160,
      render: (_: unknown, record: AdminTaskData) => {
        if (record.status !== 'processing') return null
        return (
          <div>
            <Progress
              percent={record.progress}
              size="small"
              status="normal"
              style={{ width: 120 }}
            />
            {record.speed ? (
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {formatSpeed(record.speed)}
              </Typography.Text>
            ) : null}
          </div>
        )
      },
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 140,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: 'Actions',
      width: 90,
      align: 'center',
      render: (_: unknown, record: AdminTaskData) => {
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
                  onClick={() => handleCancel(record)}
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
                  onClick={() => handleRetry(record)}
                />
              </Tooltip>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 16 }}>
        Tasks
      </Typography.Title>

      {stats && (
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
      )}

      <Card style={{ marginBottom: 16 }}>
        <Space size="medium" wrap align="center">
          <Input.Search
            placeholder="Search by title..."
            onSearch={handleSearch}
            style={{ width: 260 }}
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
            style={{ width: 150 }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <Select.Option key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Option>
            ))}
          </Select>
          <Select
            placeholder="All Types"
            value={typeFilter}
            onChange={(value) => {
              setTypeFilter(value || undefined)
              setPage(1)
            }}
            allowClear
            style={{ width: 150 }}
          >
            {TASK_TYPE_OPTIONS.map((opt) => (
              <Select.Option key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Option>
            ))}
          </Select>
          <Space size="mini" align="center">
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
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
          scroll={{ x: 1100 }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: true,
            sizeCanChange: false,
          }}
          noDataElement="No tasks found"
        />
      </Card>
    </div>
  )
}
