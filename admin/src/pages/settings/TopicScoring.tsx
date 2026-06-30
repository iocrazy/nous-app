import { useEffect, useState } from 'react'
import {
  Card,
  InputNumber,
  Button,
  Message,
  Spin,
  Divider,
  Switch,
  Input,
} from '@arco-design/web-react'
import { IconThunderbolt } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import {
  useTopicScoringConfig,
  useUpdateTopicScoringConfig,
  useTopicPrefilterConfig,
  useUpdateTopicPrefilterConfig,
  useTopicContentFetchConfig,
  useUpdateTopicContentFetchConfig,
  useTopicModuleConfig,
  useUpdateTopicModuleConfig,
  type TopicScoringConfig,
  type TopicPrefilterConfig,
  type TopicContentFetchConfig,
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

  const { data: preData, isLoading: preLoading } = useTopicPrefilterConfig()
  const updatePre = useUpdateTopicPrefilterConfig()
  const [pre, setPre] = useState<TopicPrefilterConfig | null>(null)
  // Edit keywords as one textarea (one per line); convert on save.
  const [kwText, setKwText] = useState('')

  const { data: cfData, isLoading: cfLoading } = useTopicContentFetchConfig()
  const updateCf = useUpdateTopicContentFetchConfig()
  const [cf, setCf] = useState<TopicContentFetchConfig | null>(null)

  const { data: modData } = useTopicModuleConfig()
  const updateMod = useUpdateTopicModuleConfig()

  const toggleModule = async (enabled: boolean) => {
    try {
      await updateMod.mutateAsync({ enabled })
      Message.success(
        enabled
          ? 'Topic Inspiration enabled.'
          : 'Topic Inspiration turned off — collection paused, page hidden.',
      )
    } catch (e) {
      Message.error(`Save failed: ${(e as Error).message}`)
    }
  }

  useEffect(() => {
    if (data) setCfg(data)
  }, [data])

  useEffect(() => {
    if (preData) {
      setPre(preData)
      setKwText((preData.keywords || []).join('\n'))
    }
  }, [preData])

  useEffect(() => {
    if (cfData) setCf(cfData)
  }, [cfData])

  if (isLoading || !cfg || preLoading || !pre || cfLoading || !cf) {
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

  const savePrefilter = async () => {
    if (!pre) return
    const keywords = kwText
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    try {
      await updatePre.mutateAsync({ ...pre, keywords })
      Message.success('Pre-filter saved — applies on the next fetch tick.')
    } catch (e) {
      Message.error(`Save failed: ${(e as Error).message}`)
    }
  }

  const saveContentFetch = async () => {
    if (!cf) return
    try {
      await updateCf.mutateAsync(cf)
      Message.success('Content fetch saved — applies on the next fetch tick.')
    } catch (e) {
      Message.error(`Save failed: ${(e as Error).message}`)
    }
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <Card
        style={{ marginBottom: 24, borderColor: 'rgb(var(--primary-6))' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 15 }}>Topic Inspiration — master switch（总开关）</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
              整个「话题灵感」功能的总开关。<br />
              <b>开启</b>：正常采集热点、评分、展示页面。<br />
              <b>关闭</b>：立即停止所有后台处理（采集 / 评分 / 正文抓取 / 聚类），并对所有用户隐藏整个话题灵感页面（导航和页面都不可见）。已采集的数据保留，重新开启即恢复。
            </div>
          </div>
          <Switch
            checked={modData?.enabled ?? true}
            loading={updateMod.isPending}
            onChange={toggleModule}
          />
        </div>
      </Card>

      <SectionHeader
        icon={<IconThunderbolt />}
        title="Topic Scoring"
        subtitle="热点评分配置。LLM 给每条热点打 5 个维度的原始分，下面的权重是代码侧的合成公式（改完即时生效，无需发版）。"
      />

      <Card title="Scoring" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>Enable scoring（AI 评分与摘要）</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
              <b>开启</b>：自动给新热点用 LLM 打 5 维质量分 + 生成中文摘要。<br />
              <b>关闭</b>：停止评分和摘要（省 LLM 费用），已有的分数 / 摘要保留不变。下个抓取周期生效。
            </div>
          </div>
          <Switch
            checked={cfg.enabled}
            onChange={(v) => setCfg((c) => (c ? { ...c, enabled: v } : c))}
          />
        </div>
      </Card>

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

      <Card title="AI Summary" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>Summary max chars</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)' }}>
              ai_summary 字数上限（汉字）。0 = 用 Agent 默认（“1-2 句”）。改完即时生效，无需发版。
            </div>
          </div>
          <InputNumber
            mode="button"
            min={0}
            max={500}
            step={10}
            precision={0}
            value={cfg.summary_max_chars}
            onChange={(v) =>
              setCfg((c) => (c ? { ...c, summary_max_chars: Number(v) || 0 } : c))
            }
            style={{ width: 130 }}
          />
        </div>
      </Card>

      <Button type="primary" loading={update.isPending} onClick={save}>
        Save
      </Button>

      <Divider />

      <SectionHeader
        icon={<IconThunderbolt />}
        title="L0 Pre-filter (intake gate)"
        subtitle="噪声源（社交热榜等）入库前的关键词闸门。默认只放行含 AI 关键词的条目——做媒体/泛内容可关闭闸门或换成你自己的关键词（综艺/明星/影视/赛事…）。改完下个抓取周期生效，无需发版。"
      />

      <Card title="Pre-filter" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>Enable keyword gate（关键词闸门）</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
              入库前的关键词过滤，只对噪声源（社交热榜等 tier ≥ 下方设定）生效。<br />
              <b>开启</b>：噪声源只放行命中下方关键词的热点（过滤无关内容）。<br />
              <b>关闭</b>：噪声源全部热点都入库（量更大更杂，靠后续评分降噪）。下个抓取周期生效。
            </div>
          </div>
          <Switch
            checked={pre.enabled}
            onChange={(v) => setPre((p) => (p ? { ...p, enabled: v } : p))}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>Apply from tier</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)' }}>
              该 tier 及更"噪"的源才过闸门（1–4）；更优质的低 tier 源直接放行。
            </div>
          </div>
          <InputNumber
            mode="button"
            min={1}
            max={4}
            step={1}
            precision={0}
            disabled={!pre.enabled}
            value={pre.tier_from}
            onChange={(v) => setPre((p) => (p ? { ...p, tier_from: Number(v) || 3 } : p))}
            style={{ width: 130 }}
          />
        </div>

        <div>
          <div style={{ fontWeight: 500 }}>Include keywords</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginBottom: 8 }}>
            每行一个关键词（不区分大小写，子串匹配标题+正文）。留空 = 不按关键词过滤（等于对该闸门放行全部）。
          </div>
          <Input.TextArea
            value={kwText}
            onChange={setKwText}
            disabled={!pre.enabled}
            autoSize={{ minRows: 6, maxRows: 16 }}
            placeholder={'综艺\n明星\n影视\n电影\n电视剧\n热搜'}
          />
        </div>
      </Card>

      <Button type="primary" loading={updatePre.isPending} onClick={savePrefilter}>
        Save pre-filter
      </Button>

      <Divider />

      <SectionHeader
        icon={<IconThunderbolt />}
        title="L0.5 Content fetch (article body)"
        subtitle="用 trafilatura 给优质新闻源（tier ≤ N）抓真实正文回填，让评分/摘要不再只看标题。社交源（聚合页）跳过。改完下个抓取周期生效。"
      />

      <Card title="Content fetch" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>Enable content fetch（新闻正文抓取）</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
              用 trafilatura 给优质新闻源抓取真实文章正文回填。<br />
              <b>开启</b>：评分和摘要更准（不再只看标题）。<br />
              <b>关闭</b>：只保留标题（社交源本就无正文，不受影响）。下个抓取周期生效。
            </div>
          </div>
          <Switch
            checked={cf.enabled}
            onChange={(v) => setCf((c) => (c ? { ...c, enabled: v } : c))}
          />
        </div>

        {(
          [
            { key: 'tier_max', label: 'Apply to tier ≤', hint: '只对该 tier 及更优质的源抓正文（社交聚合页无正文）', min: 1, max: 4, step: 1 },
            { key: 'max_items', label: 'Max items / pass', hint: '每个抓取周期抓多少条（限速）', min: 1, max: 200, step: 5 },
            { key: 'concurrency', label: 'Concurrency', hint: '并发抓取数（礼貌限制，避免封 IP）', min: 1, max: 16, step: 1 },
            { key: 'timeout_s', label: 'Timeout (s)', hint: '单条抓取超时', min: 3, max: 60, step: 1 },
            { key: 'min_chars', label: 'Min chars', hint: '抽出的正文短于此判为无效丢弃', min: 1, max: 2000, step: 10 },
          ] as Array<{ key: keyof TopicContentFetchConfig; label: string; hint: string; min: number; max: number; step: number }>
        ).map((f) => (
          <div
            key={f.key}
            style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}
          >
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500 }}>{f.label}</div>
              <div style={{ fontSize: 12, color: 'var(--color-text-3)' }}>{f.hint}</div>
            </div>
            <InputNumber
              mode="button"
              min={f.min}
              max={f.max}
              step={f.step}
              precision={0}
              disabled={!cf.enabled}
              value={cf[f.key] as number}
              onChange={(v) =>
                setCf((c) => (c ? { ...c, [f.key]: Number(v) || f.min } : c))
              }
              style={{ width: 130 }}
            />
          </div>
        ))}
      </Card>

      <Button type="primary" loading={updateCf.isPending} onClick={saveContentFetch}>
        Save content fetch
      </Button>
    </div>
  )
}
