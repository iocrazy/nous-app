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
  IconCode,
  IconLock,
  IconSettings,
  IconPoweroff,
  IconMenuFold,
  IconMenuUnfold,
  IconCompass,
  IconSearch,
  IconNotification,
  IconThunderbolt,
} from '@arco-design/web-react/icon'
import { useAuth } from '../auth/AuthProvider'

const { Sider, Header, Content } = Layout
const MenuItem = Menu.Item
const MenuItemGroup = Menu.ItemGroup

const allMenuKeys = [
  '/', '/users', '/teams', '/videos', '/transcode', '/tags', '/credits',
  '/monitoring', '/search', '/audit-logs', '/request-logs', '/alerts',
  '/api-keys', '/settings', '/transcode-config',
]

export function AdminLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()

  const selectedKey = allMenuKeys.find(
    (key) => key !== '/' && location.pathname.startsWith(key),
  ) || '/'

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
          <MenuItem key="/">
            <IconDashboard />
            Dashboard
          </MenuItem>
          <MenuItemGroup key="content" title="Content">
            <MenuItem key="/users"><IconUser />Users</MenuItem>
            <MenuItem key="/teams"><IconUserGroup />Teams</MenuItem>
            <MenuItem key="/videos"><IconVideoCamera />Videos</MenuItem>
            <MenuItem key="/transcode"><IconThunderbolt />Transcode</MenuItem>
            <MenuItem key="/tags"><IconTags />Tags</MenuItem>
            <MenuItem key="/credits"><IconStar />Credits</MenuItem>
          </MenuItemGroup>
          <MenuItemGroup key="logs" title="Logs & Monitoring">
            <MenuItem key="/monitoring"><IconCompass />Monitoring</MenuItem>
            <MenuItem key="/search"><IconSearch />Search</MenuItem>
            <MenuItem key="/audit-logs"><IconFile />Audit Logs</MenuItem>
            <MenuItem key="/request-logs"><IconCode />Request Logs</MenuItem>
            <MenuItem key="/alerts"><IconNotification />Alerts</MenuItem>
          </MenuItemGroup>
          <MenuItemGroup key="system" title="System">
            <MenuItem key="/api-keys"><IconLock />API Keys</MenuItem>
            <MenuItem key="/settings"><IconSettings />Settings</MenuItem>
            <MenuItem key="/transcode-config"><IconThunderbolt />Transcode</MenuItem>
          </MenuItemGroup>
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
