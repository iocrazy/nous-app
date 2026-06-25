import {
  Card,
  Typography,
  Switch,
  Input,
  InputNumber,
  Message,
  Spin,
  Divider,
} from '@arco-design/web-react'
import { IconSettings, IconThunderbolt } from '@arco-design/web-react/icon'
import { useSystemSettings, useUpdateSetting } from '../../api/endpoints/settings'
import type { SystemSetting } from '../../api/endpoints/settings'
import { SectionHeader } from './SectionHeader'

const { Title } = Typography

function SettingRow({
  setting,
  onUpdate,
  loading,
}: {
  setting: SystemSetting
  onUpdate: (key: string, value: any) => void
  loading: boolean
}) {
  const { key, value, description } = setting

  // Treat a JSONB bool OR a "true"/"false" string as a boolean toggle (some
  // settings were seeded as strings). Toggling writes back a real bool.
  const isBool = typeof value === 'boolean' || value === 'true' || value === 'false'
  const boolChecked = value === true || value === 'true'
  const isNumber = typeof value === 'number'

  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 0' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 500, fontSize: 14 }}>{formatLabel(key)}</div>
        {description && (
          <div style={{ color: 'var(--color-text-3)', fontSize: 13, marginTop: 4 }}>{description}</div>
        )}
      </div>
      <div style={{ marginLeft: 24, minWidth: 200, textAlign: 'right' }}>
        {isBool && (
          <Switch
            checked={boolChecked}
            disabled={loading}
            onChange={(checked) => onUpdate(key, checked)}
          />
        )}
        {isNumber && (
          <InputNumber
            value={value}
            disabled={loading}
            min={0}
            style={{ width: 160 }}
            onChange={(val) => {
              if (val !== undefined && val !== null) onUpdate(key, val)
            }}
          />
        )}
        {!isBool && !isNumber && (
          <Input
            value={String(value ?? '')}
            disabled={loading}
            style={{ width: 240 }}
            onPressEnter={(e) => onUpdate(key, (e.target as HTMLInputElement).value)}
            onBlur={(e) => {
              if (e.target.value !== String(value ?? '')) {
                onUpdate(key, e.target.value)
              }
            }}
          />
        )}
      </div>
    </div>
  )
}

function formatLabel(key: string): string {
  return key
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export function Settings() {
  const { data: settings, isLoading } = useSystemSettings()
  const updateMutation = useUpdateSetting()

  const handleUpdate = (key: string, value: any) => {
    updateMutation.mutate(
      { key, value },
      {
        onSuccess: () => Message.success(`Setting "${formatLabel(key)}" updated`),
        onError: (err) => Message.error(err.message || 'Failed to update setting'),
      },
    )
  }

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 80 }}>
        <Spin size={32} />
      </div>
    )
  }

  // These keys are owned by the dedicated panels rendered below — keep them out
  // of the generic raw rows so they don't appear twice (overlap) + cluttered:
  //   graph_*                      → Memory (Graphiti) panel
  //   ai_module.* / nous.*         → AI Config Governance panel (lock + per-module
  //                                  base_url/model/api_key, embedding included)
  const HIDDEN_PREFIXES = ['graph_', 'ai_module.', 'nous.']
  const generic = (settings || []).filter(
    (s) => !HIDDEN_PREFIXES.some((p) => s.key.startsWith(p)),
  )
  // Categorise by key DOMAIN (prefix) rather than by value type — type-grouping
  // scattered related keys (e.g. transcode_*) across three cards. Each
  // SettingRow already renders the right control for its value type. Unmatched
  // keys fall into "General".
  const DOMAIN_SECTIONS: ReadonlyArray<{
    title: string
    prefix: string
    subtitle: string
    icon: React.ReactNode
  }> = [
    {
      title: 'Transcoding',
      prefix: 'transcode_',
      subtitle: 'Video transcode encoder / preset / tiers.',
      icon: <IconThunderbolt />,
    },
  ]
  const claimed = new Set<string>()
  const domainCards = DOMAIN_SECTIONS.map((sec) => {
    const items = generic.filter((s) => s.key.startsWith(sec.prefix))
    items.forEach((s) => claimed.add(s.key))
    return { ...sec, items }
  }).filter((c) => c.items.length > 0)
  const generalSettings = generic.filter((s) => !claimed.has(s.key))

  const renderRows = (items: typeof generic) =>
    items.map((s, i) => (
      <div key={s.key}>
        {i > 0 && <Divider style={{ margin: 0 }} />}
        <SettingRow setting={s} onUpdate={handleUpdate} loading={updateMutation.isPending} />
      </div>
    ))

  return (
    <div>
      <Title heading={5} style={{ marginTop: 0, marginBottom: 20 }}>
        Settings
      </Title>

      <Card style={{ marginBottom: 20 }}>
        <SectionHeader
          icon={<IconSettings />}
          title="General"
          subtitle="Miscellaneous platform settings."
        />
        {renderRows(generalSettings)}
        {generalSettings.length === 0 && (
          <div style={{ color: 'var(--color-text-3)', padding: '16px 0' }}>No general settings</div>
        )}

        {domainCards.map((c) => (
          <div key={c.title}>
            <Divider />
            <SectionHeader icon={c.icon} title={c.title} subtitle={c.subtitle} />
            {renderRows(c.items)}
          </div>
        ))}
      </Card>
    </div>
  )
}
