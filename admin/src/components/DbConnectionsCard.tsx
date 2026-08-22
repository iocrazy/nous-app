// admin/src/components/DbConnectionsCard.tsx
//
// Postgres connection-slot pressure — usage against max_connections plus the
// per-client breakdown that answers "who is holding them?".
//
// Why this panel exists: the cluster ceiling was 100 while the self-hosted
// Supabase stack's own fixed floor is 60-70 connections, so a deploy window
// running old and new pools at once tipped it to 106/100 on 2026-08-21. At
// that point psql could not connect either — the breakdown you would
// normally shell in for is exactly what stops being available, which is why
// it belongs in the admin UI.
//
// Thresholds are NOT hardcoded here: `warn_pct` / `critical_pct` come from
// the response so this badge and the scheduled WARNING log always agree.

import { Card, Progress, Table, Tag, Tooltip, Typography } from '@arco-design/web-react'
import { IconLoading, IconRefresh } from '@arco-design/web-react/icon'
import type { ColumnProps } from '@arco-design/web-react/es/Table'
import {
  useDbConnections,
  type ConnectionGroupEntry,
  type ConnectionStatus,
} from '../api/endpoints/monitoring'

const STATUS_COLORS: Record<ConnectionStatus, string> = {
  ok: '#00b42a',
  warning: '#ff7d00',
  critical: '#f53f3f',
  unknown: '#86909c',
}

const STATUS_LABELS: Record<ConnectionStatus, string> = {
  ok: 'Healthy',
  warning: 'Elevated',
  critical: 'Critical',
  // Deliberately not "Healthy" — a reading that could not be taken must never
  // render as a green light. A broken probe is worse than no probe.
  unknown: 'No reading',
}

const TAG_COLORS: Record<ConnectionStatus, string> = {
  ok: 'green',
  warning: 'orange',
  critical: 'red',
  unknown: 'gray',
}

/** Seconds → a compact human duration ("42s", "9m", "3h"). */
function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

const columns: ColumnProps<ConnectionGroupEntry>[] = [
  {
    title: 'Application',
    dataIndex: 'application_name',
    render: (value: string) => (
      <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{value}</span>
    ),
  },
  {
    title: 'User',
    dataIndex: 'usename',
    render: (value: string) => (
      <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{value}</span>
    ),
  },
  {
    title: 'State',
    dataIndex: 'state',
    render: (value: string) => (
      <Tag
        size="small"
        // "idle in transaction" is the one state worth flagging: it holds a
        // slot indefinitely while doing nothing, and is how this project has
        // starved the pool before.
        color={value === 'idle in transaction' ? 'orange' : 'gray'}
      >
        {value}
      </Tag>
    ),
  },
  {
    title: 'Count',
    dataIndex: 'count',
    align: 'right',
    sorter: (a: ConnectionGroupEntry, b: ConnectionGroupEntry) =>
      a.count - b.count,
  },
  {
    title: 'Oldest',
    dataIndex: 'oldest_state_seconds',
    align: 'right',
    render: (value: number) => (
      <span style={{ fontSize: 12 }}>{formatAge(value)}</span>
    ),
  },
]

export function DbConnectionsCard() {
  const { data, isLoading, isFetching, refetch } = useDbConnections()

  const status: ConnectionStatus = data?.status ?? 'unknown'
  const color = STATUS_COLORS[status]
  const percent = data?.percent ?? 0

  return (
    <Card
      title="Database Connections"
      extra={
        <a
          href="#"
          onClick={(e) => {
            e.preventDefault()
            refetch()
          }}
          style={{ fontSize: 12, color: 'var(--color-text-2)' }}
        >
          <IconRefresh /> Refresh
        </a>
      }
    >
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
        <span
          style={{
            display: 'inline-block',
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: color,
            boxShadow: `0 0 0 3px ${color}33`,
            marginRight: 8,
            flexShrink: 0,
          }}
          aria-label={STATUS_LABELS[status]}
        />
        <Typography.Text style={{ fontWeight: 600, fontSize: 14 }}>
          {data && data.used !== null && data.max_connections !== null
            ? `${data.used} / ${data.max_connections} slots in use (${percent}%)`
            : 'Connection usage unavailable'}
        </Typography.Text>
        <Tag color={TAG_COLORS[status]} size="small" style={{ marginLeft: 10 }}>
          {STATUS_LABELS[status]}
        </Tag>
        {isFetching && <IconLoading style={{ marginLeft: 8, color }} />}
      </div>

      {status === 'unknown' ? (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {isLoading
            ? 'Probing...'
            : `Could not read pg_stat_activity: ${data?.reason ?? 'unknown reason'}`}
        </Typography.Text>
      ) : (
        <>
          <Tooltip
            content={`Warning at ${data?.warn_pct}%, critical at ${data?.critical_pct}%`}
          >
            <Progress
              percent={Math.min(percent, 100)}
              color={color}
              showText={false}
              style={{ marginBottom: 12 }}
            />
          </Tooltip>

          {!!data?.idle_in_transaction && (
            <Typography.Text
              type="secondary"
              style={{ fontSize: 12, display: 'block', marginBottom: 12 }}
            >
              {data.idle_in_transaction} idle in transaction — oldest{' '}
              {formatAge(data.oldest_idle_in_transaction_seconds ?? 0)}. These
              hold a slot while doing nothing; a long one is a bug in the
              caller, not load.
            </Typography.Text>
          )}

          <Table
            columns={columns}
            data={data?.groups ?? []}
            rowKey={(r) => `${r.application_name}|${r.usename}|${r.state}`}
            pagination={{ pageSize: 10, sizeCanChange: false }}
            size="small"
            loading={isLoading}
            border={false}
            noDataElement={
              <Typography.Text type="secondary">
                No client backends reported.
              </Typography.Text>
            }
          />
        </>
      )}
    </Card>
  )
}

export default DbConnectionsCard
