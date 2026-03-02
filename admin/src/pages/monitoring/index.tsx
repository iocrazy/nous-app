import { useState } from 'react'
import {
  Card,
  Grid,
  Statistic,
  Table,
  Tag,
  Typography,
} from '@arco-design/web-react'
import {
  IconClockCircle,
  IconExclamationCircle,
  IconThunderbolt,
  IconBug,
  IconDesktop,
  IconCompass,
} from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { DualAxes, Pie } from '@ant-design/charts'
import { useNavigate } from 'react-router-dom'
import {
  useMonitoringStats,
  type SlowApiEntry,
  type ErrorEndpointEntry,
  type ErrorModuleEntry,
  type RecentErrorEntry,
} from '../../api/endpoints/monitoring'
import { TimeRangeSelector } from '../../components/TimeRangeSelector'
import { PageHeader } from '../../components/PageHeader'
import { EmptyState } from '../../components/EmptyState'

const { Row, Col } = Grid

function formatDateTime(dateStr: string): string {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export function MonitoringDashboard() {
  const [period, setPeriod] = useState<string>('24h')
  const [dateRange, setDateRange] = useState<[string, string] | null>(null)
  const navigate = useNavigate()

  const params = dateRange
    ? { start_date: dateRange[0], end_date: dateRange[1] }
    : { period }

  const { data, isLoading } = useMonitoringStats(params)

  const overview = data?.overview

  // ---- DualAxes chart config ----
  const trendData = data?.request_trend ?? []

  const dualAxesConfig = {
    xField: 'time',
    children: [
      {
        data: trendData,
        type: 'interval' as const,
        yField: 'requests',
        style: { fill: '#3370ff', fillOpacity: 0.6 },
        axis: {
          y: { title: 'Requests', titleFill: '#3370ff' },
        },
      },
      {
        data: trendData,
        type: 'line' as const,
        yField: 'errors',
        style: { stroke: '#f53f3f', lineWidth: 2 },
        axis: {
          y: {
            position: 'right' as const,
            title: 'Errors',
            titleFill: '#f53f3f',
          },
        },
      },
    ],
    height: 300,
  }

  // ---- Pie chart config ----
  const levelDist = data?.log_level_distribution ?? {}
  const pieData = Object.entries(levelDist).map(([level, count]) => ({
    level,
    count,
  }))
  const levelColors: Record<string, string> = {
    INFO: '#3370ff',
    SUCCESS: '#00b42a',
    WARNING: '#ff7d00',
    ERROR: '#f53f3f',
    CRITICAL: '#d91ad9',
    DEBUG: '#86909c',
  }
  const pieConfig = {
    data: pieData,
    angleField: 'count',
    colorField: 'level',
    radius: 0.8,
    innerRadius: 0.5,
    label: { text: 'level', position: 'outside' as const },
    legend: { color: { position: 'bottom' as const } },
    style: {
      fill: ({ level }: { level: string }) => levelColors[level] || '#86909c',
    },
    height: 280,
  }

  // ---- Table columns ----
  const slowColumns: ColumnProps<SlowApiEntry>[] = [
    { title: 'Path', dataIndex: 'path', ellipsis: true },
    { title: 'Avg (ms)', dataIndex: 'avg_ms', width: 90 },
    { title: 'P95 (ms)', dataIndex: 'p95_ms', width: 90 },
    { title: 'Count', dataIndex: 'count', width: 70 },
  ]

  const errorEndpointColumns: ColumnProps<ErrorEndpointEntry>[] = [
    { title: 'Path', dataIndex: 'path', ellipsis: true },
    { title: 'Errors', dataIndex: 'error_count', width: 70 },
    {
      title: 'Status',
      dataIndex: 'last_status',
      width: 70,
      render: (_, r) =>
        r.last_status ? (
          <Tag color="red" size="small">
            {r.last_status}
          </Tag>
        ) : (
          '-'
        ),
    },
  ]

  const errorModuleColumns: ColumnProps<ErrorModuleEntry>[] = [
    { title: 'Module', dataIndex: 'module' },
    { title: 'Count', dataIndex: 'count', width: 70 },
  ]

  const recentErrorColumns: ColumnProps<RecentErrorEntry>[] = [
    {
      title: 'Time',
      dataIndex: 'logged_at',
      width: 160,
      render: (_, r) => formatDateTime(r.logged_at),
    },
    {
      title: 'Level',
      dataIndex: 'level',
      width: 80,
      render: (_, r) => (
        <Tag color={r.level === 'CRITICAL' ? 'magenta' : 'red'} size="small">
          {r.level}
        </Tag>
      ),
    },
    {
      title: 'Module',
      dataIndex: 'module',
      width: 120,
      render: (_, r) => r.module || '-',
    },
    {
      title: 'Message',
      dataIndex: 'message',
      ellipsis: true,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Monitoring"
        subtitle="System health overview — request volume, error rates, and performance"
        icon={<IconCompass />}
        breadcrumb={['Logs & Monitoring', 'Monitoring']}
      />
      {/* Time Range Selector */}
      <Card style={{ marginBottom: 16 }}>
        <TimeRangeSelector
          period={period}
          onPeriodChange={setPeriod}
          dateRange={dateRange}
          onDateRangeChange={setDateRange}
        />
      </Card>

      {/* Overview Stats */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Requests"
              value={overview?.total_requests ?? 0}
              loading={isLoading}
              prefix={<IconThunderbolt style={{ color: '#3370ff' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Error Rate"
              value={overview?.error_rate ?? 0}
              suffix="%"
              loading={isLoading}
              prefix={<IconExclamationCircle style={{ color: '#f53f3f' }} />}
              styleValue={{
                color: (overview?.error_rate ?? 0) > 5 ? '#f53f3f' : undefined,
              }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Avg Response"
              value={overview?.avg_response_ms ?? 0}
              suffix="ms"
              loading={isLoading}
              prefix={<IconClockCircle style={{ color: '#ff7d00' }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="App Errors"
              value={overview?.app_error_count ?? 0}
              loading={isLoading}
              prefix={<IconBug style={{ color: '#f53f3f' }} />}
              extra={
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  <IconDesktop style={{ marginRight: 4 }} />
                  FE: {overview?.frontend_error_count ?? 0}
                </Typography.Text>
              }
            />
          </Card>
        </Col>
      </Row>

      {/* Request Volume & Error Rate Chart */}
      <Card
        title="Request Volume & Errors"
        style={{ marginBottom: 16 }}
      >
        {trendData.length > 0 ? (
          <DualAxes {...dualAxesConfig} />
        ) : (
          <div
            style={{
              height: 300,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Typography.Text type="secondary">
              No request data in this time range
            </Typography.Text>
          </div>
        )}
      </Card>

      {/* Top Slow APIs + Top Error Endpoints */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card title="Top 10 Slow APIs">
            <Table
              rowKey="path"
              columns={slowColumns}
              data={data?.top_slow_apis ?? []}
              loading={isLoading}
              pagination={false}
              size="small"
              noDataElement={<EmptyState description="No slow APIs found" />}
              onRow={(record) => ({
                style: { cursor: 'pointer' },
                onClick: () =>
                  navigate(
                    `/request-logs?path=${encodeURIComponent(record.path)}`,
                  ),
              })}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="Top 10 Error Endpoints">
            <Table
              rowKey="path"
              columns={errorEndpointColumns}
              data={data?.top_error_endpoints ?? []}
              loading={isLoading}
              pagination={false}
              size="small"
              noDataElement={<EmptyState description="No error endpoints found" />}
              onRow={(record) => ({
                style: { cursor: 'pointer' },
                onClick: () =>
                  navigate(
                    `/request-logs?path=${encodeURIComponent(record.path)}`,
                  ),
              })}
            />
          </Card>
        </Col>
      </Row>

      {/* Log Level Distribution + Top Error Modules + Recent Errors */}
      <Row gutter={16}>
        <Col span={8}>
          <Card title="Log Level Distribution">
            {pieData.length > 0 ? (
              <Pie {...pieConfig} />
            ) : (
              <div
                style={{
                  height: 280,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Typography.Text type="secondary">No log data</Typography.Text>
              </div>
            )}
          </Card>
        </Col>
        <Col span={6}>
          <Card title="Top Error Modules">
            <Table
              rowKey="module"
              columns={errorModuleColumns}
              data={data?.top_error_modules ?? []}
              loading={isLoading}
              pagination={false}
              size="small"
              noDataElement={<EmptyState description="No error modules" />}
            />
          </Card>
        </Col>
        <Col span={10}>
          <Card title="Recent Errors">
            <Table
              rowKey={(r) => `${r.logged_at}-${r.message}`}
              columns={recentErrorColumns}
              data={data?.recent_errors ?? []}
              loading={isLoading}
              pagination={false}
              size="small"
              noDataElement={<EmptyState description="No recent errors" />}
              onRow={() => ({
                style: { cursor: 'pointer' },
                onClick: () => navigate('/request-logs?tab=app-logs'),
              })}
            />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
