import { useState } from 'react'
import {
  Table,
  Tag,
  Select,
  Modal,
  Typography,
  Button,
  Card,
  Space,
  Tabs,
  Switch,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Message,
  Tooltip,
} from '@arco-design/web-react'
import {
  IconPlus,
  IconEdit,
  IconDelete,
  IconCheck,
  IconMute,
  IconPlayArrow,
  IconRefresh,
  IconExport,
  IconNotification,
} from '@arco-design/web-react/icon'
import { exportToCsv } from '../../utils/csv-export'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import {
  useAlertRules,
  useCreateAlertRule,
  useUpdateAlertRule,
  useDeleteAlertRule,
  useMuteAlertRule,
  useUnmuteAlertRule,
  useAlertHistory,
  useResolveAlert,
  useCheckAlerts,
  type AlertRule,
  type AlertRuleCreate,
  type AlertHistory,
} from '../../api/endpoints/alerts'
import { TimeRangeSelector, periodToDateRange } from '../../components/TimeRangeSelector'
import { PageHeader } from '../../components/PageHeader'
import { EmptyState } from '../../components/EmptyState'

const FormItem = Form.Item

const METRIC_TYPES = [
  { value: 'error_rate', label: 'Error Rate (%)' },
  { value: 'avg_response_time', label: 'Avg Response Time (ms)' },
  { value: 'error_count', label: 'App Error Count' },
  { value: 'log_level_count', label: 'Critical Log Count' },
]

const CONDITIONS = [
  { value: 'gt', label: '>' },
  { value: 'gte', label: '>=' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '<=' },
  { value: 'eq', label: '=' },
]

const MUTE_DURATIONS = [
  { value: 30, label: '30 minutes' },
  { value: 60, label: '1 hour' },
  { value: 240, label: '4 hours' },
  { value: 480, label: '8 hours' },
  { value: 1440, label: '24 hours' },
  { value: 10080, label: '7 days' },
]

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function getMetricLabel(type: string): string {
  return METRIC_TYPES.find((m) => m.value === type)?.label || type
}

function getConditionLabel(cond: string): string {
  return CONDITIONS.find((c) => c.value === cond)?.label || cond
}

function formatMuteUntil(muteUntil: string | null): string {
  if (!muteUntil) return ''
  const d = new Date(muteUntil)
  const now = new Date()
  if (d <= now) return 'Expired'
  const diffMs = d.getTime() - now.getTime()
  const hours = Math.floor(diffMs / 3600000)
  const mins = Math.floor((diffMs % 3600000) / 60000)
  if (hours > 0) return `${hours}h ${mins}m remaining`
  return `${mins}m remaining`
}

// ============================================
// Alert Rules Tab
// ============================================

