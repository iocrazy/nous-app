import { useState } from 'react'
import {
  Card,
  Table,
  Button,
  Message,
  Modal,
  Tabs,
  Tag,
  Divider,
} from '@arco-design/web-react'
import { IconShareAlt } from '@arco-design/web-react/icon'
import { SectionHeader } from './SectionHeader'
import {
  usePromotions,
  useApprovePromotion,
  useRejectPromotion,
  useDemoteMemory,
  type PromotionItem,
} from '../../api/endpoints/settings'

const { TabPane } = Tabs

// ── Review modal: original_body_md vs scrubbed_body_md side by side ─────────
function ReviewModal({
  item,
  visible,
  onClose,
  onApprove,
  approving,
}: {
  item: PromotionItem | null
  visible: boolean
  onClose: () => void
  onApprove?: () => void
  approving?: boolean
}) {
  if (!item) return null
  return (
    <Modal
      title={`Review: ${item.title}`}
      visible={visible}
      onCancel={onClose}
      footer={
        onApprove
          ? [
              <Button key="cancel" onClick={onClose}>
                Cancel
              </Button>,
              <Button key="approve" type="primary" loading={approving} onClick={onApprove}>
                Approve
              </Button>,
            ]
          : [
              <Button key="close" type="primary" onClick={onClose}>
                Close
              </Button>,
            ]
      }
      style={{ width: 920 }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 16,
          fontSize: 13,
        }}
      >
        <div>
          <div
            style={{
              fontWeight: 600,
              marginBottom: 8,
              color: 'var(--color-text-1)',
            }}
          >
            Original (private)
          </div>
          <pre
            style={{
              background: 'var(--color-fill-2)',
              borderRadius: 6,
              padding: '10px 12px',
              margin: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: 400,
              overflow: 'auto',
              fontSize: 12,
              lineHeight: 1.6,
            }}
          >
            {item.original_body_md}
          </pre>
        </div>
        <div>
          <div
            style={{
              fontWeight: 600,
              marginBottom: 8,
              color: 'rgb(var(--green-6))',
            }}
          >
            Scrubbed (will be shared)
          </div>
          <pre
            style={{
              background: 'var(--color-fill-2)',
              borderRadius: 6,
              padding: '10px 12px',
              margin: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: 400,
              overflow: 'auto',
              fontSize: 12,
              lineHeight: 1.6,
              border: '1px solid rgb(var(--green-3))',
            }}
          >
            {item.scrubbed_body_md}
          </pre>
        </div>
      </div>
      <Divider style={{ margin: '12px 0 4px' }} />
      <div style={{ fontSize: 12, color: 'var(--color-text-3)' }}>
        <strong>Justification:</strong> {item.justification || '—'}
      </div>
    </Modal>
  )
}

// ── Pending tab ──────────────────────────────────────────────────────────────
function PendingTab() {
  const { data, isLoading } = usePromotions('pending')
  const approve = useApprovePromotion()
  const reject = useRejectPromotion()
  const [reviewing, setReviewing] = useState<PromotionItem | null>(null)

  const columns = [
    { title: 'Title', dataIndex: 'title', ellipsis: true },
    { title: 'Owner', dataIndex: 'owner_user_id', width: 180, ellipsis: true },
    {
      title: 'Scope',
      dataIndex: 'proposed_scope',
      width: 110,
      render: (v: string) => <Tag color="arcoblue">{v}</Tag>,
    },
    { title: 'Kind', dataIndex: 'classification_kind', width: 120 },
    {
      title: 'Confidence',
      dataIndex: 'confidence',
      width: 110,
      render: (v: number) => (v != null ? v.toFixed(2) : '—'),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: 'Actions',
      dataIndex: '_actions',
      width: 220,
      render: (_: unknown, row: PromotionItem) => (
        <div style={{ display: 'flex', gap: 6 }}>
          <Button
            size="small"
            onClick={() => setReviewing(row)}
          >
            Review
          </Button>
          <Button
            size="small"
            type="primary"
            loading={approve.isPending}
            onClick={() =>
              approve.mutate(row.id, {
                onSuccess: () => Message.success(`"${row.title}" approved`),
                onError: (e: unknown) =>
                  Message.error((e as Error)?.message || 'Approve failed'),
              })
            }
          >
            Approve
          </Button>
          <Button
            size="small"
            status="danger"
            loading={reject.isPending}
            onClick={() =>
              reject.mutate(row.id, {
                onSuccess: () => Message.success(`"${row.title}" rejected`),
                onError: (e: unknown) =>
                  Message.error((e as Error)?.message || 'Reject failed'),
              })
            }
          >
            Reject
          </Button>
        </div>
      ),
    },
  ]

  return (
    <>
      <Table
        rowKey="id"
        columns={columns}
        data={data?.items ?? []}
        loading={isLoading}
        pagination={false}
        noDataElement={<div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--color-text-3)' }}>No pending proposals</div>}
      />
      <ReviewModal
        item={reviewing}
        visible={reviewing !== null}
        onClose={() => setReviewing(null)}
        onApprove={
          reviewing
            ? () =>
                approve.mutate(reviewing.id, {
                  onSuccess: () => {
                    Message.success(`"${reviewing.title}" approved`)
                    setReviewing(null)
                  },
                  onError: (e: unknown) =>
                    Message.error((e as Error)?.message || 'Approve failed'),
                })
            : undefined
        }
        approving={approve.isPending}
      />
    </>
  )
}

