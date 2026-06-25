import { useState } from 'react'
import {
  Table,
  Switch,
  Select,
  Tag,
  Message,
  Spin,
  Button,
  Modal,
  Input,
  InputNumber,
} from '@arco-design/web-react'
import { IconStorage, IconPlus } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import {
  useAdminSources,
  useUpdateAdminSource,
  useCreateAdminSource,
  type AdminSource,
} from '../../api/endpoints/settings'

const HEALTH_COLOR: Record<string, string> = {
  ok: 'green',
  degraded: 'orange',
  dead: 'red',
}

const EMPTY_FORM = { kind: 'newsnow', name: '', configValue: '', category: '', tier: 2 }

export function SignalSources() {
  const { data, isLoading } = useAdminSources()
  const update = useUpdateAdminSource()
  const create = useCreateAdminSource()
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)

  const submitNew = async () => {
    const name = form.name.trim()
    const val = form.configValue.trim()
    if (!name) return Message.error('Name is required')
    if (!val)
      return Message.error(form.kind === 'newsnow' ? 'Platform ID is required' : 'Feed URL is required')
    const config = form.kind === 'newsnow' ? { platform_id: val } : { url: val }
    try {
      await create.mutateAsync({
        kind: form.kind,
        name,
        config,
        category: form.category.trim() || null,
        tier: form.tier,
      })
      Message.success(`${name} added — collects on the next fetch cycle.`)
      setAdding(false)
      setForm(EMPTY_FORM)
    } catch (e) {
      Message.error(`Add failed: ${(e as Error).message}`)
    }
  }

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
      <div style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<IconPlus />} onClick={() => setAdding(true)}>
          Add Source
        </Button>
      </div>

      <Modal
        title="Add Signal Source"
        visible={adding}
        onCancel={() => setAdding(false)}
        onOk={submitNew}
        confirmLoading={create.isPending}
        okText="Add"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginBottom: 4 }}>
              Kind
            </div>
            <Select
              value={form.kind}
              onChange={(v) => setForm({ ...form, kind: v, configValue: '' })}
              options={[
                { label: 'newsnow (hot-list platform)', value: 'newsnow' },
                { label: 'rss (feed URL)', value: 'rss' },
              ]}
            />
          </div>
          <Input
            placeholder="Name (e.g. Hacker News)"
            value={form.name}
            onChange={(v) => setForm({ ...form, name: v })}
          />
          <Input
            placeholder={
              form.kind === 'newsnow'
                ? 'Platform ID (e.g. weibo, hackernews, github-trending-today)'
                : 'Feed URL (https://…)'
            }
            value={form.configValue}
            onChange={(v) => setForm({ ...form, configValue: v })}
          />
          <Input
            placeholder="Category (optional, e.g. model / product / industry)"
            value={form.category}
            onChange={(v) => setForm({ ...form, category: v })}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 13 }}>Tier</span>
            <InputNumber
              mode="button"
              min={1}
              max={3}
              value={form.tier}
              onChange={(v) => setForm({ ...form, tier: Number(v) || 2 })}
              style={{ width: 100 }}
            />
            <span style={{ fontSize: 12, color: 'var(--color-text-3)' }}>
              1 = official/primary · 3 = noisy aggregator
            </span>
          </div>
        </div>
      </Modal>

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
