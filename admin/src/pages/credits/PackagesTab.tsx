import { useState } from 'react'
import {
  Card,
  Grid,
  Button,
  Modal,
  Form,
  Input,
  InputNumber,
  Switch,
  Message,
  Space,
  Tag,
  Typography,
  Empty,
} from '@arco-design/web-react'
import { IconPlus, IconEdit, IconDelete } from '@arco-design/web-react/icon'
import {
  usePackages,
  useCreatePackage,
  useUpdatePackage,
  useDeletePackage,
  type PointPackage,
} from '../../api/endpoints/credits'

const { Row, Col } = Grid
const { Title, Text } = Typography
const FormItem = Form.Item

function PackageModal({
  visible,
  editingPackage,
  onClose,
}: {
  visible: boolean
  editingPackage: PointPackage | null
  onClose: () => void
}) {
  const [form] = Form.useForm()
  const createPackage = useCreatePackage()
  const updatePackage = useUpdatePackage()

  const isEditing = editingPackage !== null
  const isPending = createPackage.isPending || updatePackage.isPending

  const handleSubmit = () => {
    form.validate().then((values) => {
      const payload = {
        name: values.name as string,
        description: (values.description as string) || null,
        points_amount: values.points_amount as number,
        price_cents: Math.round((values.price_yuan as number) * 100),
        sort_order: (values.sort_order as number) ?? 0,
        is_active: (values.is_active as boolean) ?? true,
      }

      if (isEditing) {
        updatePackage.mutate(
          { id: editingPackage.id, ...payload },
          {
            onSuccess: () => {
              Message.success('Package updated successfully')
              form.resetFields()
              onClose()
            },
            onError: (err) => {
              Message.error(err.message || 'Failed to update package')
            },
          },
        )
      } else {
        createPackage.mutate(payload, {
          onSuccess: () => {
            Message.success('Package created successfully')
            form.resetFields()
            onClose()
          },
          onError: (err) => {
            Message.error(err.message || 'Failed to create package')
          },
        })
      }
    })
  }

  return (
    <Modal
      title={isEditing ? 'Edit Package' : 'Add Package'}
      visible={visible}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={isPending}
      unmountOnExit
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={
          isEditing
            ? {
                name: editingPackage.name,
                description: editingPackage.description ?? '',
                points_amount: editingPackage.points_amount,
                price_yuan: editingPackage.price_cents / 100,
                sort_order: editingPackage.sort_order,
                is_active: editingPackage.is_active,
              }
            : {
                sort_order: 0,
                is_active: true,
              }
        }
      >
        <FormItem
          label="Name"
          field="name"
          rules={[{ required: true, message: 'Name is required' }]}
        >
          <Input placeholder="Package name" />
        </FormItem>
        <FormItem label="Description" field="description">
          <Input.TextArea placeholder="Optional description" />
        </FormItem>
        <FormItem
          label="Points Amount"
          field="points_amount"
          rules={[{ required: true, message: 'Points amount is required' }]}
        >
          <InputNumber min={1} placeholder="Number of points" style={{ width: '100%' }} />
        </FormItem>
        <FormItem
          label="Price (¥)"
          field="price_yuan"
          rules={[{ required: true, message: 'Price is required' }]}
        >
          <InputNumber min={0} step={0.01} placeholder="Price in yuan" style={{ width: '100%' }} />
        </FormItem>
        <FormItem label="Sort Order" field="sort_order">
          <InputNumber min={0} style={{ width: '100%' }} />
        </FormItem>
        <FormItem
          label="Is Active"
          field="is_active"
          triggerPropName="checked"
        >
          <Switch />
        </FormItem>
      </Form>
    </Modal>
  )
}

function handleDelete(id: string, deleteFn: ReturnType<typeof useDeletePackage>) {
  Modal.confirm({
    title: 'Delete Package',
    content: 'Are you sure you want to delete this package? This action cannot be undone.',
    okButtonProps: { status: 'danger' },
    onOk: () =>
      new Promise<void>((resolve, reject) => {
        deleteFn.mutate(id, {
          onSuccess: () => {
            Message.success('Package deleted successfully')
            resolve()
          },
          onError: (err) => {
            Message.error(err.message || 'Failed to delete package')
            reject()
          },
        })
      }),
  })
}

export function PackagesTab() {
  const [modalVisible, setModalVisible] = useState(false)
  const [editingPackage, setEditingPackage] = useState<PointPackage | null>(null)

  const { data: packages, isLoading } = usePackages()
  const deletePackage = useDeletePackage()

  const openCreate = () => {
    setEditingPackage(null)
    setModalVisible(true)
  }

  const openEdit = (pkg: PointPackage) => {
    setEditingPackage(pkg)
    setModalVisible(true)
  }

  const closeModal = () => {
    setEditingPackage(null)
    setModalVisible(false)
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title heading={6} style={{ margin: 0 }}>Packages</Title>
        <Button type="primary" icon={<IconPlus />} onClick={openCreate}>
          Add Package
        </Button>
      </div>

      {/* Card Grid */}
      {!isLoading && (!packages || packages.length === 0) ? (
        <Empty description="No packages yet" />
      ) : (
        <Row gutter={16}>
          {(packages ?? []).map((pkg) => (
            <Col key={pkg.id} span={6} style={{ marginBottom: 16 }}>
              <Card
                hoverable
                actions={[
                  <Button
                    key="edit"
                    type="text"
                    icon={<IconEdit />}
                    size="small"
                    onClick={() => openEdit(pkg)}
                  >
                    Edit
                  </Button>,
                  <Button
                    key="delete"
                    type="text"
                    status="danger"
                    icon={<IconDelete />}
                    size="small"
                    onClick={() => handleDelete(pkg.id, deletePackage)}
                  >
                    Delete
                  </Button>,
                ]}
              >
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Text bold>{pkg.name}</Text>
                  {pkg.description && (
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      {pkg.description}
                    </Text>
                  )}
                  <Text>{pkg.points_amount} Points</Text>
                  <Text>¥{(pkg.price_cents / 100).toFixed(2)}</Text>
                  <Tag color={pkg.is_active ? 'green' : 'gray'}>
                    {pkg.is_active ? 'Active' : 'Inactive'}
                  </Tag>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {/* Package Modal */}
      <PackageModal
        visible={modalVisible}
        editingPackage={editingPackage}
        onClose={closeModal}
      />
    </div>
  )
}
