import { useState } from 'react'
import { formatDate } from '../../utils/format'
import {
  Table,
  Input,
  Tag,
  Modal,
  Message,
  Card,
  Space,
  Typography,
  Button,
} from '@arco-design/web-react'
import { IconDelete, IconUser } from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import {
  useTeams,
  useTeamMembers,
  useDeleteTeam,
  ROLE_COLOR_MAP,
} from '../../api/endpoints/teams'
import type { Team, TeamMember } from '../../api/endpoints/teams'

const PAGE_SIZE = 20

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

export function TeamList() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [expandedRowKeys, setExpandedRowKeys] = useState<string[]>([])

  const { data, isLoading } = useTeams({ page, pageSize: PAGE_SIZE, search })
  const deleteTeam = useDeleteTeam()

  const teams = data?.items ?? []
  const total = data?.total ?? 0

  const handleSearch = (value: string) => {
    setSearch(value)
    setPage(1)
  }

  const handleDelete = (team: Team) => {
    Modal.confirm({
      title: 'Delete Team',
      content: `Are you sure you want to delete team "${team.name}"? This action cannot be undone.`,
      okButtonProps: { status: 'danger' },
      onOk: () =>
        deleteTeam.mutateAsync(team.id, {
          onSuccess: () => {
            Message.success('Team deleted')
            setExpandedRowKeys((keys) => keys.filter((k) => k !== team.id))
          },
        }),
    })
  }

  const columns: ColumnProps<Team>[] = [
    {
      title: 'Team',
      dataIndex: 'name',
      render: (_: unknown, record: Team) => (
        <Space>
          <IconUser style={{ fontSize: 20, color: 'rgb(var(--primary-6))' }} />
          <div>
            <Typography.Text bold>{record.name}</Typography.Text>
            <br />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Owner: {record.owner_email || 'Unknown'}
            </Typography.Text>
          </div>
        </Space>
      ),
    },
    {
      title: 'Members',
      dataIndex: 'member_count',
      width: 100,
    },
    {
      title: 'Videos',
      dataIndex: 'video_count',
      width: 100,
    },
    {
      title: 'Invite Code',
      dataIndex: 'invite_code',
      width: 200,
      render: (code: string) => (
        <Typography.Paragraph
          copyable
          style={{ margin: 0, fontSize: 13, fontFamily: 'monospace' }}
        >
          {code}
        </Typography.Paragraph>
      ),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 140,
      render: (value: string) => formatDate(value),
    },
    {
      title: 'Actions',
      width: 80,
      align: 'center',
      render: (_: unknown, record: Team) => (
        <Button
          type="text"
          status="danger"
          icon={<IconDelete />}
          onClick={() => handleDelete(record)}
        />
      ),
    },
  ]

  return (
    <div>
      <Typography.Title heading={4} style={{ marginTop: 0, marginBottom: 16 }}>
        Teams
      </Typography.Title>

      <Card style={{ marginBottom: 16 }}>
        <Input.Search
          placeholder="Search by team name..."
          onSearch={handleSearch}
          style={{ width: 320 }}
          allowClear
        />
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          data={teams}
          loading={isLoading}
          scroll={{ x: 1000 }}
          expandedRowKeys={expandedRowKeys}
          onExpandedRowsChange={(keys) => setExpandedRowKeys(keys as string[])}
          expandedRowRender={(record: Team) => (
            <ExpandedMembers teamId={record.id} ownerId={record.owner_id} />
          )}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: setPage,
            showTotal: true,
            sizeCanChange: false,
          }}
          noDataElement="No teams found"
        />
      </Card>
    </div>
  )
}
