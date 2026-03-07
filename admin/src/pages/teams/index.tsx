import { useMemo } from 'react'
import {
  Table,
  Tag,
  Modal,
  Message,
  Typography,
  Button,
  Space,
} from '@arco-design/web-react'
import { IconDelete, IconUser } from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import type { Team, TeamMember } from '../../api/endpoints/teams'
import { useTeamMembers, useDeleteTeam, ROLE_COLOR_MAP } from '../../api/endpoints/teams'
import { formatDate } from '../../utils/format'

// --- Expanded members sub-table (Arco Design) ---

function ExpandedMembers({ teamId, ownerId }: { teamId: string; ownerId: string }) {
  const { data: members = [], isLoading } = useTeamMembers(teamId)

  const memberColumns: ColumnProps<TeamMember>[] = [
    {
      title: 'User',
      dataIndex: 'username',
      render: (_: unknown, record: TeamMember) => (
        <div>
          <Typography.Text>{record.username || 'No username'}</Typography.Text>
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.email}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Role',
      dataIndex: 'role',
      width: 120,
      render: (_: unknown, record: TeamMember) => {
        const role = record.user_id === ownerId ? 'owner' : record.role
        const label = role.charAt(0).toUpperCase() + role.slice(1)
        return <Tag color={ROLE_COLOR_MAP[role] || ''}>{label}</Tag>
      },
    },
    {
      title: 'Joined',
      dataIndex: 'joined_at',
      width: 140,
      render: (value: string) => formatDate(value),
    },
  ]

  return (
    <Table
      rowKey="user_id"
      columns={memberColumns}
      data={members}
      loading={isLoading}
      pagination={false}
      size="small"
      noDataElement="No members found"
      border={false}
    />
  )
}

// --- Main component ---

export function TeamList() {
  const deleteTeam = useDeleteTeam()

  const handleDelete = (team: Team) => {
    Modal.confirm({
      title: 'Delete Team',
      content: `Are you sure you want to delete team "${team.name}"? This action cannot be undone.`,
      okButtonProps: { status: 'danger' },
      onOk: () =>
        deleteTeam.mutateAsync(team.id, {
          onSuccess: () => {
            Message.success('Team deleted')
          },
        }),
    })
  }

  const columns = useMemo<NotionColumnDef<Team>[]>(
    () => [
      {
        key: 'name',
        header: 'Team',
        type: 'text',
        filterable: true,
        sortable: true,
        required: true,
        minSize: 200,
        cell: (row) => (
          <Space>
            <IconUser style={{ fontSize: 20, color: 'rgb(var(--primary-6))' }} />
            <div>
              <Typography.Text bold>{row.name}</Typography.Text>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Owner: {row.owner_email || 'Unknown'}
              </Typography.Text>
            </div>
          </Space>
        ),
      },
      {
        key: 'member_count',
        header: 'Members',
        type: 'number',
        sortable: true,
        size: 100,
      },
      {
        key: 'video_count',
        header: 'Videos',
        type: 'number',
        sortable: true,
        size: 100,
      },
      {
        key: 'invite_code',
        header: 'Invite Code',
        type: 'text',
        size: 200,
        cell: (row) => (
          <Typography.Paragraph
            copyable
            style={{ margin: 0, fontSize: 13, fontFamily: 'monospace' }}
          >
            {row.invite_code}
          </Typography.Paragraph>
        ),
      },
      {
        key: 'created_at',
        header: 'Created',
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
          <Button
            type="text"
            status="danger"
            icon={<IconDelete />}
            onClick={(e) => {
              e.stopPropagation()
              handleDelete(row)
            }}
          />
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
  } = useNotionTable<Team>({
    tableKey: 'teams',
    columns,
    defaultSorts: [{ field: 'created_at', direction: 'desc' }],
    fetchData: async ({ page, pageSize, search }) => {
      const { data } = await apiClient.get('/api/v1/admin/teams', {
        params: {
          page,
          page_size: pageSize,
          ...(search && { search }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  return (
    <NotionTable<Team>
      table={table}
      toolbarProps={toolbarProps}
      pagination={pagination}
      onPageChange={setPage}
      isLoading={isLoading}
      title="Teams"
      emptyText="No teams found"
      expandedRowRender={(row) => (
        <ExpandedMembers teamId={row.id} ownerId={row.owner_id} />
      )}
      scrollX={1000}
    />
  )
}