// ── Approved tab ─────────────────────────────────────────────────────────────
function ApprovedTab() {
  const { data, isLoading } = usePromotions('approved')
  const demote = useDemoteMemory()
  const [reviewing, setReviewing] = useState<PromotionItem | null>(null)

  const columns = [
    { title: 'Title', dataIndex: 'title', ellipsis: true },
    { title: 'Owner', dataIndex: 'owner_user_id', width: 180, ellipsis: true },
    {
      title: 'Scope',
      dataIndex: 'proposed_scope',
      width: 110,
      render: (v: string) => <Tag color="green">{v}</Tag>,
    },
    { title: 'Kind', dataIndex: 'classification_kind', width: 120 },
    {
      title: 'Confidence',
      dataIndex: 'confidence',
      width: 110,
      render: (v: number) => (v != null ? v.toFixed(2) : '—'),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: 'Actions',
      dataIndex: '_actions',
      width: 180,
      render: (_: unknown, row: PromotionItem) => (
        <div style={{ display: 'flex', gap: 6 }}>
          <Button
            size="small"
            onClick={() => setReviewing(row)}
          >
            Review
          </Button>
          <Button
            size="small"
            status="danger"
            loading={demote.isPending}
            onClick={() =>
              demote.mutate(row.memory_id, {
                onSuccess: () => Message.success(`"${row.title}" demoted`),
                onError: (e: unknown) =>
                  Message.error((e as Error)?.message || 'Demote failed'),
              })
            }
          >
            Demote
          </Button>
        </div>
      ),
    },
  ]

  return (
    <>
      <Table
        rowKey="id"
        columns={columns}
        data={data?.items ?? []}
        loading={isLoading}
        pagination={false}
        noDataElement={<div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--color-text-3)' }}>No approved promotions</div>}
      />
      <ReviewModal
        item={reviewing}
        visible={reviewing !== null}
        onClose={() => setReviewing(null)}
      />
    </>
  )
}

// ── Rejected tab ─────────────────────────────────────────────────────────────
function RejectedTab() {
  const { data, isLoading } = usePromotions('rejected')
  const [reviewing, setReviewing] = useState<PromotionItem | null>(null)

  const columns = [
    { title: 'Title', dataIndex: 'title', ellipsis: true },
    { title: 'Owner', dataIndex: 'owner_user_id', width: 180, ellipsis: true },
    {
      title: 'Scope',
      dataIndex: 'proposed_scope',
      width: 110,
      render: (v: string) => <Tag color="red">{v}</Tag>,
    },
    { title: 'Kind', dataIndex: 'classification_kind', width: 120 },
    {
      title: 'Confidence',
      dataIndex: 'confidence',
      width: 110,
      render: (v: number) => (v != null ? v.toFixed(2) : '—'),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: 'Actions',
      dataIndex: '_actions',
      width: 90,
      render: (_: unknown, row: PromotionItem) => (
        <Button size="small" onClick={() => setReviewing(row)}>
          Review
        </Button>
      ),
    },
  ]

  return (
    <>
      <Table
        rowKey="id"
        columns={columns}
        data={data?.items ?? []}
        loading={isLoading}
        pagination={false}
        noDataElement={<div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--color-text-3)' }}>No rejected proposals</div>}
      />
      <ReviewModal
        item={reviewing}
        visible={reviewing !== null}
        onClose={() => setReviewing(null)}
      />
    </>
  )
}

// ── Top-level export ─────────────────────────────────────────────────────────
export function PromotionsSection() {
  return (
    <Card title="Memory Promotions" style={{ marginBottom: 20 }}>
      <SectionHeader
        icon={<IconShareAlt />}
        title="Memory Promotions"
        subtitle="Review proposals to share private memories with a team/project. Compare original vs PII-scrubbed body before approving."
      />
      <div style={{ marginTop: 16 }}>
        <Tabs defaultActiveTab="pending" type="rounded" lazyload destroyOnHide>
          <TabPane key="pending" title="Pending">
            <PendingTab />
          </TabPane>
          <TabPane key="approved" title="Approved">
            <ApprovedTab />
          </TabPane>
          <TabPane key="rejected" title="Rejected">
            <RejectedTab />
          </TabPane>
        </Tabs>
      </div>
    </Card>
  )
}
