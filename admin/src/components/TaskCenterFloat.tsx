import { useState, useEffect, useRef, useCallback, Component, type ReactNode } from 'react'
import {
  Drawer,
  Typography,
  Tag,
  Progress,
  Empty,
  Space,
  Button,
  Message,
} from '@arco-design/web-react'
import {
  IconList,
  IconCheckCircle,
  IconCloseCircle,
  IconClockCircle,
  IconLoading,
  IconStop,
  IconDelete,
} from '@arco-design/web-react/icon'
import { supabase } from '../auth/supabase'
import { apiClient } from '../api/client'

interface UnifiedTask {
  id: string
  user_id: string
  task_type: string
  status: string
  title: string
  subtitle: string | null
  progress: number
  speed: number | null
  total_bytes: number | null
  error_msg: string | null
  created_at: string
  updated_at: string | null
}

const TASK_TYPE_COLORS: Record<string, string> = {
  parse: 'purple',
  download: 'blue',
  upload: 'cyan',
  transcode: 'orange',
  ai_pipeline: 'magenta',
  ai_extract: 'green',
  ai_transcription: 'lime',
  ai_summary: 'gold',
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'completed':
      return <IconCheckCircle style={{ color: 'rgb(var(--green-6))' }} />
    case 'failed':
      return <IconCloseCircle style={{ color: 'rgb(var(--red-6))' }} />
    case 'processing':
      return <IconLoading style={{ color: 'rgb(var(--blue-6))' }} />
    case 'pending':
      return <IconClockCircle style={{ color: 'rgb(var(--orange-6))' }} />
    case 'cancelled':
      return <IconStop style={{ color: 'var(--color-text-3)' }} />
    default:
      return null
  }
}

function formatSpeed(bytesPerSec: number | null): string {
  if (!bytesPerSec || bytesPerSec <= 0) return ''
  if (bytesPerSec < 1024) return `${bytesPerSec} B/s`
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`
  return `${(bytesPerSec / 1024 / 1024).toFixed(1)} MB/s`
}

const DEBOUNCE_MS = 500

export function TaskCenterFloat() {
  const [visible, setVisible] = useState(false)
  const [tasks, setTasks] = useState<UnifiedTask[]>([])
  const [connected, setConnected] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchTasks = useCallback(async () => {
    try {
      const { data } = await apiClient.get('/api/v1/admin/tasks', {
        params: {
          page: 1,
          page_size: 50,
          sort_by: 'created_at',
          sort_order: 'desc',
        },
      })
      setTasks(data.items || [])
    } catch {
      // silent
    }
  }, [])

  // Debounced refetch: batch rapid Supabase events into a single API call
  const debouncedFetch = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      fetchTasks()
    }, DEBOUNCE_MS)
  }, [fetchTasks])

  useEffect(() => {
    fetchTasks()

    // Single wildcard listener (same pattern as Tasks page)
    const channel = supabase
      .channel('admin-task-float')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'task_tracking' },
        () => {
          debouncedFetch()
        },
      )
      .subscribe((status) => {
        setConnected(status === 'SUBSCRIBED')
        if (status === 'SUBSCRIBED') {
          fetchTasks()
        }
      })

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      supabase.removeChannel(channel)
    }
  }, [fetchTasks, debouncedFetch])

  const activeTasks = tasks.filter(
    (t) => t.status === 'processing' || t.status === 'pending',
  )
  const recentTasks = tasks.slice(0, 30)
  const activeCount = activeTasks.length

  const handleCancel = async (taskId: string) => {
    try {
      await apiClient.post(`/api/v1/admin/tasks/${taskId}/cancel`)
      Message.success('Task cancelled')
    } catch (err) {
      Message.error((err as Error).message)
    }
  }

  return (
    <>
      <div
        style={{
          cursor: 'pointer',
          fontSize: 18,
          display: 'flex',
          alignItems: 'center',
          padding: '4px 8px',
          borderRadius: 4,
          position: 'relative',
        }}
        onClick={() => setVisible(true)}
      >
        <IconList />
        {activeCount > 0 && (
          <span
            style={{
              position: 'absolute',
              top: 2,
              right: 4,
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: 'rgb(var(--red-6))',
            }}
          />
        )}
      </div>

      <Drawer
        title={
          <Space>
            <span>Task Center</span>
            {!connected && (
              <Tag size="small" color="red">Disconnected</Tag>
            )}
            {activeCount > 0 && (
              <Tag size="small" color="blue">{activeCount} active</Tag>
            )}
          </Space>
        }
        visible={visible}
        onCancel={() => setVisible(false)}
        footer={null}
        width={440}
        placement="right"
        unmountOnExit
      >
        {recentTasks.length === 0 ? (
          <Empty description="No tasks" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            {recentTasks.map((task) => (
              <div
                key={task.id}
                style={{
                  padding: '10px 0',
                  borderBottom: '1px solid var(--color-fill-3)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <StatusIcon status={task.status} />
                  <Typography.Text ellipsis style={{ flex: 1, fontSize: 13 }}>
                    {task.title || 'Untitled'}
                  </Typography.Text>
                  <Tag size="small" color={TASK_TYPE_COLORS[task.task_type] || 'gray'}>
                    {task.task_type}
                  </Tag>
                </div>
                {task.subtitle && (
                  <Typography.Text
                    type="secondary"
                    ellipsis
                    style={{ fontSize: 12, paddingLeft: 22 }}
                  >
                    {task.subtitle}
                  </Typography.Text>
                )}
                {task.status === 'processing' && (
                  <div style={{ paddingLeft: 22, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Progress
                      percent={task.progress}
                      size="small"
                      style={{ flex: 1 }}
                      showText={false}
                    />
                    <Typography.Text style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                      {task.progress}%
                      {task.speed ? ` · ${formatSpeed(task.speed)}` : ''}
                    </Typography.Text>
                  </div>
                )}
                {task.status === 'failed' && task.error_msg && (
                  <Typography.Text
                    type="error"
                    ellipsis
                    style={{ fontSize: 11, paddingLeft: 22 }}
                  >
                    {task.error_msg}
                  </Typography.Text>
                )}
                {(task.status === 'processing' || task.status === 'pending') && (
                  <div style={{ paddingLeft: 22 }}>
                    <Button
                      type="text"
                      size="mini"
                      status="danger"
                      icon={<IconDelete />}
                      onClick={() => handleCancel(task.id)}
                    >
                      Cancel
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Drawer>
    </>
  )
}

/** Error boundary so TaskCenterFloat never crashes the whole page. */
interface EBState { hasError: boolean }
class TaskCenterErrorBoundary extends Component<{ children: ReactNode }, EBState> {
  state: EBState = { hasError: false }
  static getDerivedStateFromError() { return { hasError: true } }
  componentDidCatch(error: Error) {
    console.warn('[TaskCenterFloat] caught error, hiding widget:', error.message)
  }
  render() {
    return this.state.hasError ? null : this.props.children
  }
}

export function SafeTaskCenterFloat() {
  return (
    <TaskCenterErrorBoundary>
      <TaskCenterFloat />
    </TaskCenterErrorBoundary>
  )
}
