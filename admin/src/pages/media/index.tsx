import { useState, useMemo } from 'react'
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
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import type { VideoData, VideoDetailData } from '../../api/endpoints/videos'
import {
  useVideoStats,
  useVideoDetail,
  useDeleteVideo,
  useRetryVideo,
} from '../../api/endpoints/videos'
import { formatDateTime, formatBytes } from '../../utils/format'

const API_URL = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_URL || '')

const KNOWN_PLATFORMS = ['douyin', 'bilibili', 'youtube', 'tiktok', 'xiaohongshu', 'twitter']

const STATUS_FILTER_OPTIONS = [
  { label: 'Pending', value: 'pending' },
  { label: 'Downloading', value: 'downloading' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
  { label: 'Skipped', value: 'skipped' },
]

function getCoverSrc(record: VideoData): string | null {
  // Prefer CDN URL (no auth needed, avoids cross-origin ORB blocking)
  if (record.cover_url) return record.cover_url
  if (record.cover_download_path) {
    const path = record.cover_download_path.startsWith('/')
      ? record.cover_download_path
      : `/${record.cover_download_path}`
    return `${API_URL}/media${path}`
  }
  return null
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

function StatsCards() {
  const { data: stats } = useVideoStats()
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
  )
}

function DetailModal({
  detailId,
  onClose,
}: {
  detailId: number | null
  onClose: () => void
}) {
  const { data: detail, isLoading: detailLoading } = useVideoDetail(detailId)

  return (
    <Modal
      title="Video Details"
      visible={detailId !== null}
      onCancel={onClose}
      footer={null}
      style={{ width: 720 }}
    >
      {detailLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : detail ? (
        <DetailContent detail={detail} />
      ) : null}
    </Modal>
  )
}

function DetailContent({ detail }: { detail: VideoDetailData }) {
  const detailCoverSrc = detail.cover_download_path
    ? `${API_URL}/media${detail.cover_download_path.startsWith('/') ? detail.cover_download_path : `/${detail.cover_download_path}`}`
    : detail.cover_url

  return (
    <div>
      {detailCoverSrc && (
        <div style={{ marginBottom: 16, textAlign: 'center' }}>
          <Image src={detailCoverSrc} width={320} style={{ borderRadius: 8 }} />
        </div>
      )}
      <Descriptions
        column={2}
        data={[
          { label: 'ID', value: detail.id },
          { label: 'Aweme ID', value: detail.aweme_id },
          { label: 'Title', value: detail.video_title || '-', span: 2 },
          { label: 'Author', value: detail.author || '-' },
          {
            label: 'Platform',
            value: detail.source_platform
              ? detail.source_platform.charAt(0).toUpperCase() + detail.source_platform.slice(1)
              : '-',
          },
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
          { label: 'Created', value: formatDateTime(detail.created_at) },
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
  )
}

function CoverCell({ record }: { record: VideoData }) {
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

function ActionsCell({
  record,
  onViewDetail,
  onRetry,
  onDelete,
  isRetrying,
}: {
  record: VideoData
  onViewDetail: () => void
  onRetry: () => void
  onDelete: () => void
  isRetrying: boolean
}) {
  const canRetry =
    record.video_download_status === 'failed' || record.video_download_status === 'pending'

  return (
    <Dropdown
      droplist={
        <Menu>
          <Menu.Item key="detail" onClick={onViewDetail}>
            <Space>
              <IconEye />
              View Details
            </Space>
          </Menu.Item>
          {canRetry && (
            <Menu.Item key="retry" onClick={onRetry} disabled={isRetrying}>
              <Space>
                {isRetrying ? <IconLoading /> : <IconRefresh />}
                Retry Download
              </Space>
            </Menu.Item>
          )}
          <Menu.Item
            key="delete"
            onClick={onDelete}
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
  )
}

export function MediaList() {
  const [detailId, setDetailId] = useState<number | null>(null)
  const deleteVideo = useDeleteVideo()
  const retryVideo = useRetryVideo()

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

  const columns = useMemo<NotionColumnDef<VideoData>[]>(
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
            <Typography.Text ellipsis style={{ maxWidth: 240 }}>
              {row.video_title || 'Untitled'}
            </Typography.Text>
            <br />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.aweme_id}
            </Typography.Text>
          </div>
        ),
      },
      {
        key: 'author',
        header: 'Author',
        type: 'text',
        filterable: true,
        sortable: true,
        size: 120,
        cell: (row) => row.author || '-',
      },
      {
        key: 'video_download_status',
        header: 'Status',
        type: 'select',
        filterable: true,
        sortable: true,
        size: 120,
        filterOptions: STATUS_FILTER_OPTIONS,
        cell: (row) => <StatusTag status={row.video_download_status} />,
      },
      {
        key: 'video_datasize_bytes',
        header: 'Size',
        type: 'number',
        sortable: true,
        size: 90,
        cell: (row) => formatBytes(row.video_datasize_bytes),
      },
      {
        key: 'user_email',
        header: 'User',
        type: 'text',
        filterable: true,
        size: 160,
        cell: (row) => (
          <Typography.Text ellipsis style={{ maxWidth: 140, fontSize: 12 }}>
            {row.user_email || '-'}
          </Typography.Text>
        ),
      },
      {
        key: 'created_at',
        header: 'Created',
        type: 'date',
        filterable: true,
        sortable: true,
        size: 160,
        cell: (row) => formatDateTime(row.created_at),
      },
      {
        key: 'actions',
        header: 'Actions',
        type: 'text',
        required: true,
        size: 80,
        cell: (row) => (
          <ActionsCell
            record={row}
            onViewDetail={() => setDetailId(row.id)}
            onRetry={() => handleRetry(row)}
            onDelete={() => handleDelete(row)}
            isRetrying={retryVideo.variables === row.id && retryVideo.isPending}
          />
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [retryVideo.variables, retryVideo.isPending],
  )

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
  } = useNotionTable<VideoData>({
    tableKey: 'admin_media',
    columns,
    defaultSorts: [{ field: 'created_at', direction: 'desc' }],
    fetchData: async ({ page, pageSize, filters, sorts, search }) => {
      const statusFilter = filters.find((f) => f.field === 'video_download_status')
      const platformFilter = filters.find((f) => f.field === 'source_platform')

      const sortBy = sorts[0]?.field
      const sortOrder = sorts[0]?.direction

      const { data } = await apiClient.get('/api/v1/admin/videos', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(statusFilter?.value && { status: statusFilter.value }),
          ...(platformFilter?.value && { platform: platformFilter.value }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  return (
    <div>
      <NotionTable<VideoData>
        table={table}
        toolbarProps={toolbarProps}
        pagination={pagination}
        onPageChange={setPage}
        isLoading={isLoading}
        title="Media"
        headerContent={<StatsCards />}
        emptyText="No media found"
        scrollX={1200}
      />
      <DetailModal detailId={detailId} onClose={() => setDetailId(null)} />
    </div>
  )
}
