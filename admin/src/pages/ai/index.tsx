import { useState, useEffect, useCallback } from 'react'
import {
  Table, Button, Modal, Form, Input, Select, Switch, InputNumber,
  Space, Message, Popconfirm, Tag, Typography, Card,
} from '@arco-design/web-react'
import { IconPlus, IconEdit, IconDelete } from '@arco-design/web-react/icon'
import { useAuth } from '../../auth/AuthProvider'

const { Title } = Typography
const FormItem = Form.Item

interface NousModel {
  id: string
  name: string
  display_name: string
  category: string
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

const CATEGORY_OPTIONS = [
  { label: 'Transcription', value: 'transcription' },
  { label: 'Summarization', value: 'summarization' },
  { label: 'Analysis', value: 'analysis' },
]

const PRICING_TYPE_OPTIONS = [
  { label: 'Per Hour', value: 'per_hour' },
  { label: 'Per Request', value: 'per_request' },
  { label: 'Per Token', value: 'per_token' },
]

const CATEGORY_COLORS: Record<string, string> = {
  transcription: 'blue',
  summarization: 'green',
  analysis: 'purple',
}

export function AIModelsPage() {
  const { session } = useAuth()
  const token = session?.access_token
  const [models, setModels] = useState<NousModel[]>([])
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [editingModel, setEditingModel] = useState<NousModel | null>(null)
  const [form] = Form.useForm()

  const apiBase = import.meta.env.VITE_API_URL || ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  const fetchModels = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/nous-models`, { headers })
      if (res.ok) {
        setModels(await res.json())
      }
    } catch (e) {
      Message.error('Failed to fetch models')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { fetchModels() }, [fetchModels])

  const handleCreate = () => {
    setEditingModel(null)
    form.resetFields()
    form.setFieldsValue({ pricing_type: 'per_hour', pricing_value: 8, is_enabled: true, sort_order: 0 })
    setModalVisible(true)
  }

  const handleEdit = (record: NousModel) => {
    setEditingModel(record)
    form.setFieldsValue({
      ...record,
      api_key: '', // don't pre-fill masked key
    })
    setModalVisible(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validate()
      // Remove empty api_key on edit (keep existing)
      if (editingModel && !values.api_key) {
        delete values.api_key
      }

      const url = editingModel
        ? `${apiBase}/api/v1/admin/nous-models/${editingModel.id}`
        : `${apiBase}/api/v1/admin/nous-models`
      const method = editingModel ? 'PUT' : 'POST'

      const res = await fetch(url, { method, headers, body: JSON.stringify(values) })
      if (res.ok) {
        Message.success(editingModel ? 'Model updated' : 'Model created')
        setModalVisible(false)
        fetchModels()
      } else {
        const err = await res.json().catch(() => ({ detail: 'Request failed' }))
        Message.error(err.detail || 'Failed')
      }
    } catch {
      // validation error
    }
  }

  const handleDelete = async (id: string) => {
    const res = await fetch(`${apiBase}/api/v1/admin/nous-models/${id}`, { method: 'DELETE', headers })
    if (res.ok) {
      Message.success('Model deleted')
      fetchModels()
    } else {
      Message.error('Failed to delete')
    }
  }

  const handleToggleEnabled = async (record: NousModel) => {
    const res = await fetch(`${apiBase}/api/v1/admin/nous-models/${record.id}`, {
      method: 'PUT', headers,
      body: JSON.stringify({ is_enabled: !record.is_enabled }),
    })
    if (res.ok) {
      setModels(prev => prev.map(m => m.id === record.id ? { ...m, is_enabled: !m.is_enabled } : m))
    }
  }

  const columns = [
    {
      title: 'Name',
      dataIndex: 'display_name',
      render: (val: string, record: NousModel) => (
        <div>
          <div style={{ fontWeight: 500 }}>{val}</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-3)' }}>{record.name}</div>
        </div>
      ),
    },
    {
      title: 'Category',
      dataIndex: 'category',
      width: 130,
      render: (val: string) => <Tag color={CATEGORY_COLORS[val] || 'gray'}>{val}</Tag>,
    },
    {
      title: 'Provider : Model',
      render: (_: unknown, record: NousModel) => (
        <span style={{ fontSize: 13 }}>{record.actual_provider}:{record.actual_model}</span>
      ),
    },
    {
      title: 'API Key',
      dataIndex: 'api_key_masked',
      width: 140,
      render: (val: string) => <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{val}</span>,
    },
    {
      title: 'Pricing',
      width: 120,
      render: (_: unknown, record: NousModel) => {
        const unit = record.pricing_type === 'per_hour' ? '/hr' : record.pricing_type === 'per_request' ? '/req' : '/1k tok'
        return <span>{record.pricing_value} pts{unit}</span>
      },
    },
    {
      title: 'Enabled',
      width: 80,
      render: (_: unknown, record: NousModel) => (
        <Switch checked={record.is_enabled} onChange={() => handleToggleEnabled(record)} size="small" />
      ),
    },
    {
      title: 'Actions',
      width: 100,
      render: (_: unknown, record: NousModel) => (
        <Space>
          <Button icon={<IconEdit />} size="mini" onClick={() => handleEdit(record)} />
          <Popconfirm title="Delete this model?" onOk={() => handleDelete(record.id)}>
            <Button icon={<IconDelete />} size="mini" status="danger" />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: '0 4px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title heading={5} style={{ margin: 0 }}>Nous AI Models</Title>
        <Button type="primary" icon={<IconPlus />} onClick={handleCreate}>Add Model</Button>
      </div>

      <Card>
        <Table
          columns={columns}
          data={models}
          rowKey="id"
          loading={loading}
          pagination={false}
          noDataElement={<div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-3)' }}>No models configured. Click "Add Model" to create one.</div>}
        />
      </Card>

      <Modal
        title={editingModel ? 'Edit Model' : 'Add Model'}
        visible={modalVisible}
        onOk={handleSubmit}
        onCancel={() => setModalVisible(false)}
        autoFocus={false}
        style={{ maxWidth: 520 }}
      >
        <Form form={form} layout="vertical">
          <FormItem label="Name" field="name" rules={[{ required: true, message: 'Required' }]}>
            <Input placeholder="e.g. nous-llm" />
          </FormItem>
          <FormItem label="Display Name" field="display_name" rules={[{ required: true }]}>
            <Input placeholder="e.g. Nous LLM (Volcengine 2.0)" />
          </FormItem>
          <FormItem label="Category" field="category" rules={[{ required: true }]}>
            <Select options={CATEGORY_OPTIONS} />
          </FormItem>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <FormItem label="Actual Provider" field="actual_provider" rules={[{ required: true }]}>
              <Input placeholder="e.g. volcengine" />
            </FormItem>
            <FormItem label="Actual Model" field="actual_model" rules={[{ required: true }]}>
              <Input placeholder="e.g. seed-asr" />
            </FormItem>
          </div>
          <FormItem label={editingModel ? 'API Key (leave empty to keep existing)' : 'API Key'} field="api_key" rules={editingModel ? [] : [{ required: true }]}>
            <Input.Password placeholder="API Key" />
          </FormItem>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <FormItem label="App ID" field="app_id">
              <Input placeholder="Optional" />
            </FormItem>
            <FormItem label="Base URL" field="base_url">
              <Input placeholder="Optional override" />
            </FormItem>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            <FormItem label="Pricing Type" field="pricing_type">
              <Select options={PRICING_TYPE_OPTIONS} />
            </FormItem>
            <FormItem label="Price (points)" field="pricing_value">
              <InputNumber min={0} precision={1} />
            </FormItem>
            <FormItem label="Sort Order" field="sort_order">
              <InputNumber min={0} />
            </FormItem>
          </div>
          <FormItem label="Enabled" field="is_enabled" triggerPropName="checked">
            <Switch />
          </FormItem>
        </Form>
      </Modal>
    </div>
  )
}
