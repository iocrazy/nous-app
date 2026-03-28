import { Link } from 'react-router-dom'
import { Card, Statistic, Grid, Spin, Typography, Tag, Badge, Tooltip } from '@arco-design/web-react'
import {
  IconUser,
  IconVideoCamera,
  IconUserGroup,
  IconDownload,
  IconThunderbolt,
  IconUserAdd,
  IconArrowRise,
  IconSettings,
  IconFile,
  IconDesktop,
  IconStorage,
} from '@arco-design/web-react/icon'
import { useStats } from '../api/endpoints/stats'
import { useCeleryWorkers, useCeleryQueues } from '../api/endpoints/celery'

const { Row, Col } = Grid
const { Title, Text } = Typography

const mainStats = [
  { key: 'total_users', title: 'Total Users', icon: <IconUser style={{ fontSize: 24 }} />, color: '#3491FA' },
  { key: 'total_videos', title: 'Total Videos', icon: <IconVideoCamera style={{ fontSize: 24 }} />, color: '#00B42A' },
  { key: 'total_teams', title: 'Active Teams', icon: <IconUserGroup style={{ fontSize: 24 }} />, color: '#722ED1' },
  { key: 'total_downloads', title: 'Downloads', icon: <IconDownload style={{ fontSize: 24 }} />, color: '#FF7D00' },
] as const

const todayStats = [
  { key: 'active_users_today', title: 'Active Today', icon: <IconThunderbolt style={{ fontSize: 24 }} />, desc: 'Users active in the last 24 hours' },
  { key: 'new_users_today', title: 'New Users Today', icon: <IconUserAdd style={{ fontSize: 24 }} />, desc: 'Users registered today' },
  { key: 'new_videos_today', title: 'New Videos Today', icon: <IconArrowRise style={{ fontSize: 24 }} />, desc: 'Videos added today' },
] as const

const quickActions = [
  { to: '/users', icon: <IconUser style={{ fontSize: 24 }} />, label: 'Manage Users' },
  { to: '/teams', icon: <IconUserGroup style={{ fontSize: 24 }} />, label: 'Manage Teams' },
  { to: '/audit-logs', icon: <IconFile style={{ fontSize: 24 }} />, label: 'View Audit Logs' },
  { to: '/settings', icon: <IconSettings style={{ fontSize: 24 }} />, label: 'Settings' },
]

