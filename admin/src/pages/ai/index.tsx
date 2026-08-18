import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Button, Modal, Form, Input, Select, Switch,
  Space, Message, Popconfirm, Tag, Typography, Card, Spin,
} from '@arco-design/web-react'
import { IconPlus, IconDelete, IconSync, IconEdit } from '@arco-design/web-react/icon'
import { useAuth } from '../../auth/AuthProvider'
import { CodexAuthCard } from './CodexAuthCard'
import { JimengAuthCard } from './JimengAuthCard'

const { Title, Text } = Typography
const FormItem = Form.Item

interface NousModel {
  id: string
  name: string
  display_name: string
  type: string
  description?: string
  actual_provider: string
  actual_model: string
  api_key_masked: string
  app_id?: string
  base_url?: string
  pricing_type: string
  pricing_value: number
  is_enabled: boolean
  sort_order: number
  created_at: string
  updated_at: string
  // Persisted connectivity-test result (survives navigation).
  //   ok         reachable
  //   fail       probed and failed
  //   not_probed the backend probe has no protocol for this model TYPE
  //              (image / video / tts) and checked nothing — NOT a fault
  //   null       never probed
  // Before migration 428 the unprobeable types were recorded as `fail`, which
  // is why this page carried three permanent red lights for healthy models.
  last_test_status?: 'ok' | 'fail' | 'not_probed' | null
  last_test_detail?: string | null
  last_tested_at?: string | null
}

interface ProviderProtocol {
  key: string
  label: string
  description: string
  model_types: string[]
  aliases: string[]
  is_default: boolean
}

// A provider card groups every model that shares the same provider + base URL
// (and therefore the same platform key).
interface ProviderGroup {
  provider: string
  base_url: string
  api_key_masked: string
  app_id?: string
  models: NousModel[]
}

const TYPE_COLORS: Record<string, string> = {
  llm: 'arcoblue',
  embedding: 'green',
  tts: 'orange',
  asr: 'purple',
}

// Full-auto: infer the model TYPE from its name so the admin never picks it.
function guessType(model: string): string {
  const m = model.toLowerCase()
  if (m.includes('embed')) return 'embedding'
  if (m.includes('asr') || m.includes('whisper') || m.includes('stt')) return 'asr'
  if (m.includes('tts') || m.includes('speech') || m.includes('voice')) return 'tts'
  return 'llm'
}

function sanitizeName(model: string): string {
  return 'mediahub-' + model.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

// Human "5m ago" from an ISO timestamp (empty string when never tested).
function timeAgo(iso?: string | null): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 60) return 'just now'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// Provider health = aggregate of its models' persisted results: any fail → red,
// else any ok → green, else any not_probed → neutral, else gray (untested).
//
// `not_probed` ranks below `ok` on purpose: a provider with one working LLM and
// one unprobeable image model is reachable, and its dot should say so.
function aggregateStatus(
  models: NousModel[],
): 'ok' | 'fail' | 'not_probed' | undefined {
  if (models.some((m) => m.last_test_status === 'fail')) return 'fail'
  if (models.some((m) => m.last_test_status === 'ok')) return 'ok'
  if (models.some((m) => m.last_test_status === 'not_probed')) return 'not_probed'
  return undefined
}

// Failing models with their persisted probe reason (e.g. "HTTP 402: ..."),
// so the card can SHOW why a dot is red instead of hiding it in a hover.
//
// `not_probed` is NOT in here, and that is the point of this change: it is not
// a failure, so it gets no red line and does not drag the provider dot red.
function failingModels(models: NousModel[]): NousModel[] {
  return models.filter((m) => m.last_test_status === 'fail')
}

// Connectivity dot from the LAST persisted Test: green = reachable, red =
// failed, mid-gray = not probed, pale = never tested. Tooltip carries the
// detail + when it was tested.
//
// `not_probed` gets its own neutral shade rather than reusing the pale
// never-tested one: "we don't check this type" and "nobody has checked yet" are
// different facts about the same model, and only the first is permanent.
// Neither is red — a probe that cannot speak the protocol has no verdict to
// give, and rendering one as a fault is what this whole change removes.
const DOT_COLORS: Record<string, string> = {
  ok: '#00b42a',
  fail: '#f53f3f',
  not_probed: 'var(--color-text-4)',
}
const DOT_LABELS: Record<string, string> = {
  ok: 'Reachable',
  fail: 'Failed',
  not_probed: 'Not probed',
}

