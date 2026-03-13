import { useEffect } from 'react'
import {
  Modal,
  Form,
  Input,
  Select,
} from '@arco-design/web-react'
import type { TagData, TagGroup } from '../../api/endpoints/tags'

const PRESET_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6',
  '#3b82f6', '#6366f1', '#8b5cf6', '#ec4899', '#f472b6',
  '#64748b', '#84cc16', '#06b6d4', '#a855f7',
]

interface TagFormModalProps {
  visible: boolean
  tag: TagData | null // null = create, non-null = edit
  groups: TagGroup[]
  onSubmit: (values: {
    name: string
    name_zh?: string
    color?: string
    icon?: string
    group_id?: string
  }) => void
  onClose: () => void
}

export function TagFormModal({ visible, tag, groups, onSubmit, onClose }: TagFormModalProps) {
  const [form] = Form.useForm()
  const isEdit = tag !== null

  useEffect(() => {
    if (visible) {
      if (tag) {
        form.setFieldsValue({
          name: tag.name,
          name_zh: tag.name_zh || '',
          color: tag.color || '#6366f1',
          icon: tag.icon || '',
          group_id: tag.group_id || undefined,
        })
      } else {
        form.resetFields()
        form.setFieldsValue({ color: '#6366f1' })
      }
    }
  }, [visible, tag, form])

  const handleOk = async () => {
    try {
      const values = await form.validate()
      onSubmit({
        name: values.name,
        name_zh: values.name_zh || undefined,
        color: values.color,
        icon: values.icon || undefined,
        group_id: values.group_id || undefined,
      })
    } catch {
      // validation failed
    }
  }

  return (
    <Modal
      title={isEdit ? 'Edit Tag' : 'New Tag'}
      visible={visible}
      onOk={handleOk}
      onCancel={onClose}
      autoFocus={false}
      unmountOnExit
    >
      <Form form={form} layout="vertical">
        <Form.Item label="Name" field="name" rules={[{ required: true, message: 'Name is required' }]}>
          <Input placeholder="Tag name (English)" />
        </Form.Item>
        <Form.Item label="Chinese Name" field="name_zh">
          <Input placeholder="Optional Chinese name" />
        </Form.Item>
        <Form.Item label="Color" field="color">
          <ColorPicker form={form} />
        </Form.Item>
        <Form.Item label="Icon" field="icon">
          <Input placeholder="Emoji icon (e.g. 🎵)" />
        </Form.Item>
        <Form.Item label="Group" field="group_id">
          <Select placeholder="Uncategorized" allowClear>
            {groups.map((g) => (
              <Select.Option key={g.id} value={g.id}>
                {g.name}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
      </Form>
    </Modal>
  )
}

function ColorPicker({ form }: { form: ReturnType<typeof Form.useForm>[0] }) {
  const currentColor = Form.useWatch('color', form) || '#6366f1'
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {PRESET_COLORS.map((c) => (
        <div
          key={c}
          onClick={() => form.setFieldValue('color', c)}
          style={{
            width: 28,
            height: 28,
            borderRadius: 6,
            background: c,
            cursor: 'pointer',
            border: currentColor === c ? '2px solid var(--color-text-1)' : '2px solid transparent',
            boxSizing: 'border-box',
          }}
        />
      ))}
    </div>
  )
}