function CeleryStatusCards() {
  const { data: workersData } = useCeleryWorkers()
  const { data: queuesData } = useCeleryQueues()

  const totalPending = queuesData?.queues.reduce((sum, q) => sum + q.messages, 0) ?? 0

  return (
    <>
      <Title heading={6} style={{ marginBottom: 16 }}>
        Celery Status
      </Title>
      <Row gutter={20} style={{ marginBottom: 20 }}>
        {/* Workers Card */}
        <Col xs={24} sm={12} lg={8}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>Workers</Text>
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                  <span style={{ color: workersData?.online ? 'rgb(var(--green-6))' : 'rgb(var(--red-6))' }}>
                    {workersData?.online ?? '-'}
                  </span>
                  <Text type="secondary" style={{ fontSize: 14, fontWeight: 400 }}> online</Text>
                </div>
              </div>
              <div
                style={{
                  width: 48, height: 48, borderRadius: 8,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  backgroundColor: workersData?.online ? '#00B42A1A' : '#F531271A',
                  color: workersData?.online ? '#00B42A' : '#F53127',
                }}
              >
                <IconDesktop style={{ fontSize: 24 }} />
              </div>
            </div>
            {workersData?.workers && workersData.workers.length > 0 && (
              <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {workersData.workers.map((w) => (
                  <Tooltip key={w.name} content={`Active: ${w.active} | Processed: ${w.processed}`}>
                    <Tag size="small" color="green">
                      {w.name.replace('celery@', '')}
                      {w.active > 0 && <Badge count={w.active} dotStyle={{ fontSize: 10 }} style={{ marginLeft: 4 }} />}
                    </Tag>
                  </Tooltip>
                ))}
              </div>
            )}
          </Card>
        </Col>

        {/* Queue Summary Card */}
        <Col xs={24} sm={12} lg={8}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>Queue Backlog</Text>
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                  <span style={{ color: totalPending > 0 ? 'rgb(var(--orange-6))' : 'rgb(var(--green-6))' }}>
                    {totalPending}
                  </span>
                  <Text type="secondary" style={{ fontSize: 14, fontWeight: 400 }}> messages</Text>
                </div>
              </div>
              <div
                style={{
                  width: 48, height: 48, borderRadius: 8,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  backgroundColor: totalPending > 0 ? '#FF7D001A' : '#00B42A1A',
                  color: totalPending > 0 ? '#FF7D00' : '#00B42A',
                }}
              >
                <IconStorage style={{ fontSize: 24 }} />
              </div>
            </div>
            {queuesData?.queues && (
              <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {queuesData.queues.map((q) => (
                  <Tag key={q.name} size="small" color={q.messages > 0 ? 'orange' : undefined}>
                    {q.name}: {q.messages}
                  </Tag>
                ))}
              </div>
            )}
          </Card>
        </Col>

        {/* Active Tasks Card */}
        <Col xs={24} sm={12} lg={8}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>Active Tasks</Text>
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>
                  {workersData?.workers.reduce((sum, w) => sum + w.active, 0) ?? 0}
                  <Text type="secondary" style={{ fontSize: 14, fontWeight: 400 }}> running</Text>
                </div>
              </div>
              <div
                style={{
                  width: 48, height: 48, borderRadius: 8,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  backgroundColor: '#3491FA1A',
                  color: '#3491FA',
                }}
              >
                <IconThunderbolt style={{ fontSize: 24 }} />
              </div>
            </div>
          </Card>
        </Col>
      </Row>
    </>
  )
}

export function Dashboard() {
  const { data: stats, isLoading } = useStats()

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 80 }}>
        <Spin size={32} />
      </div>
    )
  }

  return (
    <div>
      <Title heading={4} style={{ marginTop: 0, marginBottom: 20 }}>
        Dashboard
      </Title>

      {/* Main Stats */}
      <Row gutter={20} style={{ marginBottom: 20 }}>
        {mainStats.map((item) => (
          <Col key={item.key} xs={24} sm={12} lg={6}>
            <Card>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Statistic
                  title={item.title}
                  value={stats?.[item.key] ?? 0}
                  groupSeparator
                />
                <div
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 8,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: `${item.color}1A`,
                    color: item.color,
                  }}
                >
                  {item.icon}
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* Celery Status */}
      <CeleryStatusCards />

      {/* Today's Activity */}
      <Title heading={6} style={{ marginBottom: 16 }}>
        Today's Activity
      </Title>
      <Row gutter={20} style={{ marginBottom: 20 }}>
        {todayStats.map((item) => (
          <Col key={item.key} xs={24} sm={8}>
            <Card>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <div
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 8,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: 'var(--color-fill-2)',
                    color: 'var(--color-text-2)',
                  }}
                >
                  {item.icon}
                </div>
                <div>
                  <Statistic
                    title={item.title}
                    value={stats?.[item.key] ?? 0}
                    groupSeparator
                  />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {item.desc}
                  </Text>
                </div>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* Quick Actions */}
      <Card>
        <Title heading={6} style={{ marginTop: 0, marginBottom: 16 }}>
          Quick Actions
        </Title>
        <Row gutter={16}>
          {quickActions.map((action) => (
            <Col key={action.to} xs={12} sm={6}>
              <Link to={action.to} style={{ textDecoration: 'none' }}>
                <Card
                  hoverable
                  style={{ textAlign: 'center', cursor: 'pointer' }}
                  bodyStyle={{ padding: '20px 12px' }}
                >
                  <div style={{ color: 'var(--color-text-2)', marginBottom: 8 }}>
                    {action.icon}
                  </div>
                  <Text>{action.label}</Text>
                </Card>
              </Link>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  )
}
