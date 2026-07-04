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
import { IconStorage, IconRobot, IconCode, IconSync, IconApps } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import { PromotionsSection } from './PromotionsSection'
import { MemoryStatsCard } from './MemoryStatsCard'
import { useAuth } from '../../auth/AuthProvider'

type CatalogModel = {
  name: string
  display_name: string
  type: string
  actual_model?: string
  base_url?: string
}

// Resolve a stored extractor/embedder config to a catalog entry. The dropdown
// options key off the catalog `name` (e.g. "nous-qwen3-llm"), but a config can
// store the RAW model string (`actual_model`, e.g. "qwen3-6-35b") — matching
// only by name then wrongly falls back to "Custom endpoint…". Match by name
// first, then by actual_model (preferring the same base_url).
function matchCatalogModel(
  list: CatalogModel[],
  model: string,
  baseUrl: string,
): CatalogModel | undefined {
  if (!model) return undefined
  const norm = (s?: string) => (s || '').replace(/\/+$/, '')
  const byName = list.find((c) => c.name === model)
  if (byName) return byName
  const byModelAndUrl = list.find(
    (c) => c.actual_model === model && norm(c.base_url) === norm(baseUrl),
  )
  if (byModelAndUrl) return byModelAndUrl
  return list.find((c) => c.actual_model === model)
}
import {
  useGraphMemorySettings,
  useUpdateGraphMemorySettings,
  type GraphMemorySettingsUpdate,
  useMemoryControl,
  useSetMemorySlot,
  useReloadMemorySlot,
  useHonchoConnection,
  useUpdateHonchoConnection,
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

const SLOT_META: Record<string, { label: string; providers: string[] }> = {
  l2: { label: 'L2 · User Model', providers: ['honcho', 'none'] },
  l3: { label: 'L3 · Knowledge Graph', providers: ['graphiti', 'none'] },
}

export function MemorySettings() {
  const { data, isLoading } = useGraphMemorySettings()
  const updateMutation = useUpdateGraphMemorySettings()
  const { session } = useAuth()

  const { data: control } = useMemoryControl()
  const setSlot = useSetMemorySlot()
  const reloadSlot = useReloadMemorySlot()
  const [catalog, setCatalog] = useState<CatalogModel[]>([])

  const { data: honcho } = useHonchoConnection()
  const updateHoncho = useUpdateHonchoConnection()
  const [hcEnabled, setHcEnabled] = useState(false)
  const [hcBaseUrl, setHcBaseUrl] = useState('')
  const [hcWorkspace, setHcWorkspace] = useState('')

  useEffect(() => {
    if (!honcho) return
    setHcEnabled(honcho.enabled)
    setHcBaseUrl(honcho.base_url)
    setHcWorkspace(honcho.workspace_id)
  }, [honcho])

  // Load the platform-model catalog so the extractor/embedder can be assigned by
  // selection (instead of hand-typing base_url/model/api_key).
  useEffect(() => {
    const token = session?.access_token
    if (!token) return
    const apiBase = import.meta.env.VITE_API_URL || ''
    fetch(`${apiBase}/api/v1/admin/mediahub-models`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => setCatalog(Array.isArray(rows) ? rows : []))
      .catch(() => setCatalog([]))
  }, [session])

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
      <>
        <Card title="Memory (Graphiti)" style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
            <Spin size={28} />
          </div>
        </Card>
        <MemoryStatsCard />
        <PromotionsSection />
      </>
    )
  }

  const keyTag = (isSet: boolean) =>
    isSet ? <Tag color="green">key set</Tag> : <Tag>no key</Tag>

  return (
    <>
    <Card title="Memory (Graphiti)" style={{ marginBottom: 20 }}>
      <SectionHeader
        icon={<IconStorage />}
        title="Graph Backend (FalkorDB)"
        subtitle="The knowledge-graph store that powers long-term memory."
      />
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
      <SectionHeader
        icon={<IconRobot />}
        title="Extractor LLM"
        subtitle="OpenAI-compatible LLM that extracts entities/edges into the graph."
      />
      {(() => {
        const llm = catalog.filter((c) => c.type === 'llm')
        const matched = matchCatalogModel(llm, exModel, exBaseUrl)
        const usingCatalog = !!matched
        return (
          <>
            <Row
              label="Platform model"
              hint="Pick an LLM from the MediaHub catalog (provider + key come from it), or Custom to enter a provider manually."
            >
              <Select
                value={matched ? matched.name : '__custom__'}
                onChange={(v) => {
                  if (v === '__custom__') setExModel('')
                  else {
                    setExModel(v)
                    setExBaseUrl('')
                    setExKey('')
                  }
                }}
                placeholder="Select a platform model"
                style={{ width: 280 }}
              >
                {llm.map((c) => (
                  <Select.Option key={c.name} value={c.name}>{c.display_name}</Select.Option>
                ))}
                <Select.Option value="__custom__">Custom endpoint…</Select.Option>
              </Select>
            </Row>
            {!usingCatalog && (
              <>
                <Divider style={{ margin: 0 }} />
                <Row label="Base URL">
                  <Input value={exBaseUrl} onChange={setExBaseUrl} placeholder="https://.../v1" style={{ width: 260 }} />
                </Row>
                <Divider style={{ margin: 0 }} />
                <Row label="Model">
                  <Input value={exModel} onChange={setExModel} placeholder="e.g. Qwen/Qwen3-235B-A22B-Instruct-2507" style={{ width: 260 }} />
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
              </>
            )}
          </>
        )
      })()}
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

      <Divider />
      <SectionHeader
        icon={<IconCode />}
        title="Embedder"
        subtitle="Shared memory embedder (Graphiti + Honcho)."
      />
      <div style={{ color: 'var(--color-text-3)', fontSize: 12, marginTop: 4, marginBottom: 4 }}>
        Used by Graphiti (live, via these settings) and Honcho (its container env is kept in
        sync with these values — see the memory-embedder runbook). Dimensions is shared by
        both; capped at {DIM_MAX} (Qwen3-Embedding-8B native width). Graphiti runs on FalkorDB
        and Honcho on LanceDB, so 4096 is indexable on both.
      </div>
      {(() => {
        const emb = catalog.filter((c) => c.type === 'embedding')
        const matched = matchCatalogModel(emb, emModel, emBaseUrl)
        const usingCatalog = !!matched
        return (
          <>
            <Row
              label="Platform model"
              hint="Pick an embedding model from the MediaHub catalog (provider + key come from it), or Custom to enter a provider manually."
            >
              <Select
                value={matched ? matched.name : '__custom__'}
                onChange={(v) => {
                  if (v === '__custom__') setEmModel('')
                  else {
                    setEmModel(v)
                    setEmBaseUrl('')
                    setEmKey('')
                  }
                }}
                placeholder="Select a platform model"
                style={{ width: 280 }}
              >
                {emb.map((c) => (
                  <Select.Option key={c.name} value={c.name}>{c.display_name}</Select.Option>
                ))}
                <Select.Option value="__custom__">Custom endpoint…</Select.Option>
              </Select>
            </Row>
            {!usingCatalog && (
              <>
                <Divider style={{ margin: 0 }} />
                <Row label="Base URL" hint="Optional — defaults to the extractor/env config when blank.">
                  <Input value={emBaseUrl} onChange={setEmBaseUrl} placeholder="https://.../v1" style={{ width: 260 }} />
                </Row>
                <Divider style={{ margin: 0 }} />
                <Row label="Model">
                  <Input value={emModel} onChange={setEmModel} placeholder="e.g. Qwen/Qwen3-Embedding-4B" style={{ width: 260 }} />
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
              </>
            )}
          </>
        )
      })()}
      <Divider style={{ margin: 0 }} />
      <Row
        label="Dimensions"
        hint={`Embedding output width; sizes the vector index. 1536 = Qwen3-Embedding-4B, 4096 = Qwen3-Embedding-8B, 2048 = doubao-embedding-vision. Max ${DIM_MAX}.`}
      >
        <Input
          value={emDim}
          onChange={setEmDim}
          placeholder="1536"
          style={{ width: 260 }}
          status={dimInvalid ? 'error' : undefined}
        />
      </Row>

      <Divider />
      <SectionHeader
        icon={<IconApps />}
        title="Provider Slots"
        subtitle="Choose the backend for each memory layer, see its health, and reload it after a config change (no backend restart)."
      />
      {(control?.slots ?? []).map((s) => {
        const meta = SLOT_META[s.slot] ?? { label: s.slot, providers: [s.provider] }
        return (
          <div
            key={s.slot}
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                title={s.health ? 'Healthy' : 'Unreachable / disabled'}
                style={{
                  display: 'inline-block', width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                  background: s.health ? '#00b42a' : '#f53f3f',
                }}
              />
              <span style={{ fontWeight: 500, fontSize: 14 }}>{meta.label}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Select
                value={s.provider}
                style={{ width: 160 }}
                onChange={(provider: string) =>
                  setSlot.mutate(
                    { slot: s.slot, provider },
                    {
                      onSuccess: () => Message.success(`${meta.label} → ${provider}`),
                      onError: (e: unknown) => Message.error((e as Error)?.message || 'Failed to switch provider'),
                    },
                  )
                }
              >
                {meta.providers.map((p) => (
                  <Select.Option key={p} value={p}>{p}</Select.Option>
                ))}
              </Select>
              <Button
                size="small"
                icon={<IconSync />}
                loading={reloadSlot.isPending}
                disabled={s.provider === 'none'}
                onClick={() =>
                  reloadSlot.mutate(s.slot, {
                    onSuccess: (r) =>
                      r.ok
                        ? Message.success(`Reloaded ${r.reloaded}`)
                        : Message.warning('Slot disabled — nothing to reload'),
                    onError: (e: unknown) => Message.error((e as Error)?.message || 'Reload failed'),
                  })
                }
              >
                Reload
              </Button>
            </div>
          </div>
        )
      })}

      <Divider />
      <SectionHeader
        icon={<IconRobot />}
        title="Honcho (L2) connection"
        subtitle="How MediaHub reaches the Honcho service. After saving, click L2 Reload above to apply. (Honcho's own embedding/LLM live in its container — env-managed on the NAS.)"
      />
      <Row label="Enabled" hint="Master toggle for the L2 user-model layer.">
        <Switch checked={hcEnabled} onChange={setHcEnabled} disabled={updateHoncho.isPending} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="Base URL" hint="e.g. http://192.168.50.9:18000">
        <Input value={hcBaseUrl} onChange={setHcBaseUrl} placeholder="http://…:18000" style={{ width: 260 }} />
      </Row>
      <Divider style={{ margin: 0 }} />
      <Row label="Workspace">
        <Input value={hcWorkspace} onChange={setHcWorkspace} placeholder="mediahub" style={{ width: 260 }} />
      </Row>
      <div style={{ textAlign: 'right', paddingTop: 12 }}>
        <Button
          type="primary"
          loading={updateHoncho.isPending}
          onClick={() =>
            updateHoncho.mutate(
              { enabled: hcEnabled, base_url: hcBaseUrl.trim(), workspace_id: hcWorkspace.trim() },
              {
                onSuccess: () => Message.success('Honcho connection saved — click L2 Reload to apply'),
                onError: (e: unknown) => Message.error((e as Error)?.message || 'Failed to save'),
              },
            )
          }
        >
          Save Honcho connection
        </Button>
      </div>

      <Divider />
      <div style={{ textAlign: 'right' }}>
        <Button type="primary" loading={updateMutation.isPending} disabled={dimInvalid} onClick={handleSave}>
          Save
        </Button>
      </div>
    </Card>
    <MemoryStatsCard />
    <PromotionsSection />
    </>
  )
}
