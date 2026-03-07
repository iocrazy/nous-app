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
  Dropdown,
  Menu,
  Descriptions,
  Spin,
} from '@arco-design/web-react'
import {
  IconMore,
  IconDelete,
  IconRefresh,
  IconEye,
  IconStorage,
  IconCheckCircle,
  IconCloseCircle,
  IconClockCircle,
  IconLoading,
  IconMinus,
} from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import {
  useVideos,
  useVideoDetail,
  useVideoStats,
  useDeleteVideo,
  useRetryVideo,
} from '../../api/endpoints/videos'
import type { VideoData } from '../../api/endpoints/videos'

const PAGE_SIZE = 20

const STATUS_OPTIONS = [
  { value: 'pending', label: 'Pending' },
  { value: 'downloading', label: 'Downloading' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'skipped', label: 'Skipped' },
]

const TYPE_OPTIONS = [
  { value: '0', label: 'Video' },
  { value: '2', label: 'Carousel' },
  { value: '4', label: 'Special Video' },
  { value: '61', label: 'Special Variant' },
  { value: '68', label: 'Image-text' },
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

const API_URL = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_URL || '')

const PLATFORM_ICONS: Record<string, { label: string; color: string }> = {
  douyin: { label: 'DY', color: '#000000' },
  bilibili: { label: 'B', color: '#00A1D6' },
  youtube: { label: 'YT', color: '#FF0000' },
  tiktok: { label: 'TT', color: '#010101' },
  twitter: { label: 'X', color: '#1DA1F2' },
  xiaohongshu: { label: 'XHS', color: '#FF2442' },
}

function getCoverSrc(record: VideoData): string | null {
  if (record.cover_download_path) {
    const path = record.cover_download_path.startsWith('/')
      ? record.cover_download_path
      : `/${record.cover_download_path}`
    return `${API_URL}/media${path}`
  }
  return record.cover_url || null
}

function StatusTag({ status }: { status: string }) {
  const config: Record<string, { color: string; icon: React.ReactNode }> = {
    completed: { color: 'green', icon: <IconCheckCircle /> },
    failed: { color: 'red', icon: <IconCloseCircle /> },
    pending: { color: 'orange', icon: <IconClockCircle /> },
    downloading: { color: 'blue', icon: <IconLoading /> },
    skipped: { color: 'gray', icon: <IconMinus /> },
  }
  const { color, icon } = config[status] || { color: 'gray', icon: null }
  return (
    <Tag icon={icon} color={color}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Tag>
  )
}

function TypeTag({ type }: { type: string | null }) {
  if (type === null) return <span>-</span>
  const label = TYPE_OPTIONS.find((t) => t.value === type)?.label || `Type ${type}`
  const colorMap: Record<string, string> = {
    '0': 'arcoblue',
    '2': 'purple',
    '68': 'cyan',
  }
  return <Tag color={colorMap[type] || 'gray'}>{label}</Tag>
}

