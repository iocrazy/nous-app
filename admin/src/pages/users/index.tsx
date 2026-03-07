import { useState } from 'react'
import { formatDate } from '../../utils/format'
import {
  Table,
  Input,
  Select,
  Tag,
  Avatar,
  Dropdown,
  Menu,
  Modal,
  Message,
  Card,
  Space,
  Typography,
} from '@arco-design/web-react'
import {
  IconUser,
  IconMore,
  IconStop,
  IconCheckCircle,
  IconCloseCircle,
  IconDelete,
} from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { useUsers, useUpdateUser, useDeleteUser } from '../../api/endpoints/users'
import type { UserData } from '../../api/endpoints/users'

const ROLES = ['admin', 'user', 'test'] as const
const PAGE_SIZE = 20

export function UserList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<string | undefined>(undefined)

  const { data, isLoading } = useUsers({ page, pageSize: PAGE_SIZE, search, role: roleFilter })
  const updateUser = useUpdateUser()
  const deleteUser = useDeleteUser()

  const users = data?.items ?? []
  const total = data?.total ?? 0

  const handleSearch = (value: string) => {
    setSearch(value)
    setPage(1)
  }

  const handleRoleChange = (userId: string, newRole: string) => {
    updateUser.mutate(
      { id: userId, values: { role: newRole } },
      {
        onSuccess: () => Message.success('Role updated'),
      },
    )
  }

  const handleBanToggle = (user: UserData) => {
    const action = user.is_banned ? 'unban' : 'ban'
    Modal.confirm({
      title: `${user.is_banned ? 'Unban' : 'Ban'} User`,
      content: `Are you sure you want to ${action} "${user.username || user.email}"?`,
      onOk: () =>
        updateUser.mutateAsync(
          { id: user.id, values: { is_banned: !user.is_banned } },
          {
            onSuccess: () => Message.success(`User ${action}ned`),
          },
        ),
    })
  }

  const handleDelete = (user: UserData) => {
    Modal.confirm({
      title: 'Delete User',
      content: 'Are you sure you want to delete this user? This will ban the user.',
      okButtonProps: { status: 'danger' },
      onOk: () =>
        deleteUser.mutateAsync(user.id, {
          onSuccess: () => Message.success('User deleted'),
        }),
    })
  }

  const columns: ColumnProps<UserData>[] = [
    {
      title: 'User',
      dataIndex: 'username',
      render: (_: unknown, record: UserData) => (
        <Space>
          <Avatar size={36}>
            {record.avatar_url ? (
              <img src={record.avatar_url} alt="" />
            ) : (
              <IconUser />
            )}
          </Avatar>
          <div>
            <Typography.Text>{record.username || 'No username'}</Typography.Text>
            <br />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {record.email || 'No email'}
            </Typography.Text>
          </div>
        </Space>
      ),
    },
    {
      title: 'Role',
      dataIndex: 'role',
      width: 130,
      render: (_: unknown, record: UserData) => (
        <Select
          size="small"
          value={record.role}
          onChange={(value) => handleRoleChange(record.id, value)}
          disabled={updateUser.isPending}
          style={{ width: 110 }}
        >
          {ROLES.map((role) => (
            <Select.Option key={role} value={role}>
              {role.charAt(0).toUpperCase() + role.slice(1)}
            </Select.Option>
          ))}
        </Select>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'is_banned',
      width: 100,
      render: (_: unknown, record: UserData) =>
        record.is_banned ? (
          <Tag icon={<IconCloseCircle />} color="red">
            Banned
          </Tag>
        ) : (
          <Tag icon={<IconCheckCircle />} color="green">
            Active
          </Tag>
        ),
    },
    {
      title: 'Videos',
      dataIndex: 'video_count',
      width: 80,
    },
    {
      title: 'Teams',
      dataIndex: 'team_count',
      width: 80,
    },
    {
      title: 'Joined',
      dataIndex: 'created_at',
      width: 140,
      render: (value: string) => formatDate(value),
    },
    {
      title: 'Actions',
      width: 80,
      align: 'center',
      render: (_: unknown, record: UserData) => (
        <Dropdown
          droplist={
            <Menu>
              <Menu.Item key="ban" onClick={() => handleBanToggle(record)}>
                <Space>
                  <IconStop />
                  {record.is_banned ? 'Unban User' : 'Ban User'}
                </Space>
              </Menu.Item>
              <Menu.Item
                key="delete"
                onClick={() => handleDelete(record)}
                style={{ color: 'rgb(var(--danger-6))' }}
              >
                <Space>
                  <IconDelete />
                  Delete User
                </Space>
              </Menu.Item>
            </Menu>
          }
          position="br"
        >
          <IconMore style={{ cursor: 'pointer', fontSize: 18 }} />
        </Dropdown>
      ),
    },
  ]

  return (
    <div>
      <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 16 }}>
        Users
      </Typography.Title>

      <Card style={{ marginBottom: 16 }}>
        <Space size="medium">
          <Input.Search
            placeholder="Search by email or username..."
            onSearch={handleSearch}
            style={{ width: 320 }}
            allowClear
          />
          <Select
            placeholder="All Roles"
            value={roleFilter}
            onChange={(value) => {
              setRoleFilter(value || undefined)
              setPage(1)
            }}
            allowClear
            style={{ width: 150 }}
          >
            <Select.Option value="admin">Admin</Select.Option>
            <Select.Option value="user">User</Select.Option>
            <Select.Option value="test">Test</Select.Option>
          </Select>
        </Space>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={users}
          loading={isLoading}
          scroll={{ x: 900 }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: true,
            sizeCanChange: false,
          }}
          noDataElement="No users found"
        />
      </Card>
    </div>
  )
}
