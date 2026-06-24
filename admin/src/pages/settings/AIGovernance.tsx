import { useEffect, useState } from 'react'
import {
  Card,
  Switch,
  Input,
  Button,
  Message,
  Spin,
  Divider,
  Tag,
} from '@arco-design/web-react'
import {
  useAIGovernanceSettings,
  useUpdateAIGovernanceSettings,
  type AIGovernanceModuleUpdate,
  type AIGovernanceUpdate,
} from '../../api/endpoints/settings'

/**
 * AI Config Governance panel. Controls whether users may configure each AI
 * module's provider/key/model ("Allow user config"). When a module is locked
 * (switch OFF), the admin must supply a platform base_url/model/api_key for
 * that module — users lose the corresponding config UI section.
 *
 * API contract (mirrors MemorySettings):
 *   GET /admin/settings/ai-governance  → toggles + non-secret config + *_api_key_set
 *   PUT /admin/settings/ai-governance  → writes all fields; api_key only when non-blank
 */
function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 500, fontSize: 14 }}>{label}</div>
        {hint && <div style={{ color: 'var(--color-text-3)', fontSize: 13, marginTop: 4 }}>{hint}</div>}
      </div>
      <div style={{ marginLeft: 24, minWidth: 280, textAlign: 'right' }}>{children}</div>
    </div>
  )
}

type TaskModuleKey = 'transcription' | 'translation' | 'visual_analysis' | 'caption' | 'classification' | 'summarization' | 'topic_scorer' | 'embedding'

interface TaskModuleLocalState {
  user_allowed: boolean
  nous_allowed: boolean
  base_url: string
  model: string
  api_key: string
}

interface LocalState {
  nous_user_enabled: boolean
  chat_allowed: boolean
  chat_nous_allowed: boolean
  transcription: TaskModuleLocalState
  translation: TaskModuleLocalState
  visual_analysis: TaskModuleLocalState
  caption: TaskModuleLocalState
  classification: TaskModuleLocalState
  summarization: TaskModuleLocalState
  topic_scorer: TaskModuleLocalState
  embedding: TaskModuleLocalState
}

const TASK_MODULES: ReadonlyArray<{ key: TaskModuleKey; label: string; hint: string }> = [
  {
    key: 'transcription',
    label: 'Extract',
    hint: 'Transcription — Whisper / STT. Platform key required when locked (no env fallback).',
  },
  {
    key: 'translation',
    label: 'Rewrite / Translate',
    hint: 'LLM-based translation and rewriting. Platform key required when locked (no env fallback).',
  },
  {
    key: 'visual_analysis',
    label: 'Analyze',
    hint: 'Visual analysis of images and video frames.',
  },
  {
    key: 'caption',
    label: 'Caption',
    hint: 'AI-generated media captions.',
  },
  {
    key: 'classification',
    label: 'Classify',
    hint: 'Content classification and auto-tagging.',
  },
  {
    key: 'summarization',
    label: 'Summarization / Rewrite',
    hint: 'LLM summary/rewrite of transcripts. Platform key required when locked.',
  },
  {
    key: 'topic_scorer',
    label: 'Topic Scorer',
    hint: 'Scores news hotspots for the Topic Inspiration feed (score/reason/summary).',
  },
  {
    key: 'embedding',
    label: 'Embedding',
    hint: 'Text/multimodal embedding provider for topic cross-source clustering. e.g. Volcengine Ark multimodal — base_url https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal, model doubao-embedding-vision-250615 (2048-dim).',
  },
]

const PLATFORM_MANAGED: ReadonlyArray<{ label: string; hint: string }> = [
  { label: 'Storyboard', hint: 'Hardcoded platform agent — model is fixed by the agent slug.' },
  { label: 'Image / Video Generation', hint: 'Provider registry — platform-configured, no per-user setting.' },
  { label: 'Memory (Graphiti)', hint: 'Admin-configured in the Memory panel above.' },
  { label: 'Tasklets', hint: 'Hardcoded qwen-turbo — no per-user configuration.' },
]

function defaultTaskState(): TaskModuleLocalState {
  return { user_allowed: true, nous_allowed: true, base_url: '', model: '', api_key: '' }
}

function defaultLocalState(): LocalState {
  return {
    nous_user_enabled: false,
    chat_allowed: true,
    chat_nous_allowed: true,
    transcription: defaultTaskState(),
    translation: defaultTaskState(),
    visual_analysis: defaultTaskState(),
    caption: defaultTaskState(),
    classification: defaultTaskState(),
    summarization: defaultTaskState(),
    topic_scorer: defaultTaskState(),
    embedding: defaultTaskState(),
  }
}

