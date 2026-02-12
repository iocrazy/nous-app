import { useState } from 'react'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import {
  Layout,
  Menu,
  Avatar,
  Dropdown,
  Typography,
  Tag,
} from '@arco-design/web-react'
import {
  IconDashboard,
  IconUser,
  IconUserGroup,
  IconVideoCamera,
  IconTags,
  IconStar,
  IconFile,
  IconLock,
  IconSettings,
  IconPoweroff,
  IconMenuFold,
  IconMenuUnfold,
} from '@arco-design/web-react/icon'
import { useAuth } from '../auth/AuthProvider'

const { Sider, Header, Content } = Layout
const MenuItem = Menu.Item

const menuItems = [
  { key: '/', label: 'Dashboard', icon: <IconDashboard /> },
  { key: '/users', label: 'Users', icon: <IconUser /> },
  { key: '/teams', label: 'Teams', icon: <IconUserGroup /> },
  { key: '/videos', label: 'Videos', icon: <IconVideoCamera /> },
  { key: '/tags', label: 'Tags', icon: <IconTags /> },
  { key: '/credits', label: 'Credits', icon: <IconStar /> },
  { key: '/audit-logs', label: 'Audit Logs', icon: <IconFile /> },
  { key: '/api-keys', label: 'API Keys', icon: <IconLock /> },
  { key: '/settings', label: 'Settings', icon: <IconSettings /> },
]

export function AdminLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()

  const selectedKey = menuItems.find(
    (item) => item.key !== '/' && location.pathname.startsWith(item.key),
  )?.key || '/'

  const dropList = (
    <Menu onClickMenuItem={(key) => { if (key === 'logout') logout() }}>
      <MenuItem key="logout">
        <IconPoweroff style={{ marginRight: 8 }} />
        Logout
      </MenuItem>
    </Menu>
  )

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider
        collapsed={collapsed}
        collapsible
        trigger={null}
        width={220}
        collapsedWidth={48}
        style={{ overflow: 'auto' }}
      >
        <div
          style={{
            height: 48,
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start',
            padding: collapsed ? 0 : '0 16px',
            fontWeight: 700,
            fontSize: 16,
            color: 'var(--color-text-1)',
            borderBottom: '1px solid var(--color-border)',
          }}
        >
          {collapsed ? 'M' : 'MediaHub Admin'}
        </div>
        <Menu
          selectedKeys={[selectedKey]}
          onClickMenuItem={(key) => navigate(key)}
          collapse={collapsed}
          style={{ width: '100%' }}
        >
          {menuItems.map((item) => (
            <MenuItem key={item.key}>
              {item.icon}
              {item.label}
            </MenuItem>
          ))}
        </Menu>
      </Sider>
      <Layout>
        <Header
          style={{
            height: 48,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 16px',
            borderBottom: '1px solid var(--color-border)',
            backgroundColor: 'var(--color-bg-2)',
          }}
        >
          <div
            style={{ cursor: 'pointer', fontSize: 18 }}
            onClick={() => setCollapsed(!collapsed)}
          >
            {collapsed ? <IconMenuUnfold /> : <IconMenuFold />}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Tag color="arcoblue" size="small">
              {user?.role}
            </Tag>
            <Dropdown droplist={dropList} position="br">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <Avatar size={28} style={{ backgroundColor: '#3370ff' }}>
                  {user?.name?.charAt(0).toUpperCase() || 'A'}
                </Avatar>
                <Typography.Text>{user?.name || user?.email}</Typography.Text>
              </div>
            </Dropdown>
          </div>
        </Header>
        <Content
          style={{
            padding: 16,
            overflow: 'auto',
            backgroundColor: 'var(--color-fill-2)',
          }}
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
