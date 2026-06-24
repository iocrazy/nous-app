import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Button, Modal, Form, Input, Select, Switch,
  Space, Message, Popconfirm, Tag, Typography, Card, Spin,
} from '@arco-design/web-react'
import { IconPlus, IconDelete, IconSync } from '@arco-design/web-react/icon'
import { useAuth } from '../../auth/AuthProvider'

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
  const [selectedModels, setSelectedModels] = useState<string[]>([])
  const [probeLoading, setProbeLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [form] = Form.useForm()

  const apiBase = import.meta.env.VITE_API_URL || ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  const fetchModels = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/nous-models`, { headers })
      if (res.ok) setModels(await res.json())
    } catch {
      Message.error('Failed to fetch models')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { fetchModels() }, [fetchModels])

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

  const handleProbe = async () => {
    const provider_key = form.getFieldValue('actual_provider')
    if (!provider_key) {
      Message.warning('Enter Actual Provider first')
      return
    }
    setProbeLoading(true)
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/nous-models/probe-models`, {
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
    } catch {
      return
    }
    setSaving(true)
    let ok = 0
    const failed: string[] = []
    for (const model of selectedModels) {
      const payload = {
        name: sanitizeName(model),
        display_name: model,
        type: guessType(model),
        actual_provider: values.actual_provider as string,
        actual_model: model,
        // 'new' provider → the typed key; 'add' → blank, backend inherits it.
        api_key: (values.api_key as string) || '',
        base_url: (values.base_url as string) || '',
        app_id: (values.app_id as string) || '',
        pricing_type: 'per_hour',
        pricing_value: 0,
        is_enabled: true,
        sort_order: 0,
      }
      try {
        const res = await fetch(`${apiBase}/api/v1/admin/nous-models`, {
          method: 'POST',
          headers,
          body: JSON.stringify(payload),
        })
        if (res.ok) ok += 1
        else failed.push(model)
      } catch {
        failed.push(model)
      }
    }
    setSaving(false)
    if (ok) Message.success(`Added ${ok} model${ok > 1 ? 's' : ''}`)
    if (failed.length) Message.error(`Failed: ${failed.join(', ')}`)
    setModalVisible(false)
    fetchModels()
  }

  const handleDeleteModel = async (id: string) => {
    const res = await fetch(`${apiBase}/api/v1/admin/nous-models/${id}`, { method: 'DELETE', headers })
    if (res.ok) {
      Message.success('Model removed')
      fetchModels()
    } else {
      Message.error('Failed to remove')
    }
  }

  const handleToggleEnabled = async (record: NousModel) => {
    const res = await fetch(`${apiBase}/api/v1/admin/nous-models/${record.id}`, {
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
      const res = await fetch(`${apiBase}/api/v1/admin/nous-models/${m.id}/test`, { method: 'POST', headers })
      const data = await res.json()
      if (data.ok) {
        Message.success(`${m.actual_model}: OK${data.detail ? ` — ${data.detail}` : ''}`)
      } else {
        Message.error(`${m.actual_model}: ${data.error || 'connectivity test failed'}`)
      }
    } catch {
      Message.error(`${m.actual_model}: request failed`)
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
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{g.provider}</div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-3)', fontFamily: 'monospace' }}>
                    {g.base_url || '(provider default base URL)'}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginTop: 2 }}>
                    Key&nbsp;
                    <span style={{ fontFamily: 'monospace' }}>{'••••' + (g.api_key_masked || '').slice(-4)}</span>
                  </div>
                </div>
                <Button size="small" icon={<IconPlus />} onClick={() => openAddModels(g)}>Add Models</Button>
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
              <FormItem label="Actual Provider" field="actual_provider" rules={[{ required: true }]}>
                <Input placeholder="e.g. openai, deepseek, doubao" />
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
    </div>
  )
}