export function AIGovernance() {
  const { data, isLoading } = useAIGovernanceSettings()
  const updateMutation = useUpdateAIGovernanceSettings()

  const [state, setState] = useState<LocalState>(defaultLocalState())

  // Hydrate local form from the masked GET (never includes raw keys).
  useEffect(() => {
    if (!data) return
    setState({
      nous_user_enabled: data.nous_user_enabled,
      chat_allowed: data.chat.user_allowed,
      chat_nous_allowed: data.chat.nous_allowed,
      transcription: {
        user_allowed: data.transcription.user_allowed,
        nous_allowed: data.transcription.nous_allowed,
        base_url: data.transcription.base_url ?? '',
        model: data.transcription.model ?? '',
        api_key: '',
      },
      translation: {
        user_allowed: data.translation.user_allowed,
        nous_allowed: data.translation.nous_allowed,
        base_url: data.translation.base_url ?? '',
        model: data.translation.model ?? '',
        api_key: '',
      },
      visual_analysis: {
        user_allowed: data.visual_analysis.user_allowed,
        nous_allowed: data.visual_analysis.nous_allowed,
        base_url: data.visual_analysis.base_url ?? '',
        model: data.visual_analysis.model ?? '',
        api_key: '',
      },
      caption: {
        user_allowed: data.caption.user_allowed,
        nous_allowed: data.caption.nous_allowed,
        base_url: data.caption.base_url ?? '',
        model: data.caption.model ?? '',
        api_key: '',
      },
      classification: {
        user_allowed: data.classification.user_allowed,
        nous_allowed: data.classification.nous_allowed,
        base_url: data.classification.base_url ?? '',
        model: data.classification.model ?? '',
        api_key: '',
      },
      summarization: {
        user_allowed: data.summarization.user_allowed,
        nous_allowed: data.summarization.nous_allowed,
        base_url: data.summarization.base_url ?? '',
        model: data.summarization.model ?? '',
        api_key: '',
      },
      topic_scorer: {
        user_allowed: data.topic_scorer.user_allowed,
        nous_allowed: data.topic_scorer.nous_allowed,
        base_url: data.topic_scorer.base_url ?? '',
        model: data.topic_scorer.model ?? '',
        api_key: '',
      },
      embedding: {
        user_allowed: data.embedding.user_allowed,
        nous_allowed: data.embedding.nous_allowed,
        base_url: data.embedding.base_url ?? '',
        model: data.embedding.model ?? '',
        api_key: '',
      },
    })
  }, [data])

  function setTaskField<K extends keyof TaskModuleLocalState>(
    moduleKey: TaskModuleKey,
    field: K,
    value: TaskModuleLocalState[K],
  ) {
    setState((prev) => ({
      ...prev,
      [moduleKey]: { ...prev[moduleKey], [field]: value },
    }))
  }

  const handleSave = () => {
    // Build a task module update, including api_key only when the admin typed
    // a new value — blank means keep the stored key unchanged.
    const taskUpdate = (m: TaskModuleLocalState): AIGovernanceModuleUpdate => ({
      user_allowed: m.user_allowed,
      nous_allowed: m.nous_allowed,
      base_url: m.base_url,
      model: m.model,
      ...(m.api_key.trim() ? { api_key: m.api_key.trim() } : {}),
    })

    const payload: AIGovernanceUpdate = {
      nous_user_enabled: state.nous_user_enabled,
      chat: { user_allowed: state.chat_allowed, nous_allowed: state.chat_nous_allowed },
      transcription: taskUpdate(state.transcription),
      translation: taskUpdate(state.translation),
      visual_analysis: taskUpdate(state.visual_analysis),
      caption: taskUpdate(state.caption),
      classification: taskUpdate(state.classification),
      summarization: taskUpdate(state.summarization),
      topic_scorer: taskUpdate(state.topic_scorer),
      embedding: taskUpdate(state.embedding),
    }

    updateMutation.mutate(payload, {
      onSuccess: () => {
        Message.success('AI governance settings saved')
        // Clear api_key fields — they are write-only and never pre-filled.
        setState((prev) => ({
          ...prev,
          transcription: { ...prev.transcription, api_key: '' },
          translation: { ...prev.translation, api_key: '' },
          visual_analysis: { ...prev.visual_analysis, api_key: '' },
          caption: { ...prev.caption, api_key: '' },
          classification: { ...prev.classification, api_key: '' },
          summarization: { ...prev.summarization, api_key: '' },
          topic_scorer: { ...prev.topic_scorer, api_key: '' },
          embedding: { ...prev.embedding, api_key: '' },
        }))
      },
      onError: (err) => Message.error((err as Error).message || 'Failed to save'),
    })
  }

  if (isLoading) {
    return (
      <Card title="AI Config Governance" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Spin size={28} />
        </div>
      </Card>
    )
  }

  const keyTag = (isSet: boolean) =>
    isSet ? <Tag color="green">key set</Tag> : <Tag>no key</Tag>

  return (
    <Card title="AI Config Governance" style={{ marginBottom: 20 }}>
      {/* ── Nous master control ─────────────────────────────────────────── */}
      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--color-text-2)' }}>Nous Platform (user-side)</div>
      <Row
        label="Enable Nous for users"
        hint="Master switch. OFF ⇒ no platform models surface anywhere. Default OFF — turn on only when you accept platform-key cost exposure."
      >
        <Switch
          checked={state.nous_user_enabled}
          onChange={(checked) => setState((prev) => ({ ...prev, nous_user_enabled: checked }))}
          disabled={updateMutation.isPending}
        />
      </Row>
      <Divider />

      {/* ── Chat ────────────────────────────────────────────────────────── */}
      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--color-text-2)' }}>Chat</div>
      <Row
        label="Allow user config"
        hint="Locked ⇒ uses platform provider keys for the agent's model. The agent prompt and model are unchanged."
      >
        <Switch
          checked={state.chat_allowed}
          onChange={(checked) => setState((prev) => ({ ...prev, chat_allowed: checked }))}
          disabled={updateMutation.isPending}
        />
      </Row>
      <Row label="Allow Nous models (chat)" hint="Only effective when the master switch is on.">
        <Switch
          checked={state.chat_nous_allowed}
          onChange={(checked) => setState((prev) => ({ ...prev, chat_nous_allowed: checked }))}
          disabled={updateMutation.isPending || !state.nous_user_enabled}
        />
      </Row>

      {/* ── Task modules ─────────────────────────────────────────────────── */}
      {TASK_MODULES.map(({ key, label, hint }) => {
        const m = state[key]
        const apiKeySet = data?.[key]?.api_key_set ?? false
        return (
          <div key={key}>
            <Divider />
            <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--color-text-2)' }}>{label}</div>
            <div style={{ color: 'var(--color-text-3)', fontSize: 13, marginTop: 2, marginBottom: 0 }}>{hint}</div>
            <Row label="Allow user config">
              <Switch
                checked={m.user_allowed}
                onChange={(checked) => setTaskField(key, 'user_allowed', checked)}
                disabled={updateMutation.isPending}
              />
            </Row>
            <Row label="Allow Nous models" hint="Only effective when the master switch is on.">
              <Switch
                checked={m.nous_allowed}
                onChange={(checked) => setTaskField(key, 'nous_allowed', checked)}
                disabled={updateMutation.isPending || !state.nous_user_enabled}
              />
            </Row>
            {!m.user_allowed && (
              <>
                <Divider style={{ margin: 0 }} />
                <Row
                  label="Base URL"
                  hint="Platform provider base URL (OpenAI-compatible). Required when locked."
                >
                  <Input
                    value={m.base_url}
                    onChange={(v) => setTaskField(key, 'base_url', v)}
                    placeholder="https://.../v1"
                    style={{ width: 260 }}
                  />
                </Row>
                <Divider style={{ margin: 0 }} />
                <Row label="Model">
                  <Input
                    value={m.model}
                    onChange={(v) => setTaskField(key, 'model', v)}
                    placeholder="e.g. Qwen/Qwen3-235B-A22B-Instruct-2507"
                    style={{ width: 260 }}
                  />
                </Row>
                <Divider style={{ margin: 0 }} />
                <Row label="API key" hint="Write-only. Leave blank to keep the stored key.">
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'flex-end' }}>
                    {keyTag(apiKeySet)}
                    <Input.Password
                      value={m.api_key}
                      onChange={(v) => setTaskField(key, 'api_key', v)}
                      placeholder={apiKeySet ? 'leave blank to keep' : 'set a key'}
                      style={{ width: 200 }}
                    />
                  </div>
                </Row>
              </>
            )}
          </div>
        )
      })}

      {/* ── Platform-managed (read-only info) ────────────────────────────── */}
      <Divider />
      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--color-text-2)' }}>
        Platform-Managed Services
      </div>
      <div style={{ color: 'var(--color-text-3)', fontSize: 13, marginTop: 2, marginBottom: 0 }}>
        These surfaces are platform-configured — no per-user toggle exists.
      </div>
      {PLATFORM_MANAGED.map(({ label, hint }, index) => (
        <div key={label}>
          {index > 0 && <Divider style={{ margin: 0 }} />}
          <Row label={label} hint={hint}>
            <Tag>Platform-managed</Tag>
          </Row>
        </div>
      ))}

      <Divider />
      <div style={{ textAlign: 'right' }}>
        <Button type="primary" loading={updateMutation.isPending} onClick={handleSave}>
          Save
        </Button>
      </div>
    </Card>
  )
}
