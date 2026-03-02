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
import { useSystemSettings, useUpdateSetting } from '../../api/endpoints/settings'
import type { SystemSetting } from '../../api/endpoints/settings'

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

  const isBool = typeof value === 'boolean'
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
            checked={value}
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

  // Group settings: booleans first, then numbers, then strings
  const boolSettings = (settings || []).filter((s) => typeof s.value === 'boolean')
  const numberSettings = (settings || []).filter((s) => typeof s.value === 'number')
  const stringSettings = (settings || []).filter(
    (s) => typeof s.value !== 'boolean' && typeof s.value !== 'number',
  )

  return (
    <div>
      <Title heading={5} style={{ marginTop: 0, marginBottom: 20 }}>
        Settings
      </Title>

      <Card title="General" style={{ marginBottom: 20 }}>
        {stringSettings.map((s, i) => (
          <div key={s.key}>
            {i > 0 && <Divider style={{ margin: 0 }} />}
            <SettingRow setting={s} onUpdate={handleUpdate} loading={updateMutation.isPending} />
          </div>
        ))}
        {stringSettings.length === 0 && (
          <div style={{ color: 'var(--color-text-3)', padding: '16px 0' }}>No general settings</div>
        )}
      </Card>

      <Card title="Feature Toggles" style={{ marginBottom: 20 }}>
        {boolSettings.map((s, i) => (
          <div key={s.key}>
            {i > 0 && <Divider style={{ margin: 0 }} />}
            <SettingRow setting={s} onUpdate={handleUpdate} loading={updateMutation.isPending} />
          </div>
        ))}
      </Card>

      <Card title="Limits & Quotas">
        {numberSettings.map((s, i) => (
          <div key={s.key}>
            {i > 0 && <Divider style={{ margin: 0 }} />}
            <SettingRow setting={s} onUpdate={handleUpdate} loading={updateMutation.isPending} />
          </div>
        ))}
      </Card>
    </div>
  )
}
