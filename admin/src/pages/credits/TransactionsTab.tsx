import { useState, useMemo } from 'react'
import { Tag, Button, Modal, Typography, Descriptions } from '@arco-design/web-react'
import { IconExport } from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import { useTeamDetail } from '../../api/endpoints/credits'
import type { CreditTransaction } from '../../api/endpoints/credits'
import { formatDateTime } from '../../utils/format'
import { exportToCsv } from '../../utils/csv-export'

// --- Constants ---

const TYPE_FILTER_OPTIONS = [
  { label: 'Purchase', value: 'purchase' },
  { label: 'Consume', value: 'consume' },
  { label: 'Refund', value: 'refund' },
  { label: 'Gift', value: 'gift' },
  { label: 'Admin Adjust', value: 'admin_adjust' },
]

const TYPE_COLORS: Record<string, string> = {
  purchase: 'green',
  consume: 'red',
  refund: 'purple',
  gift: 'blue',
  admin_adjust: 'orange',
}

const EXPORT_COLUMNS = [
  { key: 'created_at', label: 'Time' },
  { key: 'team_name', label: 'Team' },
  { key: 'user_email', label: 'User' },
  { key: 'type', label: 'Type' },
  { key: 'amount', label: 'Amount' },
  { key: 'balance_after', label: 'Balance After' },
  { key: 'description', label: 'Description' },
]

// --- Sub-components ---

function TypeTag({ type }: { type: string }) {
  const color = TYPE_COLORS[type] ?? 'gray'
  const label = type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return <Tag color={color}>{label}</Tag>
}

function AmountCell({ amount }: { amount: number }) {
  const isPositive = amount >= 0
  return (
    <span style={{ color: isPositive ? 'green' : 'red', fontWeight: 'bold' }}>
      {isPositive ? `+${amount}` : amount}
    </span>
  )
}

function TeamDetailModal({
  teamId,
  onClose,
}: {
  teamId: string
  onClose: () => void
}) {
  const { data: detail, isLoading } = useTeamDetail(teamId)

  const formatStorage = (bytes: number) => {
    if (bytes === 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB']
    let i = 0
    let size = bytes
    while (size >= 1024 && i < units.length - 1) {
      size /= 1024
      i++
    }
    return `${size.toFixed(1)} ${units[i]}`
  }

  return (
    <Modal
      title="Team Detail"
      visible
      onCancel={onClose}
      footer={null}
      style={{ maxWidth: 640 }}
    >
      {isLoading || !detail ? (
        <Typography.Text type="secondary">Loading...</Typography.Text>
      ) : (
        <div>
          <Descriptions
            column={2}
            data={[
              { label: 'Team Name', value: detail.team_name },
              { label: 'Points Balance', value: String(detail.points_balance) },
              {
                label: 'Storage Used / Limit',
                value: `${formatStorage(detail.storage_used_bytes)} / ${formatStorage(detail.storage_limit_bytes)}`,
              },
              { label: 'Member Count', value: String(detail.member_count) },
            ]}
            style={{ marginBottom: 24 }}
          />

          <Typography.Title heading={6} style={{ marginBottom: 12 }}>
            Recent Transactions
          </Typography.Title>
          {detail.recent_transactions.length === 0 ? (
            <Typography.Text type="secondary">No recent transactions</Typography.Text>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {detail.recent_transactions.map((tx) => (
                <div
                  key={tx.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: '8px 0',
                    borderBottom: '1px solid var(--color-border)',
                  }}
                >
                  <TypeTag type={tx.type} />
                  <AmountCell amount={tx.amount} />
                  <Typography.Text
                    type="secondary"
                    style={{ flex: 1, fontSize: 13 }}
                    ellipsis
                  >
                    {tx.description || '-'}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {formatDateTime(tx.created_at)}
                  </Typography.Text>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

// --- Main component ---

export function TransactionsTab() {
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null)

  const columns = useMemo<NotionColumnDef<CreditTransaction>[]>(
    () => [
      {
        key: 'team_name',
        header: 'Team',
        type: 'text',
        filterable: true,
        required: true,
        cell: (row) => (
          <Typography.Text
            style={{ color: 'rgb(var(--primary-6))', cursor: 'pointer' }}
            onClick={() => setSelectedTeamId(row.team_id)}
          >
            {row.team_name || '-'}
          </Typography.Text>
        ),
      },
      {
        key: 'user_email',
        header: 'User',
        type: 'text',
        size: 160,
      },
      {
        key: 'type',
        header: 'Type',
        type: 'select',
        filterable: true,
        size: 130,
        filterOptions: TYPE_FILTER_OPTIONS,
        cell: (row) => <TypeTag type={row.type} />,
      },
      {
        key: 'amount',
        header: 'Amount',
        type: 'number',
        sortable: true,
        size: 100,
        cell: (row) => <AmountCell amount={row.amount} />,
      },
      {
        key: 'balance_after',
        header: 'Balance After',
        type: 'number',
        size: 110,
      },
      {
        key: 'description',
        header: 'Description',
        type: 'text',
        size: 200,
        cell: (row) => (
          <Typography.Text ellipsis={{ showTooltip: true }}>
            {row.description || '-'}
          </Typography.Text>
        ),
      },
      {
        key: 'created_at',
        header: 'Created',
        type: 'date',
        filterable: true,
        sortable: true,
        required: true,
        size: 160,
        cell: (row) => formatDateTime(row.created_at),
      },
    ],
    [],
  )

  const {
    table,
    toolbarProps,
    pagination,
    isLoading,
    setPage,
    queryResult,
  } = useNotionTable<CreditTransaction>({
    tableKey: 'credits-transactions',
    columns,
    defaultSorts: [{ field: 'created_at', direction: 'desc' }],
    fetchData: async ({ page, pageSize, filters, sorts }) => {
      const typeFilter = filters.find((f) => f.field === 'type')
      const teamFilter = filters.find((f) => f.field === 'team_name')
      const { data } = await apiClient.get('/api/v1/admin/credits/transactions', {
        params: {
          page,
          page_size: pageSize,
          ...(typeFilter?.value && { type: typeFilter.value }),
          ...(teamFilter?.value && { team_id: teamFilter.value }),
          ...(sorts[0]?.field && { sort_by: sorts[0].field }),
          ...(sorts[0]?.direction && { sort_order: sorts[0].direction }),
        },
      })
      return { items: data.items, total: data.total }
    },
  })

  const handleExport = () => {
    const items = queryResult.data?.items ?? []
    exportToCsv('credit-transactions.csv', items as unknown as Record<string, unknown>[], EXPORT_COLUMNS)
  }

  return (
    <>
      <NotionTable<CreditTransaction>
        table={table}
        toolbarProps={toolbarProps}
        pagination={pagination}
        onPageChange={setPage}
        isLoading={isLoading}
        emptyText="No transactions found"
        scrollX={1060}
        toolbarExtra={
          <Button
            icon={<IconExport />}
            size="small"
            onClick={handleExport}
          >
            Export
          </Button>
        }
      />

      {selectedTeamId && (
        <TeamDetailModal
          teamId={selectedTeamId}
          onClose={() => setSelectedTeamId(null)}
        />
      )}
    </>
  )
}
