import { useMemo } from 'react'
import {
  Select,
  Tag,
  Avatar,
  Dropdown,
  Menu,
  Modal,
  Message,
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
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import type { UserData } from '../../api/endpoints/users'
import { useUpdateUser, useDeleteUser } from '../../api/endpoints/users'
import { formatDate } from '../../utils/format'

// --- Constants ---

const ROLES = ['admin', 'user', 'test'] as const

const ROLE_FILTER_OPTIONS = [
  { label: 'Admin', value: 'admin' },
  { label: 'User', value: 'user' },
  { label: 'Test', value: 'test' },
]

// --- Sub-components ---

function UserCell({ record }: { record: UserData }) {
  return (
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
  )
}

function RoleCell({
  record,
  isPending,
  onRoleChange,
}: {
  record: UserData
  isPending: boolean
  onRoleChange: (userId: string, role: string) => void
}) {
  return (
    <Select
      size="small"
      value={record.role}
      onChange={(value) => onRoleChange(record.id, value)}
      disabled={isPending}
      style={{ width: 110 }}
    >
      {ROLES.map((role) => (
        <Select.Option key={role} value={role}>
          {role.charAt(0).toUpperCase() + role.slice(1)}
        </Select.Option>
      ))}
    </Select>
  )
}

function StatusCell({ record }: { record: UserData }) {
  return record.is_banned ? (
    <Tag icon={<IconCloseCircle />} color="red">
      Banned
    </Tag>
  ) : (
    <Tag icon={<IconCheckCircle />} color="green">
      Active
    </Tag>
  )
}

function ActionsCell({
  record,
  onBanToggle,
  onDelete,
}: {
  record: UserData
  onBanToggle: (user: UserData) => void
  onDelete: (user: UserData) => void
}) {
  return (
    <Dropdown
      droplist={
        <Menu>
          <Menu.Item key="ban" onClick={() => onBanToggle(record)}>
            <Space>
              <IconStop />
              {record.is_banned ? 'Unban User' : 'Ban User'}
            </Space>
          </Menu.Item>
          <Menu.Item
            key="delete"
            onClick={() => onDelete(record)}
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
  )
}

// --- Main component ---

export function UserList() {
  const updateUser = useUpdateUser()
  const deleteUser = useDeleteUser()

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

  const columns = useMemo<NotionColumnDef<UserData>[]>(
    () => [
      {
        key: 'username',
        header: 'User',
        type: 'text',
        filterable: true,
        sortable: true,
        required: true,
        minSize: 200,
        cell: (row) => <UserCell record={row} />,
      },
      {
        key: 'role',
        header: 'Role',
        type: 'select',
        filterable: true,
        size: 140,
        filterOptions: ROLE_FILTER_OPTIONS,
        cell: (row) => (
          <RoleCell
            record={row}
            isPending={updateUser.isPending}
            onRoleChange={handleRoleChange}
          />
        ),
      },
      {
        key: 'is_banned',
        header: 'Status',
        type: 'boolean',
        filterable: true,
        size: 110,
        cell: (row) => <StatusCell record={row} />,
      },
      {
        key: 'video_count',
        header: 'Videos',
        type: 'number',
        sortable: true,
        size: 80,
      },
      {
        key: 'team_count',
        header: 'Teams',
        type: 'number',
        sortable: true,
        size: 80,
      },
      {
        key: 'created_at',
        header: 'Joined',
        type: 'date',
        filterable: true,
        sortable: true,
        size: 140,
        cell: (row) => formatDate(row.created_at),
      },
      {
        key: 'actions',
        header: 'Actions',
        type: 'text',
        required: true,
        size: 80,
        cell: (row) => (
          <ActionsCell
            record={row}
            onBanToggle={handleBanToggle}
            onDelete={handleDelete}
          />
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [updateUser.isPending],
  )

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
  } = useNotionTable<UserData>({
    tableKey: 'users',
    columns,
    defaultSorts: [{ field: 'created_at', direction: 'desc' }],
    fetchData: async ({ page, pageSize, filters, sorts, search }) => {
      const roleFilter = filters.find((f) => f.field === 'role')
      const sortBy = sorts[0]?.field
      const sortOrder = sorts[0]?.direction

      const { data } = await apiClient.get('/api/v1/admin/users', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
          ...(roleFilter?.value && { role: roleFilter.value }),
          ...(sortBy && { sort_by: sortBy }),
          ...(sortOrder && { sort_order: sortOrder }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  return (
    <NotionTable<UserData>
      table={table}
      toolbarProps={toolbarProps}
      pagination={pagination}
      onPageChange={setPage}
      isLoading={isLoading}
      title="Users"
      emptyText="No users found"
      scrollX={900}
    />
  )
}
