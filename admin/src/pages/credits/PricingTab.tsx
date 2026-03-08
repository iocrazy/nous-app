import { useState } from 'react'
import {
  Table,
  Button,
  Modal,
  Form,
  InputNumber,
  Input,
  Message,
  Typography,
} from '@arco-design/web-react'
import { IconEdit } from '@arco-design/web-react/icon'
import {
  usePricing,
  useUpdatePricing,
  type PricingRule,
} from '../../api/endpoints/credits'

const { Title } = Typography
const FormItem = Form.Item

function EditPricingModal({
  visible,
  editingRule,
  onClose,
}: {
  visible: boolean
  editingRule: PricingRule | null
  onClose: () => void
}) {
  const [form] = Form.useForm()
  const updatePricing = useUpdatePricing()

  const handleSubmit = () => {
    if (!editingRule) return

    form.validate().then((values) => {
      updatePricing.mutate(
        {
          action_type: editingRule.action_type,
          points_cost: values.points_cost as number,
          description: (values.description as string) || null,
        },
        {
          onSuccess: () => {
            Message.success('Pricing updated successfully')
            form.resetFields()
            onClose()
          },
          onError: (err) => {
            Message.error(err.message || 'Failed to update pricing')
          },
        },
      )
    })
  }

  return (
    <Modal
      title="Edit Pricing"
      visible={visible}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={updatePricing.isPending}
      unmountOnExit
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={
          editingRule
            ? {
                points_cost: editingRule.points_cost,
                description: editingRule.description ?? '',
              }
            : undefined
        }
      >
        <FormItem
          label="Points Cost"
          field="points_cost"
          rules={[{ required: true, message: 'Points cost is required' }]}
        >
          <InputNumber min={0} placeholder="Cost in points" style={{ width: '100%' }} />
        </FormItem>
        <FormItem label="Description" field="description">
          <Input.TextArea placeholder="Optional description" />
        </FormItem>
      </Form>
    </Modal>
  )
}

export function PricingTab() {
  const [editingRule, setEditingRule] = useState<PricingRule | null>(null)
  const [modalVisible, setModalVisible] = useState(false)

  const { data: pricingRules, isLoading } = usePricing()

  const openEdit = (rule: PricingRule) => {
    setEditingRule(rule)
    setModalVisible(true)
  }

  const closeModal = () => {
    setEditingRule(null)
    setModalVisible(false)
  }

  const columns = [
    {
      title: 'Action Type',
      dataIndex: 'action_type',
    },
    {
      title: 'Points Cost',
      dataIndex: 'points_cost',
    },
    {
      title: 'Description',
      dataIndex: 'description',
    },
    {
      title: 'Actions',
      render: (_: unknown, record: PricingRule) => (
        <Button
          type="text"
          icon={<IconEdit />}
          size="small"
          onClick={() => openEdit(record)}
        >
          Edit
        </Button>
      ),
    },
  ]

  return (
    <div>
      <Title heading={6} style={{ marginBottom: 16 }}>Action Pricing</Title>

      <Table
        columns={columns}
        data={pricingRules ?? []}
        rowKey="action_type"
        loading={isLoading}
        pagination={false}
      />

      <EditPricingModal
        visible={modalVisible}
        editingRule={editingRule}
        onClose={closeModal}
      />
    </div>
  )
}