export function VideoList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined)
  const [detailId, setDetailId] = useState<number | null>(null)

  const { data, isLoading } = useVideos({
    page,
    pageSize: PAGE_SIZE,
    search,
    status: statusFilter,
    awemeType: typeFilter,
  })
  const { data: stats } = useVideoStats()
  const { data: detail, isLoading: detailLoading } = useVideoDetail(detailId)
  const deleteVideo = useDeleteVideo()
  const retryVideo = useRetryVideo()

  const videos = data?.items ?? []
  const total = data?.total ?? 0

  const handleSearch = (value: string) => {
    setSearch(value)
    setPage(1)
  }

  const handleDelete = (video: VideoData) => {
    Modal.confirm({
      title: 'Delete Video',
      content: `Are you sure you want to delete "${video.video_title || video.aweme_id}"?`,
      okButtonProps: { status: 'danger' },
      onOk: () =>
        deleteVideo.mutateAsync(video.id, {
          onSuccess: () => Message.success('Video deleted'),
        }),
    })
  }

  const handleRetry = (video: VideoData) => {
    retryVideo.mutate(video.id, {
      onSuccess: () => Message.success('Download retry queued'),
    })
  }

  const columns: ColumnProps<VideoData>[] = [
    {
      title: 'Cover',
      dataIndex: 'cover_url',
      width: 80,
      render: (_: unknown, record: VideoData) => {
        const src = getCoverSrc(record)
        const platform = record.source_platform
          ? PLATFORM_ICONS[record.source_platform]
          : null
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
            {platform && (
              <span
                style={{
                  position: 'absolute',
                  top: 2,
                  right: 2,
                  background: platform.color,
                  color: '#fff',
                  fontSize: 9,
                  fontWeight: 700,
                  padding: '1px 3px',
                  borderRadius: 3,
                  lineHeight: 1.2,
                  opacity: 0.9,
                }}
              >
                {platform.label}
              </span>
            )}
          </div>
        )
      },
    },
    {
      title: 'Title',
      dataIndex: 'video_title',
      render: (_: unknown, record: VideoData) => (
        <div>
          <Typography.Text ellipsis style={{ maxWidth: 240 }}>
            {record.video_title || 'Untitled'}
          </Typography.Text>
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.aweme_id}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Author',
      dataIndex: 'author',
      width: 120,
      render: (value: string | null) => value || '-',
    },
    {
      title: 'Type',
      dataIndex: 'aweme_type',
      width: 110,
      render: (value: string | null) => <TypeTag type={value} />,
    },
    {
      title: 'Status',
      dataIndex: 'video_download_status',
      width: 120,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: 'Size',
      dataIndex: 'video_datasize_bytes',
      width: 90,
      render: (value: number) => formatBytes(value),
    },
    {
      title: 'User',
      dataIndex: 'user_email',
      width: 160,
      render: (value: string | null) => (
        <Typography.Text ellipsis style={{ maxWidth: 140, fontSize: 12 }}>
          {value || '-'}
        </Typography.Text>
      ),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 120,
      render: (value: string) => formatDate(value),
    },
    {
      title: 'Actions',
      width: 80,
      align: 'center',
      render: (_: unknown, record: VideoData) => (
        <Dropdown
          droplist={
            <Menu>
              <Menu.Item key="detail" onClick={() => setDetailId(record.id)}>
                <Space>
                  <IconEye />
                  View Details
                </Space>
              </Menu.Item>
              {(record.video_download_status === 'failed' || record.video_download_status === 'pending') && (
                <Menu.Item key="retry" onClick={() => handleRetry(record)}>
                  <Space>
                    <IconRefresh />
                    Retry Download
                  </Space>
                </Menu.Item>
              )}
              <Menu.Item
                key="delete"
                onClick={() => handleDelete(record)}
                style={{ color: 'rgb(var(--danger-6))' }}
              >
                <Space>
                  <IconDelete />
                  Delete
                </Space>
              </Menu.Item>
            </Menu>
          }
          position="br"
        >
          <IconMore style={{ cursor: 'pointer', fontSize: 18 }} />
        </Dropdown>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 16 }}>
        Videos
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
                title="Pending"
                value={stats.pending}
                styleValue={{ color: 'rgb(var(--orange-6))' }}
              />
            </Card>
          </Grid.Col>
          <Grid.Col span={4}>
            <Card>
              <Statistic
                title="Downloading"
                value={stats.downloading}
                styleValue={{ color: 'rgb(var(--blue-6))' }}
              />
            </Card>
          </Grid.Col>
          <Grid.Col span={4}>
            <Card>
              <Statistic
                title="Storage"
                value={formatBytes(stats.total_storage_bytes)}
                prefix={<IconStorage />}
              />
            </Card>
          </Grid.Col>
        </Grid.Row>
      )}

      <Card style={{ marginBottom: 16 }}>
        <Space size="medium">
          <Input.Search
            placeholder="Search by title or aweme_id..."
            onSearch={handleSearch}
            style={{ width: 320 }}
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
            {TYPE_OPTIONS.map((opt) => (
              <Select.Option key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Option>
            ))}
          </Select>
        </Space>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={videos}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: true,
            sizeCanChange: false,
          }}
          noDataElement="No videos found"
        />
      </Card>

      <Modal
        title="Video Details"
        visible={detailId !== null}
        onCancel={() => setDetailId(null)}
        footer={null}
        style={{ width: 720 }}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : detail ? (
          <div>
            {(() => {
              const detailCoverSrc = detail.cover_download_path
                ? `${API_URL}/media${detail.cover_download_path.startsWith('/') ? detail.cover_download_path : `/${detail.cover_download_path}`}`
                : detail.cover_url
              return detailCoverSrc ? (
                <div style={{ marginBottom: 16, textAlign: 'center' }}>
                  <Image src={detailCoverSrc} width={320} style={{ borderRadius: 8 }} />
                </div>
              ) : null
            })()}
            <Descriptions
              column={2}
              data={[
                { label: 'ID', value: detail.id },
                { label: 'Aweme ID', value: detail.aweme_id },
                { label: 'Title', value: detail.video_title || '-', span: 2 },
                { label: 'Author', value: detail.author || '-' },
                { label: 'Type', value: <TypeTag type={detail.aweme_type} /> },
                {
                  label: 'Download Status',
                  value: <StatusTag status={detail.video_download_status} />,
                },
                { label: 'Size', value: formatBytes(detail.video_datasize_bytes) },
                { label: 'Duration', value: detail.video_duration || '-' },
                { label: 'User', value: detail.user_email || detail.user_id || '-' },
                { label: 'Likes', value: detail.video_digg_count.toLocaleString() },
                { label: 'Comments', value: detail.video_comment_count.toLocaleString() },
                { label: 'Shares', value: detail.video_share_count.toLocaleString() },
                { label: 'Collects', value: detail.video_collect_count.toLocaleString() },
                { label: 'Views', value: detail.view_count.toLocaleString() },
                { label: 'Music', value: detail.music_name || '-' },
                { label: 'Hashtags', value: detail.video_hashtag_name || '-' },
                {
                  label: 'Music Status',
                  value: <StatusTag status={detail.music_download_status} />,
                },
                {
                  label: 'Cover Status',
                  value: <StatusTag status={detail.cover_download_status} />,
                },
                { label: 'Keep Forever', value: detail.keep_forever ? 'Yes' : 'No' },
                { label: 'Created', value: formatDate(detail.created_at) },
              ]}
              style={{ marginBottom: 16 }}
            />
            {detail.error_message && (
              <Card title="Error Message" style={{ marginTop: 16 }}>
                <Typography.Text type="error">{detail.error_message}</Typography.Text>
              </Card>
            )}
            {detail.video_desc && (
              <Card title="Description" style={{ marginTop: 16 }}>
                <Typography.Paragraph>{detail.video_desc}</Typography.Paragraph>
              </Card>
            )}
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
