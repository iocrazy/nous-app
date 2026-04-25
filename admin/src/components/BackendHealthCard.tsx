// admin/src/components/BackendHealthCard.tsx
//
// Backend services health panel — shows per-component status (DB, trigram
// indexes, Celery worker, Redis) with green/yellow/red dot indicators
// like an openclaw-style gateway dashboard. Refreshes every 10s.

import { Card, Tag, Tooltip, Typography } from '@arco-design/web-react'
import { IconLoading, IconRefresh } from '@arco-design/web-react/icon'
import {
  useBackendHealth,
  type HealthCheck,
} from '../api/endpoints/health'

const STATUS_COLORS: Record<HealthCheck['status'], string> = {
  healthy: '#00b42a',
  degraded: '#ff7d00',
  down: '#f53f3f',
  unknown: '#86909c',
}

const STATUS_LABELS: Record<HealthCheck['status'], string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
  unknown: 'Unknown',
}

function StatusDot({ status }: { status: HealthCheck['status'] }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        background: STATUS_COLORS[status],
        boxShadow: `0 0 0 3px ${STATUS_COLORS[status]}33`,
        marginRight: 8,
        flexShrink: 0,
      }}
      aria-label={STATUS_LABELS[status]}
    />
  )
}

interface BackendHealthCardProps {
  /** Hide the card title (useful when embedded in a parent Card). */
  bare?: boolean
}

export function BackendHealthCard({ bare = false }: BackendHealthCardProps) {
  const { data, isLoading, isFetching, refetch } = useBackendHealth()

  const overall = data?.overall ?? 'unknown'
  const overallColor = STATUS_COLORS[overall as HealthCheck['status']]
  const overallLabel = STATUS_LABELS[overall as HealthCheck['status']]

  const body = (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
        <StatusDot status={overall as HealthCheck['status']} />
        <Typography.Text style={{ fontWeight: 600, fontSize: 14 }}>
          Backend services: {overallLabel}
        </Typography.Text>
        <Typography.Text
          type="secondary"
          style={{ marginLeft: 'auto', fontSize: 12 }}
        >
          {data?.timestamp
            ? new Date(data.timestamp * 1000).toLocaleTimeString()
            : '—'}
          {isFetching && (
            <IconLoading style={{ marginLeft: 6, color: overallColor }} />
          )}
        </Typography.Text>
      </div>

      {isLoading ? (
        <Typography.Text type="secondary">Probing...</Typography.Text>
      ) : !data?.checks?.length ? (
        <Typography.Text type="secondary">No probes returned.</Typography.Text>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {data.checks.map((c) => (
            <div
              key={c.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                padding: '6px 8px',
                background: 'var(--color-fill-1)',
                borderRadius: 4,
              }}
            >
              <StatusDot status={c.status} />
              <span style={{ flex: 1, fontSize: 13 }}>{c.name}</span>
              <Tooltip content={c.detail || STATUS_LABELS[c.status]}>
                <Tag
                  color={
                    c.status === 'healthy'
                      ? 'green'
                      : c.status === 'degraded'
                        ? 'orange'
                        : c.status === 'down'
                          ? 'red'
                          : 'gray'
                  }
                  size="small"
                >
                  {c.detail || STATUS_LABELS[c.status]}
                </Tag>
              </Tooltip>
            </div>
          ))}
        </div>
      )}
    </div>
  )

  if (bare) return body

  return (
    <Card
      title="Backend Services"
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
      {body}
    </Card>
  )
}

export default BackendHealthCard
