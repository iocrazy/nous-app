import { Table, Switch, Select, Tag, Message, Spin } from '@arco-design/web-react'
import { IconStorage } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import {
  useAdminSources,
  useUpdateAdminSource,
  type AdminSource,
} from '../../api/endpoints/settings'

const HEALTH_COLOR: Record<string, string> = {
  ok: 'green',
  degraded: 'orange',
  dead: 'red',
}

export function SignalSources() {
  const { data, isLoading } = useAdminSources()
  const update = useUpdateAdminSource()

  const toggle = async (row: AdminSource, enabled: boolean) => {
    try {
      await update.mutateAsync({ id: row.id, enabled })
      Message.success(
        enabled
          ? `${row.name} enabled — collection resumes next cycle.`
          : `${row.name} disabled — stops collecting and hidden from all feeds.`,
      )
    } catch (e) {
      Message.error(`Failed: ${(e as Error).message}`)
    }
  }

  const setTier = async (row: AdminSource, tier: number) => {
    try {
      await update.mutateAsync({ id: row.id, tier })
      Message.success(`${row.name} tier → ${tier}`)
    } catch (e) {
      Message.error(`Failed: ${(e as Error).message}`)
    }
  }

  if (isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  const columns = [
    {
      title: 'Source',
      dataIndex: 'name',
      render: (_: unknown, row: AdminSource) => (
        <span>
          {row.name}{' '}
          {!row.is_system && (
            <Tag size="small" color="arcoblue">
              user
            </Tag>
          )}
        </span>
      ),
    },
    { title: 'Kind', dataIndex: 'kind', width: 110 },
    {
      title: 'Tier',
      dataIndex: 'tier',
      width: 110,
      render: (_: unknown, row: AdminSource) => (
        <Select
          size="small"
          value={row.tier}
          style={{ width: 80 }}
          onChange={(v) => setTier(row, Number(v))}
          options={[
            { label: '1', value: 1 },
            { label: '2', value: 2 },
            { label: '3', value: 3 },
          ]}
        />
      ),
    },
    {
      title: 'Health',
      dataIndex: 'health',
      width: 110,
      render: (_: unknown, row: AdminSource) => (
        <Tag color={HEALTH_COLOR[row.health] || 'gray'}>{row.health}</Tag>
      ),
    },
    {
      title: 'Enabled',
      dataIndex: 'enabled',
      width: 100,
      render: (_: unknown, row: AdminSource) => (
        <Switch
          checked={row.enabled}
          loading={update.isPending}
          onChange={(v) => toggle(row, v)}
        />
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 860 }}>
      <SectionHeader
        icon={<IconStorage />}
        title="Signal Sources"
        subtitle="Enable/disable sources globally and set credibility tier. Disabling stops collection AND removes the source's hotspots from every user's feed."
      />
      <Table
        rowKey="id"
        columns={columns}
        data={data || []}
        pagination={false}
        size="small"
        border={{ wrapper: true, cell: true }}
      />
    </div>
  )
}
