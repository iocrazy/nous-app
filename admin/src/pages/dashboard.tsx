import { Link } from 'react-router-dom'
import { Card, Statistic, Grid, Spin, Typography } from '@arco-design/web-react'
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
} from '@arco-design/web-react/icon'
import { useStats } from '../api/endpoints/stats'

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
