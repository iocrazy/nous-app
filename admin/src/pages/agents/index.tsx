/**
 * Admin Agents Catalog — the master set of system agents users overlay.
 *
 * The override model (mig 341): system presets are the single rows every
 * user/team customizes via agent_overrides. This page is the admin CRUD
 * surface for the BASE rows: prompts, model params, enable flag — plus a
 * view of how many users/teams customized each agent, and a reload-seeds
 * action for recovery.
 */

import { useState, useEffect, useCallback } from 'react'
import {
  Button, Modal, Form, Input, InputNumber, Switch,
  Message, Tag, Typography, Card, Spin, Space, Popconfirm,
} from '@arco-design/web-react'
import { IconEdit, IconSync } from '@arco-design/web-react/icon'
import { useAuth } from '../../auth/AuthProvider'

const { Title, Text } = Typography
const FormItem = Form.Item

interface CatalogAgent {
  id: string
  slug: string
  name: string
  description?: string | null
  icon?: string | null
  model: string
  temperature: number
  max_tokens: number
  identity_md?: string | null
  soul_md?: string | null
  agent_md?: string | null
  fallback_models?: string[]
  enabled: boolean
  updated_at: string
  current_version: number
  override_counts: { user: number; team: number }
}

const apiBase = import.meta.env.VITE_API_URL || ''

