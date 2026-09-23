import { useEffect, useState } from 'react'
import { Card, InputNumber, Button, Message, Spin } from '@arco-design/web-react'
import { IconExclamationCircle } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import {
  useAgentCostAnomalyConfig,
  useUpdateAgentCostAnomalyConfig,
} from '../../api/endpoints/settings'

/**
 * Floor for the hourly agent cost anomaly sweep
 * (backend/app/workflows/agent_cost_anomaly.py): an agent-hour must cost at
 * least this much before a spike raises an alert on the Alerts page.
 * Backed by GET/PUT /api/v1/admin/settings/agent-cost-anomaly (upsert, so no
 * seed row is needed); the detector re-reads it every tick.
 */
export function AgentCostAnomaly() {
  const { data, isLoading, error } = useAgentCostAnomalyConfig()
  const update = useUpdateAgentCostAnomalyConfig()
  const [value, setValue] = useState<number | null>(null)

  useEffect(() => {
    if (data) setValue(data.min_hour_cost_cents)
  }, [data])

  const save = async () => {
    if (value === null) return
    try {
      await update.mutateAsync({ min_hour_cost_cents: value })
      Message.success('Cost anomaly floor saved — applies on the next hourly sweep.')
    } catch (e) {
      Message.error(`Save failed: ${(e as Error).message}`)
    }
  }

  const unchanged = data !== undefined && value === data.min_hour_cost_cents

  return (
    <Card style={{ marginBottom: 20 }}>
      <SectionHeader
        icon={<IconExclamationCircle />}
        title="Agent Cost Anomaly"
        subtitle="Floor for the hourly agent cost anomaly alert."
      />
      {isLoading ? (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Spin />
        </div>
      ) : error ? (
        <div style={{ color: 'var(--color-danger-6)', padding: '16px 0' }}>
          Failed to load: {(error as Error).message}
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '16px 0' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500, fontSize: 14 }}>
              Minimum agent-hour cost to alert (cents)
            </div>
            <div style={{ color: 'var(--color-text-3)', fontSize: 13, marginTop: 4 }}>
              Agent-hours cheaper than this never raise a cost-anomaly alert. Default 0.5¢
              (production p75 of per-hour own cost).
            </div>
          </div>
          <InputNumber
            aria-label="Minimum agent-hour cost to alert (cents)"
            min={0}
            step={0.1}
            precision={2}
            value={value ?? undefined}
            onChange={(v) => setValue(typeof v === 'number' && Number.isFinite(v) ? v : null)}
            style={{ width: 160 }}
          />
          <Button
            type="primary"
            loading={update.isPending}
            disabled={value === null || unchanged || update.isPending}
            onClick={save}
          >
            Save
          </Button>
        </div>
      )}
    </Card>
  )
}
