import { Card, Switch, Message, Tag, Spin } from '@arco-design/web-react'
import { IconThunderbolt } from '@arco-design/web-react/icon'
import {
  useModules,
  useUpdateModule,
  type ModuleSummary,
} from '../../api/endpoints/settings'
import { SectionHeader } from './SectionHeader'

/**
 * Module Control Center — one card per product module, each with a Processing
 * switch (backend behavior) and a Visibility switch (frontend nav + pages).
 * Registry-driven: adding a module server-side makes a card appear here with no
 * frontend change. Opt-in (default-off) modules are badged.
 */
export function Modules() {
  const { data: modules, isLoading } = useModules()
  const update = useUpdateModule()

  const patch = (
    m: ModuleSummary,
    partial: Partial<Pick<ModuleSummary, 'enabled' | 'visible'>>,
    okMsg: string,
  ) => {
    update.mutate(
      { id: m.id, enabled: m.enabled, visible: m.visible, ...partial },
      {
        onSuccess: () => Message.success(okMsg),
        onError: () => Message.error('Update failed'),
      },
    )
  }

  if (isLoading || !modules) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <div>
      {modules.map((m) => {
        const isUpdating = update.isPending && update.variables?.id === m.id
        return (
        <Card key={m.id} style={{ marginBottom: 20 }}>
          <SectionHeader
            icon={<IconThunderbolt />}
            title={
              <span>
                {m.label}
                {!m.enabled_default && (
                  <Tag color="orange" size="small" style={{ marginLeft: 8 }}>
                    Opt-in · default off
                  </Tag>
                )}
              </span>
            }
            subtitle={`Processing + visibility switches for the ${m.label} module. Instant, no redeploy.`}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 15 }}>
                {m.label} — Processing（后台处理）
              </div>
              <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
                Backend behavior switch. Off pauses the module's processing / API.
              </div>
            </div>
            <Switch
              checked={m.enabled}
              loading={isUpdating}
              onChange={(v: boolean) =>
                patch(m, { enabled: v }, v ? `${m.label} processing enabled.` : `${m.label} processing paused.`)
              }
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 15 }}>
                {m.label} — Visibility（用户可见）
              </div>
              <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
                Frontend display switch. Off hides the nav entry + pages for all users.
              </div>
            </div>
            <Switch
              checked={m.visible}
              loading={isUpdating}
              onChange={(v: boolean) =>
                patch(m, { visible: v }, v ? `${m.label} visible to users.` : `${m.label} hidden from users.`)
              }
            />
          </div>
        </Card>
        )
      })}
    </div>
  )
}
