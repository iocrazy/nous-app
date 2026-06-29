import { Card, Tag, Spin, Divider } from '@arco-design/web-react'
import { IconStorage } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import { useMemoryStats } from '../../api/endpoints/settings'

// ── Small stat row ────────────────────────────────────────────────────────────
function StatRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '5px 0',
        fontSize: 13,
      }}
    >
      <span style={{ color: 'var(--color-text-3)' }}>{label}</span>
      <span style={{ fontWeight: 500, color: 'var(--color-text-1)' }}>{value}</span>
    </div>
  )
}

// ── Section sub-group ─────────────────────────────────────────────────────────
function StatGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div
        style={{
          fontWeight: 600,
          fontSize: 12,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          color: 'var(--color-text-3)',
          marginBottom: 4,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  )
}

// ── Top-level export ──────────────────────────────────────────────────────────
export function MemoryStatsCard() {
  const { data, isLoading } = useMemoryStats()

  if (isLoading) {
    return (
      <Card title="Agent Memory Stats" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Spin size={28} />
        </div>
      </Card>
    )
  }

  const recallEnabled = data?.recall_enabled ?? false
  const totalActive = data?.total_active ?? 0
  const byVis = data?.by_visibility ?? {}
  const byScope = data?.by_scope ?? {}
  const byStatus = data?.by_status ?? {}
  const promos = data?.promotions ?? {}
  const lastCreatedAt = data?.last_created_at

  const fmt = (n: number | undefined): string => String(n ?? 0)
  const fmtDate = (s: string | null | undefined): string =>
    s ? new Date(s).toLocaleString() : '—'

  return (
    <Card title="Agent Memory Stats" style={{ marginBottom: 20 }}>
      <SectionHeader
        icon={<IconStorage />}
        title="Memory Store Overview"
        subtitle="Live counts from the agent memory store and promotion queue depth."
      />

      {/* Recall status + total active headline */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 32,
          padding: '16px 0 12px',
        }}
      >
        <div>
          <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginBottom: 6 }}>
            Recall
          </div>
          <Tag color={recallEnabled ? 'green' : undefined}>
            {recallEnabled ? 'Active' : 'Disabled'}
          </Tag>
        </div>
        <Divider type="vertical" style={{ height: 36 }} />
        <div>
          <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginBottom: 2 }}>
            Total Active
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, lineHeight: 1, color: 'var(--color-text-1)' }}>
            {totalActive.toLocaleString()}
          </div>
        </div>
      </div>

      <Divider style={{ margin: '4px 0 12px' }} />

      {/* Four-column breakdown grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: '16px 32px',
        }}
      >
        <StatGroup title="By Visibility">
          <StatRow label="Private" value={fmt(byVis['private'])} />
          <StatRow label="Shared" value={fmt(byVis['shared'])} />
        </StatGroup>

        <StatGroup title="By Scope">
          <StatRow label="Agent User" value={fmt(byScope['agent_user'])} />
          <StatRow label="Team" value={fmt(byScope['team'])} />
          <StatRow label="Project" value={fmt(byScope['project'])} />
        </StatGroup>

        <StatGroup title="By Status">
          <StatRow label="Active" value={fmt(byStatus['active'])} />
          <StatRow label="Archived" value={fmt(byStatus['archived'])} />
          <StatRow label="Superseded" value={fmt(byStatus['superseded'])} />
        </StatGroup>

        <StatGroup title="Recent Activity">
          <StatRow label="Created (24 h)" value={fmt(data?.created_24h)} />
          <StatRow label="Created (7 d)" value={fmt(data?.created_7d)} />
          <StatRow label="Last Created" value={fmtDate(lastCreatedAt)} />
        </StatGroup>
      </div>

      <Divider style={{ margin: '12px 0' }} />

      {/* Promotion queue depth */}
      <StatGroup title="Promotion Queue">
        <div style={{ display: 'flex', gap: 24 }}>
          <StatRow label="Pending" value={fmt(promos['pending'])} />
          <StatRow label="Approved" value={fmt(promos['approved'])} />
          <StatRow label="Rejected" value={fmt(promos['rejected'])} />
        </div>
      </StatGroup>
    </Card>
  )
}