function StatusDot({
  status,
  detail,
  at,
}: {
  status?: 'ok' | 'fail' | 'not_probed' | null
  detail?: string | null
  at?: string | null
}) {
  const color = (status && DOT_COLORS[status]) || 'var(--color-fill-3)'
  const label = (status && DOT_LABELS[status]) || 'Not tested'
  const ago = timeAgo(at)
  const title = [label, detail || undefined, ago ? `tested ${ago}` : undefined]
    .filter(Boolean)
    .join(' · ')
  return (
    <span
      title={title}
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
        flexShrink: 0,
      }}
    />
  )
}

export function AIModelsPage() {
  const { session } = useAuth()
  const token = session?.access_token
  const [models, setModels] = useState<NousModel[]>([])
  const [loading, setLoading] = useState(false)

  const [modalVisible, setModalVisible] = useState(false)
  // 'new' = configure a brand-new provider; 'add' = add models to an existing
  // provider card (key inherited server-side, so no re-typing).
  const [modalMode, setModalMode] = useState<'new' | 'add'>('new')
  const [modalGroup, setModalGroup] = useState<ProviderGroup | null>(null)
  const [fetchedModels, setFetchedModels] = useState<string[]>([])
  const [protocols, setProtocols] = useState<ProviderProtocol[]>([])
  const [selectedModels, setSelectedModels] = useState<string[]>([])
  const [probeLoading, setProbeLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testingProvider, setTestingProvider] = useState<string | null>(null)
  // Provider Test progress (done/total), so the card shows "Testing 2/3"
  // instead of an opaque spinner. Keyed by provider key.
  const [testProgress, setTestProgress] = useState<
    Record<string, { done: number; total: number }>
  >({})
  const [form] = Form.useForm()

  // Edit modal — reused for a single model ('model') and for a whole
  // provider's shared key/base_url ('provider', looped PUT over the group).
  const [editForm] = Form.useForm()
  const [editModal, setEditModal] = useState<
    | { mode: 'model'; model: NousModel }
    | { mode: 'provider'; group: ProviderGroup }
    | null
  >(null)
  const [editSaving, setEditSaving] = useState(false)

  const apiBase = import.meta.env.VITE_API_URL || ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  const fetchModels = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models`, { headers })
      if (res.ok) setModels(await res.json())
    } catch {
      Message.error('Failed to fetch models')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { fetchModels() }, [fetchModels])

  useEffect(() => {
    // Best-effort: a failure leaves protocols=[] and the field falls back to
    // a plain text input (see the Actual Provider FormItem).
    const loadProtocols = async () => {
      try {
        const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models/protocols`, { headers })
        if (res.ok) {
          const data = await res.json()
          setProtocols(data.protocols || [])
        }
      } catch (err) {
        // Non-fatal: the field degrades to a create-only Select. Log per the
        // project's no-silent-swallow rule so a broken endpoint is visible.
        console.error('[ai] failed to load provider protocols', err)
      }
    }
    loadProtocols()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const groups = useMemo<ProviderGroup[]>(() => {
    const map = new Map<string, ProviderGroup>()
    for (const m of models) {
      const key = `${m.actual_provider}|${m.base_url || ''}`
      if (!map.has(key)) {
        map.set(key, {
          provider: m.actual_provider,
          base_url: m.base_url || '',
          api_key_masked: m.api_key_masked,
          app_id: m.app_id,
          models: [],
        })
      }
      map.get(key)!.models.push(m)
    }
    return Array.from(map.values())
  }, [models])

  const protocolOptions = useMemo(
    () =>
      protocols.map((p) => ({
        label: `${p.label} — ${p.model_types.join('/')}`,
        value: p.key,
      })),
    [protocols],
  )

  const openAddProvider = () => {
    setModalMode('new')
    setModalGroup(null)
    setFetchedModels([])
    setSelectedModels([])
    form.resetFields()
    setModalVisible(true)
  }

  const openAddModels = (group: ProviderGroup) => {
    setModalMode('add')
    setModalGroup(group)
    setFetchedModels([])
    setSelectedModels([])
    form.resetFields()
    form.setFieldsValue({
      actual_provider: group.provider,
      base_url: group.base_url,
      app_id: group.app_id || '',
    })
    setModalVisible(true)
  }

  // Probe one model and persist + reflect the result. Returns the three-way
  // outcome — a boolean would force every caller to fold `not_probed` back into
  // "failed", which is the bug. Shared by the per-model Test and the provider
  // "Test all" button.
  const runModelTest = async (
    m: NousModel,
  ): Promise<'ok' | 'fail' | 'not_probed'> => {
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models/${m.id}/test`, {
        method: 'POST',
        headers,
      })
      const data = await res.json()
      // Mirror the backend's three-way mapping, not `ok ? green : red`. The
      // backend persisted `not_probed` for this row; painting it red here would
      // undo that on the very click meant to check it.
      const status: 'ok' | 'fail' | 'not_probed' = data.ok
        ? 'ok'
        : data.not_probed
          ? 'not_probed'
          : 'fail'
      const detail = data.ok || data.not_probed
        ? data.detail || 'ok'
        : data.error || 'failed'
      // The backend persisted this; mirror it into the row so the dot + "tested
      // Xm ago" update instantly (and stay correct after the next list fetch).
      setModels((prev) =>
        prev.map((x) =>
          x.id === m.id
            ? {
                ...x,
                last_test_status: status,
                last_test_detail: detail,
                last_tested_at: data.tested_at || new Date().toISOString(),
              }
            : x,
        ),
      )
      return status
    } catch {
      setModels((prev) =>
        prev.map((x) =>
          x.id === m.id
            ? { ...x, last_test_status: 'fail', last_test_detail: 'request failed', last_tested_at: new Date().toISOString() }
            : x,
        ),
      )
      // A request that never reached the backend IS a failure — of this page's
      // call, which is a real thing that went wrong, unlike `not_probed`.
      return 'fail'
    }
  }

  // Provider-level test, directly on the card: probe EVERY model in the
  // provider and persist each, so the provider dot (aggregate) and per-model
  // dots all reflect a real check. Runs the probes CONCURRENTLY — total time is
  // the slowest single model, not the sum (sequential made a 3-model card spin
  // for the sum of three real inference calls).
  const handleTestProvider = async (g: ProviderGroup) => {
    const k = `${g.provider}|${g.base_url}`
    const total = g.models.length
    setTestingProvider(k)
    setTestProgress((p) => ({ ...p, [k]: { done: 0, total } }))
    try {
      const results = await Promise.all(
        g.models.map((m) =>
          runModelTest(m).then((r) => {
            // Bump the completed count as each concurrent probe resolves.
            setTestProgress((p) => ({
              ...p,
              [k]: { done: (p[k]?.done ?? 0) + 1, total },
            }))
            return r
          }),
        ),
      )
      // Unprobeable models are excluded from the denominator, not counted as
      // losses: "2/3 models reachable" on a card whose third model was never
      // checked is a warning about nothing, and the fastest way to teach an
      // admin to ignore this button.
      const ok = results.filter((r) => r === 'ok').length
      const skipped = results.filter((r) => r === 'not_probed').length
      const checked = total - skipped
      const suffix = skipped ? ` (${skipped} not probed)` : ''
      if (checked === 0) {
        // Every model on the card was unprobeable, so this run verified
        // NOTHING. `ok === checked` would be 0 === 0 here and paint a green
        // "all 0 models reachable" — an affirmative reachability claim on top
        // of zero evidence, which is the same dishonest-signal bug as the red
        // lights this change removes, just inverted. Neutral, not success.
        Message.info(`${g.provider}: nothing to probe (${skipped} not probed)`)
      } else if (ok === checked) {
        Message.success(`${g.provider}: all ${checked} models reachable${suffix}`)
      } else {
        Message.warning(`${g.provider}: ${ok}/${checked} models reachable${suffix}`)
      }
    } finally {
      setTestingProvider(null)
      setTestProgress((p) => {
        const next = { ...p }
        delete next[k]
        return next
      })
    }
  }

  const handleProbe = async () => {
    const provider_key = form.getFieldValue('actual_provider')
    if (!provider_key) {
      Message.warning('Enter Actual Provider first')
      return
    }
    setProbeLoading(true)
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models/probe-models`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          provider_key,
          api_key: form.getFieldValue('api_key') || '',
          base_url: form.getFieldValue('base_url') || '',
          app_id: form.getFieldValue('app_id') || '',
          // 'add' mode: no key typed → backend falls back to the stored key of
          // an existing model in this provider group.
          name: modalMode === 'add' ? modalGroup?.models[0]?.name : undefined,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok && data.success && Array.isArray(data.models) && data.models.length) {
        setFetchedModels(data.models)
        Message.success(`Loaded ${data.models.length} models`)
      } else {
        setFetchedModels([])
        Message.warning(data.error || 'No model list — type the model name(s) manually')
      }
    } catch {
      setFetchedModels([])
      Message.error('Probe failed — type the model name(s) manually')
    } finally {
      setProbeLoading(false)
    }
  }

  // Models already configured in the target provider (hidden from the picker).
  const existingModels = useMemo(
    () => new Set((modalGroup?.models || []).map((m) => m.actual_model)),
    [modalGroup],
  )

  const handleSaveModels = async () => {
    if (!selectedModels.length) {
      Message.warning('Pick at least one model')
      return
    }
    let values: Record<string, unknown>
    try {
      values = await form.validate()
    } catch (err) {
      // Surface the reason instead of silently no-op'ing (a spinning /
      // dead button with no feedback). validate() only rejects in 'new'
      // mode where the provider fields are required + mounted.
      Message.error('Please fill the required provider fields')
      console.error('[AddModels] form validate failed:', err)
      return
    }
    // Provider fields: in 'add' mode the actual_provider / base_url / app_id
    // Form.Items are NOT mounted (they live in the 'new' branch), so
    // form.validate() returns them as undefined — sending that produced a
    // 422 (missing actual_provider). Source them from the target provider
    // group directly; the backend inherits the key from a sibling model.
    const providerFields =
      modalMode === 'add'
        ? {
            actual_provider: modalGroup?.provider ?? '',
            base_url: modalGroup?.base_url ?? '',
            app_id: modalGroup?.app_id ?? '',
            api_key: '', // blank → backend inherits from a sibling
          }
        : {
            actual_provider: (values.actual_provider as string) ?? '',
            base_url: (values.base_url as string) ?? '',
            app_id: (values.app_id as string) ?? '',
            api_key: (values.api_key as string) ?? '',
          }
    setSaving(true)
    let ok = 0
    const failures: string[] = []
    for (const model of selectedModels) {
      const payload = {
        name: sanitizeName(model),
        display_name: model,
        type: guessType(model),
        actual_model: model,
        ...providerFields,
        pricing_type: 'per_hour',
        pricing_value: 0,
        is_enabled: true,
        sort_order: 0,
      }
      try {
        const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models`, {
          method: 'POST',
          headers,
          body: JSON.stringify(payload),
        })
        if (res.ok) {
          ok += 1
        } else {
          const body = await res.text().catch(() => '')
          failures.push(`${model} (HTTP ${res.status}${body ? `: ${body.slice(0, 120)}` : ''})`)
        }
      } catch (err) {
        failures.push(`${model} (${err instanceof Error ? err.message : 'network error'})`)
      }
    }
    setSaving(false)
    if (ok) Message.success(`Added ${ok} model${ok > 1 ? 's' : ''}`)
    if (failures.length) Message.error(`Failed: ${failures.join('; ')}`)
    setModalVisible(false)
    fetchModels()
  }

  const handleDeleteModel = async (id: string) => {
    const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models/${id}`, { method: 'DELETE', headers })
    if (res.ok) {
      Message.success('Model removed')
      fetchModels()
    } else {
      Message.error('Failed to remove')
    }
  }

  const openEditModel = (m: NousModel) => {
    setEditModal({ mode: 'model', model: m })
    editForm.resetFields()
    editForm.setFieldsValue({
      display_name: m.display_name,
      actual_model: m.actual_model,
      type: m.type,
      base_url: m.base_url || '',
      pricing_value: m.pricing_value,
      api_key: '', // blank = keep current
    })
  }

  const openEditProvider = (g: ProviderGroup) => {
    setEditModal({ mode: 'provider', group: g })
    editForm.resetFields()
    editForm.setFieldsValue({
      base_url: g.base_url,
      app_id: g.app_id || '',
      api_key: '', // blank = keep current
    })
  }

  // PUT one model. Only non-empty fields are sent (blank api_key = keep).
  const putModel = async (id: string, patch: Record<string, unknown>) => {
    const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models/${id}`, {
      method: 'PUT',
      headers,
      body: JSON.stringify(patch),
    })
    if (!res.ok) {
      const body = await res.text().catch(() => '')
      throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 120)}` : ''}`)
    }
  }

  const handleEditSave = async () => {
    if (!editModal) return
    let values: Record<string, unknown>
    try {
      values = await editForm.validate()
    } catch {
      Message.error('Please check the fields')
      return
    }
    setEditSaving(true)
    try {
      if (editModal.mode === 'model') {
        const patch: Record<string, unknown> = {
          display_name: values.display_name,
          actual_model: values.actual_model,
          type: values.type,
          base_url: values.base_url || '',
          pricing_value: Number(values.pricing_value) || 0,
        }
        // Only overwrite the key when the admin typed a new one.
        if ((values.api_key as string)?.trim()) patch.api_key = values.api_key
        await putModel(editModal.model.id, patch)
        Message.success('Model updated')
      } else {
        // Provider-level: base_url / app_id / (optional) api_key apply to
        // every model row in the group — they share the provider's access.
        const shared: Record<string, unknown> = {
          base_url: values.base_url || '',
          app_id: values.app_id || '',
        }
        if ((values.api_key as string)?.trim()) shared.api_key = values.api_key
        for (const m of editModal.group.models) {
          await putModel(m.id, shared)
        }
        Message.success(`Updated ${editModal.group.models.length} model(s)`)
      }
      setEditModal(null)
      fetchModels()
    } catch (err) {
      Message.error(`Update failed: ${err instanceof Error ? err.message : 'error'}`)
    } finally {
      setEditSaving(false)
    }
  }

  const handleToggleEnabled = async (record: NousModel) => {
    const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models/${record.id}`, {
      method: 'PUT',
      headers,
      body: JSON.stringify({ is_enabled: !record.is_enabled }),
    })
    if (res.ok) {
      setModels((prev) => prev.map((m) => (m.id === record.id ? { ...m, is_enabled: !m.is_enabled } : m)))
    }
  }

  const handleTestModel = async (m: NousModel) => {
    setTestingId(m.id)
    try {
      const outcome = await runModelTest(m)
      if (outcome === 'ok') Message.success(`${m.actual_model}: OK`)
      // Says what happened instead of claiming a failure: nothing was checked,
      // and the reason is the model's type, not the model.
      else if (outcome === 'not_probed') {
        Message.info(`${m.actual_model}: not probed — no ${m.type} probe exists`)
      } else Message.error(`${m.actual_model}: connectivity test failed`)
    } finally {
      setTestingId(null)
    }
  }

  const pickerOptions = fetchedModels
    .filter((m) => !existingModels.has(m))
    .map((m) => ({ label: `${m}  ·  ${guessType(m)}`, value: m }))

  return (
    <div style={{ padding: '0 4px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title heading={5} style={{ margin: 0 }}>MediaHub AI Models</Title>
        <Button type="primary" icon={<IconPlus />} onClick={openAddProvider}>Add Provider</Button>
      </div>

      <JimengAuthCard />
      <CodexAuthCard />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><Spin /></div>
      ) : groups.length === 0 ? (
        <Card>
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-3)' }}>
            No providers configured. Click "Add Provider" — enter the base URL + key once, then pick the models.
          </div>
        </Card>
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          {groups.map((g) => (
            <Card key={`${g.provider}|${g.base_url}`}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <StatusDot
                      status={aggregateStatus(g.models)}
                      detail={failingModels(g.models)
                        .map((m) => `${m.actual_model}: ${m.last_test_detail || 'failed'}`)
                        .join(' | ') || undefined}
                    />
                    {g.provider}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-3)', fontFamily: 'monospace' }}>
                    {g.base_url || '(provider default base URL)'}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginTop: 2 }}>
                    Key&nbsp;
                    <span style={{ fontFamily: 'monospace' }}>{'••••' + (g.api_key_masked || '').slice(-4)}</span>
                  </div>
                  {(() => {
                    // Most-recent test across the provider's models.
                    const latest = g.models
                      .map((m) => m.last_tested_at)
                      .filter(Boolean)
                      .sort()
                      .pop()
                    const ago = timeAgo(latest)
                    return ago ? (
                      <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginTop: 2 }}>
                        Last tested {ago}
                      </div>
                    ) : null
                  })()}
                  {/* WHY a dot is red, visible at a glance — the persisted probe
                      reason (e.g. "HTTP 402: Insufficient Balance") used to hide
                      in an 8px-dot hover tooltip only. Full text stays on title. */}
                  {failingModels(g.models).map((m) => (
                    <div
                      key={`fail-${m.id}`}
                      title={m.last_test_detail || undefined}
                      style={{
                        fontSize: 12,
                        color: '#f53f3f',
                        marginTop: 2,
                        maxWidth: 560,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      ✕ {m.actual_model} — {m.last_test_detail || 'connectivity test failed'}
                    </div>
                  ))}
                </div>
                <Space>
                  {(() => {
                    const k = `${g.provider}|${g.base_url}`
                    const prog = testProgress[k]
                    return (
                      <Button
                        size="small"
                        loading={testingProvider === k}
                        onClick={() => handleTestProvider(g)}
                      >
                        {prog ? `Testing ${prog.done}/${prog.total}` : 'Test'}
                      </Button>
                    )
                  })()}
                  <Button size="small" icon={<IconEdit />} onClick={() => openEditProvider(g)}>Edit</Button>
                  <Button size="small" icon={<IconPlus />} onClick={() => openAddModels(g)}>Add Models</Button>
                </Space>
              </div>

              <Text style={{ fontSize: 12, color: 'var(--color-text-3)' }}>Enabled Models</Text>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 8 }}>
                {g.models.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8,
                      border: '1px solid var(--color-border-2)', borderRadius: 6,
                      padding: '4px 8px', opacity: m.is_enabled ? 1 : 0.5,
                    }}
                  >
                    <StatusDot
                      status={m.last_test_status}
                      detail={m.last_test_detail}
                      at={m.last_tested_at}
                    />
                    <Tag color={TYPE_COLORS[m.type] || 'gray'} size="small">{m.type}</Tag>
                    <span style={{ fontSize: 13, fontFamily: 'monospace' }}>{m.actual_model}</span>
                    <Switch
                      size="small"
                      checked={m.is_enabled}
                      onChange={() => handleToggleEnabled(m)}
                    />
                    <Button
                      size="mini"
                      type="text"
                      loading={testingId === m.id}
                      onClick={() => handleTestModel(m)}
                    >
                      Test
                    </Button>
                    <Button
                      icon={<IconEdit />}
                      size="mini"
                      type="text"
                      onClick={() => openEditModel(m)}
                    />
                    <Popconfirm title="Remove this model?" onOk={() => handleDeleteModel(m.id)}>
                      <Button icon={<IconDelete />} size="mini" status="danger" type="text" />
                    </Popconfirm>
                  </div>
                ))}
              </div>
            </Card>
          ))}
        </Space>
      )}

      <Modal
        title={modalMode === 'new' ? 'Add Provider' : `Add Models — ${modalGroup?.provider}`}
        visible={modalVisible}
        onOk={handleSaveModels}
        confirmLoading={saving}
        okText="Add"
        onCancel={() => setModalVisible(false)}
        autoFocus={false}
        style={{ maxWidth: 520 }}
      >
        <Form form={form} layout="vertical">
          {modalMode === 'new' ? (
            <>
              <FormItem
                label="Actual Provider"
                field="actual_provider"
                rules={[{ required: true }]}
                extra="Dispatch protocol. Unknown/custom values fall back to the generic OpenAI-compatible adapter."
              >
                <Select
                  allowCreate
                  showSearch
                  placeholder="Select a protocol or type a custom value"
                  options={protocolOptions}
                />
              </FormItem>
              <FormItem
                label="API Base URL"
                field="base_url"
                extra="OpenAI-compatible base URL (ends with /v1). Leave blank only for built-in providers with a default."
              >
                <Input placeholder="https://api.deepseek.com/v1" />
              </FormItem>
              <FormItem label="API Key" field="api_key" rules={[{ required: true }]}>
                <Input.Password placeholder="API Key (entered once for all models below)" />
              </FormItem>
              <FormItem label="App ID" field="app_id">
                <Input placeholder="Optional (volcengine)" />
              </FormItem>
            </>
          ) : (
            <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-text-2)' }}>
              <div><b>Provider:</b> {modalGroup?.provider}</div>
              <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{modalGroup?.base_url}</div>
              <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginTop: 2 }}>
                Key reused from this provider — no need to re-enter.
              </div>
            </div>
          )}

          <div style={{ marginBottom: 12 }}>
            <Button size="small" loading={probeLoading} onClick={handleProbe} icon={<IconSync />}>
              Test &amp; Load Models
            </Button>
          </div>

          <FormItem
            label="Models"
            extra="Pick the models to enable. Type auto-detected from the name. Providers without a model catalog (e.g. volcengine ASR) — type the model name and press Enter."
          >
            <Select
              mode="multiple"
              allowCreate
              showSearch
              placeholder="Load models above, then select"
              value={selectedModels}
              onChange={setSelectedModels}
              options={pickerOptions}
            />
          </FormItem>
        </Form>
      </Modal>

      {/* Edit modal — single model, or a whole provider's shared access. */}
      <Modal
        title={
          editModal?.mode === 'provider'
            ? `Edit Provider — ${editModal.group.provider}`
            : editModal?.mode === 'model'
              ? `Edit Model — ${editModal.model.actual_model}`
              : 'Edit'
        }
        visible={!!editModal}
        onOk={handleEditSave}
        confirmLoading={editSaving}
        onCancel={() => setEditModal(null)}
        okText="Save"
        unmountOnExit
      >
        <Form form={editForm} layout="vertical">
          {editModal?.mode === 'provider' && (
            <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-text-3)' }}>
              Changes apply to all {editModal.group.models.length} model(s) on
              this provider (they share the base URL + key).
            </div>
          )}

          {editModal?.mode === 'model' && (
            <>
              <FormItem label="Display Name" field="display_name" rules={[{ required: true }]}>
                <Input />
              </FormItem>
              <FormItem label="Actual Model" field="actual_model" rules={[{ required: true }]}>
                <Input placeholder="Model id sent to the provider" />
              </FormItem>
              <FormItem label="Type" field="type" rules={[{ required: true }]}>
                <Select
                  options={['llm', 'embedding', 'tts', 'asr'].map((v) => ({ label: v, value: v }))}
                />
              </FormItem>
              <FormItem label="Pricing Value" field="pricing_value">
                <Input type="number" />
              </FormItem>
            </>
          )}

          <FormItem label="API Base URL" field="base_url">
            <Input placeholder="https://api.example.com/v1" />
          </FormItem>

          {editModal?.mode === 'provider' && (
            <FormItem label="App ID" field="app_id">
              <Input placeholder="Optional (volcengine)" />
            </FormItem>
          )}

          <FormItem
            label="API Key"
            field="api_key"
            extra="Leave blank to keep the current key. Type a new one to replace it."
          >
            <Input.Password placeholder="•••• (unchanged)" />
          </FormItem>
        </Form>
      </Modal>
    </div>
  )
}
