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
  Select,
} from '@arco-design/web-react'
import {
  useGraphMemorySettings,
  useUpdateGraphMemorySettings,
  type GraphMemorySettingsUpdate,
} from '../../api/endpoints/settings'

/**
 * Graphiti graph-memory config panel. Reads the masked bundle
 * (GET /admin/settings/graph-memory — api keys come back only as *_set
 * booleans) and writes via PUT. API-key inputs start empty: leave blank to
 * keep the stored key, type a new value to replace it. The gate stays off in
 * prod until a FalkorDB host is set, the toggle is on, AND a FalkorDB backend
 * exists (separate infra/bake step).
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

export function MemorySettings() {
  const { data, isLoading } = useGraphMemorySettings()
  const updateMutation = useUpdateGraphMemorySettings()

  const [enabled, setEnabled] = useState(false)
  const [host, setHost] = useState('')
  const [port, setPort] = useState('6379')
  const [database, setDatabase] = useState('mediahub_memory')
  const [exBaseUrl, setExBaseUrl] = useState('')
  const [exModel, setExModel] = useState('')
  const [exMode, setExMode] = useState('json_object')
  const [exKey, setExKey] = useState('')
  const [emBaseUrl, setEmBaseUrl] = useState('')
  const [emModel, setEmModel] = useState('')
  const [emKey, setEmKey] = useState('')
  const [emDim, setEmDim] = useState('1536')

  // Hydrate local form from the masked GET (never includes raw keys).
  useEffect(() => {
    if (!data) return
    setEnabled(data.enabled)
    setHost(data.falkordb_host)
    setPort(data.falkordb_port)
    setDatabase(data.falkordb_database)
    setExBaseUrl(data.extractor_base_url)
    setExModel(data.extractor_model)
    setExMode(data.extractor_structured_output_mode || 'json_object')
    setEmBaseUrl(data.embedder_base_url)
    setEmModel(data.embedder_model)
    setEmDim(String(data.embedder_dimensions ?? 1536))
    setExKey('')
    setEmKey('')
  }, [data])

  const DIM_MAX = 4096
  const dimNum = Number(emDim)
  const dimInvalid = !Number.isInteger(dimNum) || dimNum < 1 || dimNum > DIM_MAX

  const handleSave = () => {
    if (dimInvalid) {
      Message.error(`Embedder dimensions must be an integer between 1 and ${DIM_MAX}`)
      return
    }
    const payload: GraphMemorySettingsUpdate = {
      enabled,
      falkordb_host: host,
      falkordb_port: port,
      falkordb_database: database,
      extractor_base_url: exBaseUrl,
      extractor_model: exModel,
      extractor_structured_output_mode: exMode,
      embedder_base_url: emBaseUrl,
      embedder_model: emModel,
      embedder_dimensions: dimNum,
    }
    // Only send key fields when the admin actually typed one — blank keeps the
    // stored key unchanged.
    if (exKey.trim()) payload.extractor_api_key = exKey.trim()
    if (emKey.trim()) payload.embedder_api_key = emKey.trim()
    updateMutation.mutate(payload, {
      onSuccess: () => {
        Message.success('Graph memory settings saved')
        setExKey('')
        setEmKey('')
      },
      onError: (err) => Message.error((err as Error).message || 'Failed to save'),
    })
  }

  if (isLoading) {
    return (
      <Card title="Memory (Graphiti)" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Spin size={28} />
        </div>
      </Card>
    )
  }

  const keyTag = (isSet: boolean) =>
    isSet ? <Tag color="green">key set</Tag> : <Tag>no key</Tag>

  return (
    <Card title="Memory (Graphiti)" style={{ marginBottom: 20 }}>
      <Row label="Enabled" hint="Master gate. Needs a FalkorDB host + a running backend to take effect.">
        <Switch checked={enabled} onChange={setEnabled} disabled={updateMutation.isPending} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="FalkorDB host" hint="Graph backend address; empty = inoperative.">
        <Input value={host} onChange={setHost} placeholder="e.g. 192.168.50.9" style={{ width: 260 }} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="FalkorDB port">
        <Input value={port} onChange={setPort} style={{ width: 260 }} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="FalkorDB database">
        <Input value={database} onChange={setDatabase} style={{ width: 260 }} />
      </Row>

      <Divider />
      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--color-text-2)' }}>Extractor LLM (OpenAI-compatible)</div>
      <Row label="Base URL">
        <Input value={exBaseUrl} onChange={setExBaseUrl} placeholder="https://.../v1" style={{ width: 260 }} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="Model">
        <Input value={exModel} onChange={setExModel} placeholder="e.g. Qwen/Qwen3-235B-A22B-Instruct-2507" style={{ width: 260 }} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row
        label="Structured output"
        hint="json_object works on ModelScope/Qwen/DeepSeek; json_schema needs native constrained-decoding support (e.g. OpenAI)."
      >
        <Select value={exMode} onChange={setExMode} style={{ width: 200 }}>
          <Select.Option value="json_object">json_object (default)</Select.Option>
          <Select.Option value="json_schema">json_schema</Select.Option>
        </Select>
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="API key" hint="Must support structured output (chat completions).">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'flex-end' }}>
          {keyTag(data?.extractor_api_key_set ?? false)}
          <Input.Password
            value={exKey}
            onChange={setExKey}
            placeholder={data?.extractor_api_key_set ? 'leave blank to keep' : 'set a key'}
            style={{ width: 200 }}
          />
        </div>
      </Row>

      <Divider />
      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--color-text-2)' }}>Embedder (shared memory embedder)</div>
      <div style={{ color: 'var(--color-text-3)', fontSize: 12, marginTop: 4, marginBottom: 4 }}>
        Used by Graphiti (live, via these settings) and Honcho (its container env is kept in
        sync with these values — see the memory-embedder runbook). Dimensions is shared by
        both; capped at {DIM_MAX} (Qwen3-Embedding-8B native width). Graphiti runs on FalkorDB
        and Honcho on LanceDB, so 4096 is indexable on both.
      </div>
      <Row label="Base URL" hint="Optional — defaults to the extractor/env config when blank.">
        <Input value={emBaseUrl} onChange={setEmBaseUrl} placeholder="https://.../v1" style={{ width: 260 }} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="Model">
        <Input value={emModel} onChange={setEmModel} placeholder="e.g. Qwen/Qwen3-Embedding-4B" style={{ width: 260 }} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row
        label="Dimensions"
        hint={`Embedding output width; sizes the vector index. 1536 = Qwen3-Embedding-4B, 4096 = Qwen3-Embedding-8B. Max ${DIM_MAX}.`}
      >
        <Input
          value={emDim}
          onChange={setEmDim}
          placeholder="1536"
          style={{ width: 260 }}
          status={dimInvalid ? 'error' : undefined}
        />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="API key">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'flex-end' }}>
          {keyTag(data?.embedder_api_key_set ?? false)}
          <Input.Password
            value={emKey}
            onChange={setEmKey}
            placeholder={data?.embedder_api_key_set ? 'leave blank to keep' : 'set a key'}
            style={{ width: 200 }}
          />
        </div>
      </Row>

      <Divider />
      <div style={{ textAlign: 'right' }}>
        <Button type="primary" loading={updateMutation.isPending} disabled={dimInvalid} onClick={handleSave}>
          Save
        </Button>
      </div>
    </Card>
  )
}
