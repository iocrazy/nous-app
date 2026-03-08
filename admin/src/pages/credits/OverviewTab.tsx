import { useState } from 'react'
import {
  Card,
  Statistic,
  Grid,
  Button,
  Modal,
  Form,
  Input,
  InputNumber,
  Message,
  Space,
  Spin,
  Radio,
  Select,
} from '@arco-design/web-react'
import { IconPlus, IconGift } from '@arco-design/web-react/icon'
import { Line, Pie, Bar } from '@ant-design/charts'
import {
  useCreditsStats,
  useRevenueChart,
  useConsumptionChart,
  useTopTeams,
  useAdjustPoints,
  useBatchGift,
} from '../../api/endpoints/credits'
import { useTeams, getTeamDisplayName } from '../../api/endpoints/teams'

const { Row, Col } = Grid
const FormItem = Form.Item

function formatCents(cents: number): string {
  return (cents / 100).toFixed(2)
}

function AdjustPointsModal({
  visible,
  onClose,
}: {
  visible: boolean
  onClose: () => void
}) {
  const [form] = Form.useForm()
  const adjustPoints = useAdjustPoints()
  const { data: teamsData, isLoading: teamsLoading } = useTeams({ pageSize: 100 })
  const teams = teamsData?.items ?? []

  const handleSubmit = () => {
    form.validate().then((values) => {
      adjustPoints.mutate(
        {
          team_id: values.team_id,
          amount: values.amount,
          description: values.description || undefined,
        },
        {
          onSuccess: () => {
            Message.success('Points adjusted successfully')
            form.resetFields()
            onClose()
          },
          onError: (err) => {
            Message.error(err.message || 'Failed to adjust points')
          },
        },
      )
    })
  }

  return (
    <Modal
      title="Adjust Points"
      visible={visible}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={adjustPoints.isPending}
      unmountOnExit
    >
      <Form form={form} layout="vertical">
        <FormItem label="Team" field="team_id" rules={[{ required: true, message: 'Please select a team' }]}>
          <Select
            placeholder="Select a team"
            showSearch
            loading={teamsLoading}
            filterOption={(input, option) => {
              const label = option?.props?.children
              const text = typeof label === 'string' ? label : String(label ?? '')
              return text.toLowerCase().includes(input.toLowerCase())
            }}
          >
            {teams.map((t) => (
              <Select.Option key={t.id} value={t.id}>
                {`${getTeamDisplayName(t)} (${t.owner_email || 'No owner'})`}
              </Select.Option>
            ))}
          </Select>
        </FormItem>
        <FormItem label="Amount" field="amount" rules={[{ required: true, message: 'Amount is required' }]}>
          <InputNumber placeholder="Positive to add, negative to deduct" style={{ width: '100%' }} />
        </FormItem>
        <FormItem label="Description" field="description">
          <Input placeholder="Optional description" />
        </FormItem>
      </Form>
    </Modal>
  )
}

function BatchGiftModal({
  visible,
  onClose,
}: {
  visible: boolean
  onClose: () => void
}) {
  const [form] = Form.useForm()
  const batchGift = useBatchGift()
  const { data: teamsData, isLoading: teamsLoading } = useTeams({ pageSize: 100 })
  const teams = teamsData?.items ?? []

  const handleSubmit = () => {
    form.validate().then((values) => {
      const teamIds = values.team_ids as string[]
      if (!teamIds || teamIds.length === 0) {
        Message.error('Please select at least one team')
        return
      }

      batchGift.mutate(
        {
          team_ids: teamIds,
          amount: values.amount,
          description: values.description || undefined,
        },
        {
          onSuccess: () => {
            Message.success('Points gifted successfully')
            form.resetFields()
            onClose()
          },
          onError: (err) => {
            Message.error(err.message || 'Failed to gift points')
          },
        },
      )
    })
  }

  return (
    <Modal
      title="Batch Gift Points"
      visible={visible}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={batchGift.isPending}
      unmountOnExit
    >
      <Form form={form} layout="vertical">
        <FormItem
          label="Teams"
          field="team_ids"
          rules={[{ required: true, message: 'Please select at least one team' }]}
        >
          <Select
            mode="multiple"
            placeholder="Select teams"
            showSearch
            loading={teamsLoading}
            filterOption={(input, option) => {
              const label = option?.props?.children
              const text = typeof label === 'string' ? label : String(label ?? '')
              return text.toLowerCase().includes(input.toLowerCase())
            }}
          >
            {teams.map((t) => (
              <Select.Option key={t.id} value={t.id}>
                {`${getTeamDisplayName(t)} (${t.owner_email || 'No owner'})`}
              </Select.Option>
            ))}
          </Select>
        </FormItem>
        <FormItem label="Amount" field="amount" rules={[{ required: true, message: 'Amount is required' }]}>
          <InputNumber min={1} placeholder="Points to gift (positive only)" style={{ width: '100%' }} />
        </FormItem>
        <FormItem label="Description" field="description">
          <Input placeholder="Optional description" />
        </FormItem>
      </Form>
    </Modal>
  )
}

