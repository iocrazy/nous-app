import { Card, Switch, Message } from '@arco-design/web-react'
import {
  useDistributionModuleConfig,
  useUpdateDistributionModuleConfig,
  type DistributionModuleConfig,
} from '../../api/endpoints/settings'
import { SectionHeader } from './SectionHeader'
import { IconThunderbolt } from '@arco-design/web-react/icon'

/**
 * Distribution module switches — mirrors the Topic Inspiration toggle.
 * `enabled` gates the backend account/OAuth API; `visible` shows/hides the
 * frontend nav entry + routes. Both default OFF (opt-in): the module ships
 * dark and only appears once an admin turns it on here — no redeploy.
 */
export function DistributionModule() {
  const { data } = useDistributionModuleConfig()
  const update = useUpdateDistributionModuleConfig()

  const patch = (partial: Partial<DistributionModuleConfig>, okMsg: string) => {
    const next: DistributionModuleConfig = {
      enabled: data?.enabled ?? false,
      visible: data?.visible ?? false,
      ...partial,
    }
    update.mutate(next, {
      onSuccess: () => Message.success(okMsg),
      onError: () => Message.error('Update failed'),
    })
  }

  return (
    <Card style={{ marginBottom: 20 }}>
      <SectionHeader
        icon={<IconThunderbolt />}
        title="Distribution"
        subtitle="Publish media to social platforms (Douyin first). Opt-in — off by default."
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 15 }}>Distribution — visibility（用户可见）</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
            前端显示开关。<br />
            <b>开启</b>：所有用户可见「分发」导航入口和账号页面。<br />
            <b>关闭</b>：对所有用户隐藏导航和页面（含直链访问）。
          </div>
        </div>
        <Switch
          checked={data?.visible ?? false}
          loading={update.isPending}
          onChange={(v: boolean) =>
            patch(
              { visible: v },
              v ? 'Distribution is now visible to users.' : 'Distribution hidden from users.',
            )
          }
        />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 15 }}>Distribution — API access（后端接口）</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
            后端账号/授权接口开关。<br />
            <b>开启</b>：账号绑定 / OAuth 接口可用。<br />
            <b>关闭</b>：所有 <code>/api/v1/distribution</code> 接口返回 404（含前端已显示时的调用）。
          </div>
        </div>
        <Switch
          checked={data?.enabled ?? false}
          loading={update.isPending}
          onChange={(v: boolean) =>
            patch(
              { enabled: v },
              v ? 'Distribution API enabled.' : 'Distribution API disabled (404).',
            )
          }
        />
      </div>
    </Card>
  )
}