function AlertRulesTab() {
  const [modalVisible, setModalVisible] = useState(false)
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null)
  const [muteModalVisible, setMuteModalVisible] = useState(false)
  const [muteRuleId, setMuteRuleId] = useState<string | null>(null)
  const [muteDuration, setMuteDuration] = useState(60)
  const [form] = Form.useForm()

  const { data, isLoading } = useAlertRules()
  const createMutation = useCreateAlertRule()
  const updateMutation = useUpdateAlertRule()
  const deleteMutation = useDeleteAlertRule()
  const muteMutation = useMuteAlertRule()
  const unmuteMutation = useUnmuteAlertRule()
  const checkMutation = useCheckAlerts()

  const rules = data?.data ?? []

  const openCreate = () => {
    setEditingRule(null)
    form.resetFields()
    form.setFieldsValue({
      metric_type: 'error_rate',
      condition: 'gt',
      threshold: 5,
      window_minutes: 5,
      notification_channel: 'discord',
    })
    setModalVisible(true)
  }

  const openEdit = (rule: AlertRule) => {
    setEditingRule(rule)
    form.setFieldsValue({
      name: rule.name,
      metric_type: rule.metric_type,
      condition: rule.condition,
      threshold: rule.threshold,
      window_minutes: rule.window_minutes,
      notification_channel: rule.notification_channel,
    })
    setModalVisible(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validate()
      if (editingRule) {
        await updateMutation.mutateAsync({ id: editingRule.id, ...values })
        Message.success('Rule updated')
      } else {
        await createMutation.mutateAsync(values as AlertRuleCreate)
        Message.success('Rule created')
      }
      setModalVisible(false)
    } catch {
      // validation error
    }
  }

  const handleDelete = async (id: string) => {
    await deleteMutation.mutateAsync(id)
    Message.success('Rule deleted')
  }

  const handleToggleActive = async (rule: AlertRule) => {
    await updateMutation.mutateAsync({ id: rule.id, is_active: !rule.is_active })
    Message.success(rule.is_active ? 'Rule disabled' : 'Rule enabled')
  }

  const handleMute = async () => {
    if (!muteRuleId) return
    await muteMutation.mutateAsync({ id: muteRuleId, duration_minutes: muteDuration })
    Message.success('Rule muted')
    setMuteModalVisible(false)
  }

  const handleUnmute = async (id: string) => {
    await unmuteMutation.mutateAsync(id)
    Message.success('Rule unmuted')
  }

  const handleCheck = async () => {
    const result = await checkMutation.mutateAsync()
    if (result.alerts_triggered > 0) {
      Message.warning(`${result.alerts_triggered} alert(s) triggered`)
    } else {
      Message.success('No alerts triggered')
    }
  }

  const columns: ColumnProps<AlertRule>[] = [
    {
      title: 'Status',
      width: 80,
      render: (_, record) => (
        <Space direction="vertical" size={2}>
          <Switch
            size="small"
            checked={record.is_active}
            onChange={() => handleToggleActive(record)}
          />
          {record.is_muted && (
            <Tag color="orangered" size="small">Muted</Tag>
          )}
        </Space>
      ),
    },
    {
      title: 'Name',
      dataIndex: 'name',
      render: (_, record) => (
        <div>
          <Typography.Text bold>{record.name}</Typography.Text>
          {record.is_muted && record.mute_until && (
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {formatMuteUntil(record.mute_until)}
              </Typography.Text>
            </div>
          )}
        </div>
      ),
    },
    {
      title: 'Condition',
      width: 280,
      render: (_, record) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 13 }}>
          {getMetricLabel(record.metric_type)} {getConditionLabel(record.condition)} {record.threshold}
        </Typography.Text>
      ),
    },
    {
      title: 'Window',
      dataIndex: 'window_minutes',
      width: 100,
      render: (_, record) => `${record.window_minutes} min`,
    },
    {
      title: 'Channel',
      dataIndex: 'notification_channel',
      width: 100,
      render: (_, record) => (
        <Tag size="small" color="purple">{record.notification_channel}</Tag>
      ),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 180,
      render: (_, record) => (
        <Typography.Text style={{ fontSize: 13 }}>
          {formatDateTime(record.created_at)}
        </Typography.Text>
      ),
    },
    {
      title: 'Actions',
      width: 160,
      render: (_, record) => (
        <Space size={4}>
          <Tooltip content="Edit">
            <Button type="text" size="mini" icon={<IconEdit />} onClick={() => openEdit(record)} />
          </Tooltip>
          {record.is_muted ? (
            <Tooltip content="Unmute">
              <Button type="text" size="mini" icon={<IconPlayArrow />} onClick={() => handleUnmute(record.id)} />
            </Tooltip>
          ) : (
            <Tooltip content="Mute">
              <Button
                type="text"
                size="mini"
                icon={<IconMute />}
                onClick={() => {
                  setMuteRuleId(record.id)
                  setMuteDuration(60)
                  setMuteModalVisible(true)
                }}
              />
            </Tooltip>
          )}
          <Popconfirm title="Delete this rule?" onOk={() => handleDelete(record.id)}>
            <Tooltip content="Delete">
              <Button type="text" size="mini" icon={<IconDelete />} status="danger" />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space size="medium">
            <Button type="primary" icon={<IconPlus />} onClick={openCreate}>
              New Rule
            </Button>
            <Button
              icon={<IconRefresh />}
              onClick={handleCheck}
              loading={checkMutation.isPending}
            >
              Check Now
            </Button>
          </Space>
          <Typography.Text type="secondary">
            {rules.length} rule(s) configured
          </Typography.Text>
        </div>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={rules}
          loading={isLoading}
          pagination={false}
          noDataElement={<EmptyState description="No alert rules configured" />}
        />
      </Card>

      {/* Create / Edit Modal */}
      <Modal
        title={editingRule ? 'Edit Alert Rule' : 'New Alert Rule'}
        visible={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={handleSubmit}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        style={{ width: 520 }}
      >
        <Form form={form} layout="vertical">
          <FormItem label="Name" field="name" rules={[{ required: true, message: 'Name is required' }]}>
            <Input placeholder="e.g., High Error Rate" />
          </FormItem>
          <Space size="medium" style={{ display: 'flex' }}>
            <FormItem label="Metric" field="metric_type" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select>
                {METRIC_TYPES.map((m) => (
                  <Select.Option key={m.value} value={m.value}>{m.label}</Select.Option>
                ))}
              </Select>
            </FormItem>
            <FormItem label="Condition" field="condition" rules={[{ required: true }]} style={{ width: 80 }}>
              <Select>
                {CONDITIONS.map((c) => (
                  <Select.Option key={c.value} value={c.value}>{c.label}</Select.Option>
                ))}
              </Select>
            </FormItem>
            <FormItem label="Threshold" field="threshold" rules={[{ required: true }]} style={{ width: 120 }}>
              <InputNumber min={0} precision={2} />
            </FormItem>
          </Space>
          <Space size="medium" style={{ display: 'flex' }}>
            <FormItem label="Window (minutes)" field="window_minutes" style={{ flex: 1 }}>
              <InputNumber min={1} max={1440} />
            </FormItem>
            <FormItem label="Channel" field="notification_channel" style={{ flex: 1 }}>
              <Select>
                <Select.Option value="discord">Discord</Select.Option>
              </Select>
            </FormItem>
          </Space>
        </Form>
      </Modal>

      {/* Mute Duration Modal */}
      <Modal
        title="Mute Alert Rule"
        visible={muteModalVisible}
        onCancel={() => setMuteModalVisible(false)}
        onOk={handleMute}
        confirmLoading={muteMutation.isPending}
        style={{ width: 400 }}
      >
        <Form layout="vertical">
          <FormItem label="Mute Duration">
            <Select value={muteDuration} onChange={setMuteDuration}>
              {MUTE_DURATIONS.map((d) => (
                <Select.Option key={d.value} value={d.value}>{d.label}</Select.Option>
              ))}
            </Select>
          </FormItem>
        </Form>
      </Modal>
    </>
  )
}

// ============================================
// Alert History Tab
// ============================================

const PAGE_SIZE = 50

function AlertHistoryTab() {
  const [page, setPage] = useState(1)
  const [resolvedFilter, setResolvedFilter] = useState<string>('')
  const [period, setPeriod] = useState('24h')
  const [dateRange, setDateRange] = useState<[string, string] | null>(null)

  const timeRange = periodToDateRange(period, dateRange)

  const { data, isLoading } = useAlertHistory({
    page,
    pageSize: PAGE_SIZE,
    resolved: resolvedFilter === '' ? undefined : resolvedFilter === 'true',
    ...timeRange,
  })
  const resolveMutation = useResolveAlert()

  const alerts = data?.data ?? []
  const total = data?.total ?? 0

  const handleResolve = async (id: string) => {
    await resolveMutation.mutateAsync(id)
    Message.success('Alert resolved')
  }

  const columns: ColumnProps<AlertHistory>[] = [
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
      title: 'Status',
      width: 100,
      render: (_, record) => (
        record.resolved ? (
          <Tag color="green" size="small">Resolved</Tag>
        ) : (
          <Tag color="red" size="small">Active</Tag>
        )
      ),
    },
    {
      title: 'Rule',
      dataIndex: 'rule_name',
      width: 200,
      render: (_, record) => (
        <Typography.Text bold>{record.rule_name}</Typography.Text>
      ),
    },
    {
      title: 'Metric',
      width: 180,
      render: (_, record) => (
        <Typography.Text style={{ fontFamily: 'monospace', fontSize: 13 }}>
          {getMetricLabel(record.metric_type)}
        </Typography.Text>
      ),
    },
    {
      title: 'Value',
      width: 120,
      render: (_, record) => (
        <Typography.Text style={{ fontFamily: 'monospace', color: 'var(--color-danger-6)' }}>
          {record.metric_value} {getConditionLabel(record.condition)} {record.threshold}
        </Typography.Text>
      ),
    },
    {
      title: 'Message',
      dataIndex: 'message',
      render: (_, record) => (
        <Typography.Text style={{ fontSize: 13 }} ellipsis>
          {record.message}
        </Typography.Text>
      ),
    },
    {
      title: '',
      width: 48,
      render: (_, record) =>
        !record.resolved ? (
          <Tooltip content="Mark as resolved">
            <Button
              type="text"
              size="mini"
              icon={<IconCheck />}
              status="success"
              onClick={() => handleResolve(record.id)}
            />
          </Tooltip>
        ) : null,
    },
  ]

  return (
    <>
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size="medium">
          <TimeRangeSelector
            period={period}
            onPeriodChange={setPeriod}
            dateRange={dateRange}
            onDateRangeChange={setDateRange}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Space size="medium">
              <Select
                placeholder="All Status"
                value={resolvedFilter}
                onChange={(val) => { setResolvedFilter(val); setPage(1) }}
                style={{ width: 160 }}
                allowClear
                onClear={() => { setResolvedFilter(''); setPage(1) }}
              >
                <Select.Option value="false">Active</Select.Option>
                <Select.Option value="true">Resolved</Select.Option>
              </Select>
            </Space>
            <Button
              icon={<IconExport />}
              size="small"
              onClick={() => exportToCsv(
                `alert-history-${new Date().toISOString().slice(0, 10)}.csv`,
                alerts as unknown as Record<string, unknown>[],
                [
                  { key: 'created_at', label: 'Time' },
                  { key: 'rule_name', label: 'Rule' },
                  { key: 'metric_type', label: 'Metric' },
                  { key: 'metric_value', label: 'Value' },
                  { key: 'threshold', label: 'Threshold' },
                  { key: 'message', label: 'Message' },
                  { key: 'resolved', label: 'Resolved' },
                ],
              )}
            >
              Export
            </Button>
          </div>
        </Space>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={alerts}
          loading={isLoading}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: (t) => `Total ${t} entries`,
            sizeCanChange: false,
          }}
          noDataElement={<EmptyState description="No alert history" />}
        />
      </Card>
    </>
  )
}

// ============================================
// Main Component
// ============================================

export function Alerts() {
  return (
    <div>
      <PageHeader
        title="Alerts"
        subtitle="Configure alert rules and view alert history"
        icon={<IconNotification />}
        breadcrumb={['Logs & Monitoring', 'Alerts']}
      />
      <Tabs defaultActiveTab="rules" type="card-gutter">
        <Tabs.TabPane key="rules" title="Alert Rules">
          <AlertRulesTab />
        </Tabs.TabPane>
        <Tabs.TabPane key="history" title="Alert History">
          <AlertHistoryTab />
        </Tabs.TabPane>
      </Tabs>
    </div>
  )
}
