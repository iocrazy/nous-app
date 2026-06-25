import { useEffect, useState } from 'react'
import { Card, InputNumber, Button, Message, Spin, Divider } from '@arco-design/web-react'
import { IconThunderbolt } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import {
  useTopicScoringConfig,
  useUpdateTopicScoringConfig,
  type TopicScoringConfig,
} from '../../api/endpoints/settings'

// Order + labels for the five LLM-rated dimensions (the agent emits raw 0..1
// scores; these weights are the CODE-side composite — tunable here, no deploy).
const DIM_LABELS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'novelty', label: 'Novelty 新颖度', hint: '真·首发/新观点 vs 炒冷饭' },
  { key: 'impact', label: 'Impact 影响力', hint: '行业/从业者影响面与量级' },
  { key: 'credibility', label: 'Credibility 可信度', hint: '内容本身的事实/数据(非"谁发的")' },
  { key: 'actionability', label: 'Actionability 可操作性', hint: '能否据此做选题' },
  { key: 'shareability', label: 'Shareability 传播潜力', hint: '话题性/讨论度' },
]

const TIER_LABELS: Array<{ key: string; label: string }> = [
  { key: '1', label: 'Tier 1 — 官方/论文原文' },
  { key: '2', label: 'Tier 2 — 大佬个人号/优质资讯(默认)' },
  { key: '3', label: 'Tier 3 — 综合/社交热榜(噪声多)' },
]

export function TopicScoring() {
  const { data, isLoading } = useTopicScoringConfig()
  const update = useUpdateTopicScoringConfig()
  const [cfg, setCfg] = useState<TopicScoringConfig | null>(null)

  useEffect(() => {
    if (data) setCfg(data)
  }, [data])

  if (isLoading || !cfg) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  const dimSum = DIM_LABELS.reduce((s, d) => s + (cfg.dim_weights[d.key] || 0), 0)

  const setDim = (key: string, v: number) =>
    setCfg((c) => (c ? { ...c, dim_weights: { ...c.dim_weights, [key]: v } } : c))
  const setTier = (key: string, v: number) =>
    setCfg((c) => (c ? { ...c, tier_weights: { ...c.tier_weights, [key]: v } } : c))

  const save = async () => {
    if (!cfg) return
    try {
      await update.mutateAsync(cfg)
      Message.success('Scoring config saved — applies on the next scoring pass.')
    } catch (e) {
      Message.error(`Save failed: ${(e as Error).message}`)
    }
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <SectionHeader
        icon={<IconThunderbolt />}
        title="Topic Scoring"
        subtitle="Tune how hotspots are scored. The LLM rates five raw dimensions; these weights are the code-side composite (no redeploy)."
      />

      <Card title="Dimension weights" style={{ marginBottom: 16 }}>
        {DIM_LABELS.map((d) => (
          <div
            key={d.key}
            style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}
          >
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500 }}>{d.label}</div>
              <div style={{ fontSize: 12, color: 'var(--color-text-3)' }}>{d.hint}</div>
            </div>
            <InputNumber
              mode="button"
              min={0}
              max={1}
              step={0.05}
              precision={2}
              value={cfg.dim_weights[d.key] ?? 0}
              onChange={(v) => setDim(d.key, Number(v) || 0)}
              style={{ width: 130 }}
            />
          </div>
        ))}
        <Divider style={{ margin: '8px 0' }} />
        <div style={{ fontSize: 12, color: dimSum > 1.001 ? '#d03050' : 'var(--color-text-3)' }}>
          权重和 = {dimSum.toFixed(2)}（不强制为 1；composite 会 clamp 到 0–1，但建议归一化）
        </div>
      </Card>

      <Card title="Source-tier multipliers" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginBottom: 10 }}>
          信源等级先验：低等级源乘数更小，名气压不动弱内容。
        </div>
        {TIER_LABELS.map((t) => (
          <div
            key={t.key}
            style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}
          >
            <div style={{ flex: 1, fontWeight: 500 }}>{t.label}</div>
            <InputNumber
              mode="button"
              min={0}
              max={1}
              step={0.02}
              precision={2}
              value={cfg.tier_weights[t.key] ?? 0}
              onChange={(v) => setTier(t.key, Number(v) || 0)}
              style={{ width: 130 }}
            />
          </div>
        ))}
      </Card>

      <Card title="Featured cutoff" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>Featured min score</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)' }}>
              进入「精选」视图的质量分下限（0–1）。
            </div>
          </div>
          <InputNumber
            mode="button"
            min={0}
            max={1}
            step={0.05}
            precision={2}
            value={cfg.featured_min_score}
            onChange={(v) =>
              setCfg((c) => (c ? { ...c, featured_min_score: Number(v) || 0 } : c))
            }
            style={{ width: 130 }}
          />
        </div>
      </Card>

      <Button type="primary" loading={update.isPending} onClick={save}>
        Save
      </Button>
    </div>
  )
}
