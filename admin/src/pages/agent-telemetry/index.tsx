import { useState } from 'react'
import {
  Card,
  Grid,
  Statistic,
  Table,
  Tag,
  Typography,
  Radio,
} from '@arco-design/web-react'
import {
  IconRobot,
  IconUser,
  IconExclamationCircle,
  IconThunderbolt,
} from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { Line } from '@ant-design/charts'
import { PageHeader } from '../../components/PageHeader'
import { EmptyState } from '../../components/EmptyState'
import {
  useAgentTelemetry,
  type TopAgentEntry,
  type TopUserEntry,
  type DailyTrendPoint,
  type FailureModeEntry,
} from '../../api/endpoints/agent-telemetry'

const { Row, Col } = Grid

const STATUS_COLOR: Record<string, string> = {
  completed: 'green',
  failed: 'red',
  cancelled: 'orange',
  heartbeat_lost: 'magenta',
  paused: 'blue',
  running: 'arcoblue',
}

function formatCents(c: number): string {
  if (c < 1) return `${(c * 10).toFixed(2)}m¢`
  return `${c.toFixed(2)}¢`
}

export function AgentTelemetryDashboard() {
  const [days, setDays] = useState<number>(7)
  const { data, isLoading } = useAgentTelemetry(days)

  const overview = data?.overview
  const trendData: DailyTrendPoint[] = data?.daily_trend ?? []
  const statusBreakdown = data?.status_breakdown ?? {}

  const lineConfig = {
    data: trendData,
    xField: 'date',
    yField: 'runs',
    smooth: true,
    point: { shapeField: 'circle', sizeField: 4 },
    height: 280,
  }

  const agentColumns: ColumnProps<TopAgentEntry>[] = [
    { title: 'Agent ID', dataIndex: 'agent_id', ellipsis: true },
    {
      title: 'Runs',
      dataIndex: 'run_count',
      width: 80,
      sorter: (a, b) => a.run_count - b.run_count,
    },
    {
      title: 'Tokens',
      dataIndex: 'total_tokens',
      width: 100,
      render: (v) => v?.toLocaleString() ?? 0,
    },
    {
      title: 'Cost',
      dataIndex: 'cost_cents',
      width: 100,
      sorter: (a, b) => a.cost_cents - b.cost_cents,
      render: (v: number) => formatCents(v),
    },
  ]

  const userColumns: ColumnProps<TopUserEntry>[] = [
    { title: 'User ID', dataIndex: 'user_id', ellipsis: true },
    { title: 'Runs', dataIndex: 'run_count', width: 80 },
    {
      title: 'Tokens',
      dataIndex: 'total_tokens',
      width: 100,
      render: (v) => v?.toLocaleString() ?? 0,
    },
    {
      title: 'Cost',
      dataIndex: 'cost_cents',
      width: 100,
      render: (v: number) => formatCents(v),
    },
  ]

  const failureColumns: ColumnProps<FailureModeEntry>[] = [
    {
      title: 'Error Code',
      dataIndex: 'error_code',
      render: (v) => (
        <Tag color="red" size="small">
          {v}
        </Tag>
      ),
    },
    { title: 'Count', dataIndex: 'count', width: 80 },
  ]

  return (
    <div>
      <PageHeader
        title="Agent Telemetry"
        subtitle="System-wide agent_runs snapshot — cost, usage, failure modes"
        icon={<IconRobot />}
        breadcrumb={['AI', 'Telemetry']}
      />

      {/* Time window selector */}
      <Card style={{ marginBottom: 16 }}>
        <Radio.Group
          type="button"
          value={days}
          onChange={(v) => setDays(v as number)}
          options={[
            { label: 'Last 24h', value: 1 },
            { label: 'Last 7d', value: 7 },
            { label: 'Last 14d', value: 14 },
            { label: 'Last 30d', value: 30 },
          ]}
        />
        {data && (
          <Typography.Text
            type="secondary"
            style={{ marginLeft: 16, fontSize: 12 }}
          >
            Window: {data.window_start.slice(0, 10)} →{' '}
            {data.window_end.slice(0, 10)}
          </Typography.Text>
        )}
      </Card>

      {/* Overview */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Runs"
              value={overview?.total_runs ?? 0}
              loading={isLoading}
              prefix={<IconThunderbolt style={{ color: '#3370ff' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Cost"
              value={(overview?.total_cost_cents ?? 0).toFixed(2)}
              suffix="¢"
              loading={isLoading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Unique Agents"
              value={overview?.unique_agents ?? 0}
              loading={isLoading}
              prefix={<IconRobot />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Unique Users"
              value={overview?.unique_users ?? 0}
              loading={isLoading}
              prefix={<IconUser />}
            />
          </Card>
        </Col>
      </Row>

      {/* Status breakdown */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={24}>
          <Card title="Status Breakdown">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              {Object.entries(statusBreakdown).map(([status, count]) => (
                <Tag
                  key={status}
                  color={STATUS_COLOR[status] || 'gray'}
                  size="medium"
                >
                  {status}: <strong style={{ marginLeft: 4 }}>{count}</strong>
                </Tag>
              ))}
              {Object.keys(statusBreakdown).length === 0 && (
                <Typography.Text type="secondary">
                  No runs in window
                </Typography.Text>
              )}
            </div>
          </Card>
        </Col>
      </Row>

      {/* Daily trend */}
      <Card title="Daily Run Volume" style={{ marginBottom: 16 }}>
        {trendData.length > 0 ? (
          <Line {...lineConfig} />
        ) : (
          <div
            style={{
              height: 280,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Typography.Text type="secondary">
              No data in this window
            </Typography.Text>
          </div>
        )}
      </Card>

      {/* Top agents + Top users */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card title="Top 10 Agents (by cost)">
            <Table
              rowKey="agent_id"
              columns={agentColumns}
              data={data?.top_agents ?? []}
              loading={isLoading}
              pagination={false}
              size="small"
              noDataElement={<EmptyState description="No agent activity" />}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="Top 10 Users (by cost)">
            <Table
              rowKey="user_id"
              columns={userColumns}
              data={data?.top_users ?? []}
              loading={isLoading}
              pagination={false}
              size="small"
              noDataElement={<EmptyState description="No user activity" />}
            />
          </Card>
        </Col>
      </Row>

      {/* Failure modes */}
      <Card
        title={
          <span>
            <IconExclamationCircle style={{ color: '#f53f3f', marginRight: 4 }} />
            Top Failure Modes
          </span>
        }
      >
        <Table
          rowKey="error_code"
          columns={failureColumns}
          data={data?.failure_modes ?? []}
          loading={isLoading}
          pagination={false}
          size="small"
          noDataElement={
            <EmptyState description="No failures in this window — nice" />
          }
        />
      </Card>
    </div>
  )
}