export function OverviewTab() {
  const [period, setPeriod] = useState<string>('day')
  const [adjustModalVisible, setAdjustModalVisible] = useState(false)
  const [giftModalVisible, setGiftModalVisible] = useState(false)

  const { data: stats, isLoading: statsLoading } = useCreditsStats()
  const { data: revenueData } = useRevenueChart(period)
  const { data: consumptionData } = useConsumptionChart()
  const { data: topTeamsData } = useTopTeams()

  if (statsLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 80 }}>
        <Spin size={32} />
      </div>
    )
  }

  const revenueChartData = (revenueData ?? []).map((item) => ({
    ...item,
    revenue: Number(formatCents(item.revenue_cents)),
  }))

  const statCards = [
    {
      title: 'Total Points',
      value: stats?.total_points_in_system ?? 0,
    },
    {
      title: 'Total Revenue',
      value: formatCents(stats?.total_revenue_cents ?? 0),
      prefix: '\u00a5',
    },
    {
      title: 'Total Consumed',
      value: stats?.total_consumed ?? 0,
    },
    {
      title: 'Active Teams',
      value: stats?.active_teams_count ?? 0,
    },
    {
      title: 'Pending Orders',
      value: stats?.pending_orders_count ?? 0,
      styleValue: { color: '#FF7D00' },
    },
    {
      title: 'Monthly Revenue',
      value: formatCents(stats?.monthly_revenue_cents ?? 0),
      prefix: '\u00a5',
    },
  ]

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
        <Space>
          <Button
            type="primary"
            icon={<IconPlus />}
            onClick={() => setAdjustModalVisible(true)}
          >
            Adjust Points
          </Button>
          <Button
            type="outline"
            icon={<IconGift />}
            onClick={() => setGiftModalVisible(true)}
          >
            Batch Gift
          </Button>
        </Space>
      </div>

      {/* Stats Cards */}
      <Row gutter={20} style={{ marginBottom: 20 }}>
        {statCards.map((card) => (
          <Col key={card.title} span={4}>
            <Card>
              <Statistic
                title={card.title}
                value={card.value}
                prefix={card.prefix}
                styleValue={card.styleValue}
                groupSeparator
              />
            </Card>
          </Col>
        ))}
      </Row>

      {/* Charts Row */}
      <Row gutter={20} style={{ marginBottom: 20 }}>
        <Col span={12}>
          <Card
            title="Revenue Trend"
            extra={
              <Radio.Group value={period} onChange={setPeriod} type="button" size="small">
                <Radio value="day">Day</Radio>
                <Radio value="week">Week</Radio>
                <Radio value="month">Month</Radio>
              </Radio.Group>
            }
          >
            <Line
              data={revenueChartData}
              xField="date"
              yField="revenue"
              height={300}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="Consumption by Type">
            <Pie
              data={consumptionData ?? []}
              angleField="total_points"
              colorField="action_type"
              height={300}
            />
          </Card>
        </Col>
      </Row>

      {/* Top Teams */}
      <Card title="Top 10 Consuming Teams">
        <Bar
          data={topTeamsData ?? []}
          xField="team_name"
          yField="total_consumed"
          height={300}
        />
      </Card>

      {/* Modals */}
      <AdjustPointsModal
        visible={adjustModalVisible}
        onClose={() => setAdjustModalVisible(false)}
      />
      <BatchGiftModal
        visible={giftModalVisible}
        onClose={() => setGiftModalVisible(false)}
      />
    </div>
  )
}