export function AgentsCatalogPage() {
  const { session } = useAuth()
  const token = session?.access_token
  const headers = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  }

  const [agents, setAgents] = useState<CatalogAgent[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<CatalogAgent | null>(null)
  const [saving, setSaving] = useState(false)
  const [reseeding, setReseeding] = useState(false)
  const [form] = Form.useForm()

  const fetchAgents = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/agents`, { headers })
      const data = await res.json()
      setAgents(data.items || [])
    } catch {
      Message.error('Failed to load agents')
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => { void fetchAgents() }, [fetchAgents])

  const openEdit = (a: CatalogAgent) => {
    setEditing(a)
    form.resetFields()
    form.setFieldsValue({
      name: a.name,
      description: a.description || '',
      model: a.model,
      temperature: a.temperature,
      max_tokens: a.max_tokens,
      identity_md: a.identity_md || '',
      soul_md: a.soul_md || '',
      agent_md: a.agent_md || '',
      fallback_models: (a.fallback_models || []).join(', '),
    })
  }

  const handleSave = async () => {
    if (!editing) return
    let values: Record<string, unknown>
    try {
      values = await form.validate()
    } catch {
      Message.error('Please check the fields')
      return
    }
    setSaving(true)
    try {
      const fallback = String(values.fallback_models || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      const res = await fetch(`${apiBase}/api/v1/admin/agents/${editing.slug}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({
          name: values.name,
          description: values.description,
          model: values.model,
          temperature: Number(values.temperature),
          max_tokens: Number(values.max_tokens),
          identity_md: values.identity_md,
          soul_md: values.soul_md,
          agent_md: values.agent_md,
          fallback_models: fallback,
        }),
      })
      if (!res.ok) {
        const body = await res.text().catch(() => '')
        throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 120)}` : ''}`)
      }
      Message.success('Agent updated (versioned)')
      setEditing(null)
      fetchAgents()
    } catch (err) {
      Message.error(`Save failed: ${err instanceof Error ? err.message : 'error'}`)
    } finally {
      setSaving(false)
    }
  }

  const handleToggleEnabled = async (a: CatalogAgent) => {
    const res = await fetch(`${apiBase}/api/v1/admin/agents/${a.slug}`, {
      method: 'PUT',
      headers,
      body: JSON.stringify({ enabled: !a.enabled }),
    })
    if (res.ok) {
      setAgents((prev) =>
        prev.map((x) => (x.id === a.id ? { ...x, enabled: !x.enabled } : x)),
      )
    } else {
      Message.error('Failed to toggle')
    }
  }

  const handleReloadSeeds = async () => {
    setReseeding(true)
    try {
      const res = await fetch(`${apiBase}/api/v1/ai-library/admin/reload-seeds`, {
        method: 'POST',
        headers,
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        Message.success(
          `Seeds reloaded — agents: ${data.agents ?? '?'}, skills: ${data.skills ?? '?'}`,
        )
        fetchAgents()
      } else {
        Message.error('Reload seeds failed')
      }
    } catch {
      Message.error('Reload seeds failed')
    } finally {
      setReseeding(false)
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <div
        style={{
          display: 'flex', justifyContent: 'space-between',
          alignItems: 'center', marginBottom: 16,
        }}
      >
        <div>
          <Title heading={4} style={{ margin: 0 }}>Agents Catalog</Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            System agents users overlay with personal/team customizations.
            Edits here change the base defaults (versioned); user overrides
            are untouched.
          </Text>
        </div>
        <Popconfirm
          title="Re-run the seed loader? Seed-file content re-applies where changed."
          onOk={handleReloadSeeds}
        >
          <Button loading={reseeding} icon={<IconSync />}>Reload Seeds</Button>
        </Popconfirm>
      </div>

      {loading && agents.length === 0 ? (
        <Spin style={{ display: 'block', margin: '48px auto' }} />
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {agents.map((a) => (
            <Card key={a.id} size="small">
              <div
                style={{
                  display: 'flex', justifyContent: 'space-between',
                  alignItems: 'flex-start', gap: 16,
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Text bold>{a.name}</Text>
                    <Tag size="small" color="arcoblue">{a.slug}</Tag>
                    <Tag size="small">{a.model}</Tag>
                    <Tag size="small" color="gray">v{a.current_version}</Tag>
                    {(a.override_counts.user > 0 || a.override_counts.team > 0) && (
                      <Tag size="small" color="purple">
                        {a.override_counts.user} user / {a.override_counts.team} team overrides
                      </Tag>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: 12, color: 'var(--color-text-3)', marginTop: 4,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}
                  >
                    {a.description || '—'}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-3)', marginTop: 2 }}>
                    temp {a.temperature} · max {a.max_tokens} tokens
                    {a.fallback_models?.length
                      ? ` · fallback: ${a.fallback_models.join(' → ')}`
                      : ''}
                  </div>
                </div>
                <Space>
                  <Switch
                    size="small"
                    checked={a.enabled}
                    onChange={() => handleToggleEnabled(a)}
                  />
                  <Button size="small" icon={<IconEdit />} onClick={() => openEdit(a)}>
                    Edit
                  </Button>
                </Space>
              </div>
            </Card>
          ))}
        </Space>
      )}

      <Modal
        title={editing ? `Edit — ${editing.name} (${editing.slug})` : 'Edit'}
        visible={!!editing}
        onOk={handleSave}
        confirmLoading={saving}
        onCancel={() => setEditing(null)}
        okText="Save (versioned)"
        style={{ width: 720 }}
        unmountOnExit
      >
        <Form form={form} layout="vertical">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <FormItem label="Name" field="name" rules={[{ required: true }]}>
              <Input />
            </FormItem>
            <FormItem label="Model" field="model" rules={[{ required: true }]}>
              <Input placeholder="e.g. qwen-max, nous:<platform model>" />
            </FormItem>
            <FormItem label="Temperature" field="temperature">
              <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
            </FormItem>
            <FormItem label="Max Tokens" field="max_tokens">
              <InputNumber min={1} style={{ width: '100%' }} />
            </FormItem>
          </div>
          <FormItem label="Description" field="description">
            <Input.TextArea rows={2} />
          </FormItem>
          <FormItem
            label="Fallback Models"
            field="fallback_models"
            extra="Comma-separated, tried in order after the primary model exhausts retries."
          >
            <Input placeholder="model-a, model-b" />
          </FormItem>
          <FormItem label="Identity (identity_md)" field="identity_md">
            <Input.TextArea rows={4} style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </FormItem>
          <FormItem label="Soul (soul_md)" field="soul_md">
            <Input.TextArea rows={4} style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </FormItem>
          <FormItem label="Agent (agent_md)" field="agent_md">
            <Input.TextArea rows={4} style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </FormItem>
        </Form>
      </Modal>
    </div>
  )
}
