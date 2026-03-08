import { useMemo } from 'react'
import {
  Table,
  Tag,
  Modal,
  Message,
  Typography,
  Button,
  Badge,
} from '@arco-design/web-react'
import { IconDelete, IconUser, IconUserGroup } from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import type { Team, TeamMember } from '../../api/endpoints/teams'
import { useTeamMembers, useDeleteTeam, ROLE_COLOR_MAP } from '../../api/endpoints/teams'
import { formatDate } from '../../utils/format'

// --- Expanded members sub-table ---

function ExpandedMembers({ teamId, ownerId }: { teamId: string; ownerId: string }) {
  const { data: members = [], isLoading } = useTeamMembers(teamId)

  const memberColumns: ColumnProps<TeamMember>[] = [
    {
      title: 'User',
      dataIndex: 'username',
      render: (_: unknown, record: TeamMember) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: '50%',
              background: 'var(--color-fill-2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 12,
              color: 'var(--color-text-2)',
              flexShrink: 0,
            }}
          >
            {(record.username || record.email || '?')[0].toUpperCase()}
          </div>
          <div>
            <Typography.Text style={{ fontSize: 13 }}>
              {record.username || 'No username'}
            </Typography.Text>
            <br />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {record.email}
            </Typography.Text>
          </div>
        </div>
      ),
    },
    {
      title: 'User ID',
      dataIndex: 'user_id',
      width: 160,
      render: (value: string) => (
        <Typography.Paragraph
          copyable
          style={{ margin: 0, fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-3)' }}
        >
          {value}
        </Typography.Paragraph>
      ),
    },
    {
      title: 'Role',
      dataIndex: 'role',
      width: 100,
      render: (_: unknown, record: TeamMember) => {
        const role = record.user_id === ownerId ? 'owner' : record.role
        const label = role.charAt(0).toUpperCase() + role.slice(1)
        return <Tag color={ROLE_COLOR_MAP[role] || ''} size="small">{label}</Tag>
      },
    },
    {
      title: 'Joined',
      dataIndex: 'joined_at',
      width: 120,
      render: (value: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {formatDate(value)}
        </Typography.Text>
      ),
    },
  ]

  return (
    <div
      style={{
        borderLeft: '3px solid rgb(var(--primary-6))',
        marginLeft: 16,
        paddingLeft: 16,
        paddingTop: 8,
        paddingBottom: 8,
      }}
    >
      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
        <IconUserGroup style={{ color: 'var(--color-text-3)', fontSize: 14 }} />
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          Members ({members.length})
        </Typography.Text>
      </div>
      <Table
        rowKey="user_id"
        columns={memberColumns}
        data={members}
        loading={isLoading}
        pagination={false}
        size="small"
        noDataElement="No members found"
        border={false}
        style={{ background: 'var(--color-fill-1)', borderRadius: 6 }}
      />
    </div>
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
        minSize: 280,
        cell: (row) => (
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: 6,
                background: row.is_personal
                  ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
                  : 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                marginTop: 2,
              }}
            >
              {row.is_personal
                ? <IconUser style={{ fontSize: 16, color: '#fff' }} />
                : <IconUserGroup style={{ fontSize: 16, color: '#fff' }} />
              }
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Typography.Text bold ellipsis>{row.name}</Typography.Text>
                {row.is_personal && (
                  <Tag size="small" color="arcoblue">Personal</Tag>
                )}
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Owner: {row.owner_email || 'Unknown'}
              </Typography.Text>
              <br />
              <Typography.Paragraph
                copyable={{ text: row.id }}
                style={{
                  margin: 0,
                  fontSize: 11,
                  fontFamily: 'monospace',
                  color: 'var(--color-text-3)',
                }}
              >
                ID: {row.id}
              </Typography.Paragraph>
            </div>
          </div>
        ),
      },
      {
        key: 'member_count',
        header: 'Members',
        type: 'number',
        sortable: true,
        size: 90,
      },
      {
        key: 'points_balance',
        header: 'Points',
        type: 'number',
        sortable: true,
        size: 100,
        cell: (row) => (
          <Badge
            count={row.points_balance}
            maxCount={999999}
            dotStyle={{
              background: row.points_balance > 0 ? 'rgb(var(--green-6))' : 'var(--color-fill-3)',
              color: row.points_balance > 0 ? '#fff' : 'var(--color-text-3)',
            }}
          />
        ),
      },
      {
        key: 'invite_code',
        header: 'Invite Code',
        type: 'text',
        size: 160,
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
        size: 120,
        cell: (row) => (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {formatDate(row.created_at)}
          </Typography.Text>
        ),
      },
      {
        key: 'actions',
        header: '',
        type: 'text',
        required: true,
        size: 60,
        cell: (row) => (
          <Button
            type="text"
            status="danger"
            icon={<IconDelete />}
            size="small"
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
      scrollX={900}
    />
  )
}
