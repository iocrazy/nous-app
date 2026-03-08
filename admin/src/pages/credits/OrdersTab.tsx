import { useState, useMemo } from 'react'
import { Tag, Button, Modal, Message } from '@arco-design/web-react'
import { IconExport } from '@arco-design/web-react/icon'
import { NotionTable } from '../../components/notion-table'
import type { NotionColumnDef } from '../../components/notion-table'
import { useNotionTable } from '../../hooks/useNotionTable'
import { apiClient } from '../../api/client'
import { useConfirmOrder, useRefundOrder } from '../../api/endpoints/credits'
import type { CreditOrder } from '../../api/endpoints/credits'
import { formatDateTime } from '../../utils/format'
import { exportToCsv } from '../../utils/csv-export'

// --- Status color mapping ---

const STATUS_COLORS: Record<string, string> = {
  pending: 'orange',
  paid: 'green',
  failed: 'red',
  expired: 'gray',
  refunded: 'purple',
}

// --- Main component ---

export function OrdersTab() {
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null)
  const confirmOrder = useConfirmOrder()
  const refundOrder = useRefundOrder()

  const handleConfirm = (order: CreditOrder) => {
    Modal.confirm({
      title: 'Confirm Order',
      content: `Are you sure you want to confirm order ${order.order_no}? This will add ${order.points_amount} points to the team.`,
      onOk: async () => {
        setActionLoadingId(order.id)
        try {
          await confirmOrder.mutateAsync(order.id)
          Message.success('Order confirmed successfully')
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Failed to confirm order'
          Message.error(message)
        } finally {
          setActionLoadingId(null)
        }
      },
    })
  }

  const handleRefund = (order: CreditOrder) => {
    Modal.confirm({
      title: 'Refund Order',
      content: `Are you sure you want to refund order ${order.order_no}? This will deduct ${order.points_amount} points from the team.`,
      onOk: async () => {
        setActionLoadingId(order.id)
        try {
          await refundOrder.mutateAsync(order.id)
          Message.success('Order refunded successfully')
        } catch (err) {
          const message = err instanceof Error ? err.message : 'Failed to refund order'
          Message.error(message)
        } finally {
          setActionLoadingId(null)
        }
      },
    })
  }

  const columns = useMemo<NotionColumnDef<CreditOrder>[]>(
    () => [
      {
        key: 'order_no',
        header: 'Order No',
        type: 'text',
        required: true,
        size: 120,
      },
      {
        key: 'team_name',
        header: 'Team',
        type: 'text',
        filterable: true,
        size: 140,
      },
      {
        key: 'user_email',
        header: 'User',
        type: 'text',
        size: 160,
      },
      {
        key: 'package_name',
        header: 'Package',
        type: 'text',
        size: 120,
      },
      {
        key: 'points_amount',
        header: 'Points',
        type: 'number',
        size: 90,
      },
      {
        key: 'amount_cents',
        header: 'Amount \u00A5',
        type: 'number',
        size: 100,
        cell: (row) => (row.amount_cents / 100).toFixed(2),
      },
      {
        key: 'payment_method',
        header: 'Payment',
        type: 'select',
        filterable: true,
        size: 110,
        filterOptions: [
          { label: 'WeChat', value: 'wechat' },
          { label: 'Alipay', value: 'alipay' },
        ],
        cell: (row) => {
          if (!row.payment_method) return '-'
          const color = row.payment_method === 'wechat' ? 'arcoblue' : 'blue'
          return <Tag color={color}>{row.payment_method}</Tag>
        },
      },
      {
        key: 'payment_status',
        header: 'Status',
        type: 'select',
        filterable: true,
        size: 110,
        filterOptions: [
          { label: 'Pending', value: 'pending' },
          { label: 'Paid', value: 'paid' },
          { label: 'Failed', value: 'failed' },
          { label: 'Expired', value: 'expired' },
          { label: 'Refunded', value: 'refunded' },
        ],
        cell: (row) => (
          <Tag color={STATUS_COLORS[row.payment_status] ?? 'gray'}>
            {row.payment_status}
          </Tag>
        ),
      },
      {
        key: 'created_at',
        header: 'Created',
        type: 'date',
        sortable: true,
        required: true,
        size: 150,
        cell: (row) => formatDateTime(row.created_at),
      },
      {
        key: 'paid_at',
        header: 'Paid At',
        type: 'date',
        size: 150,
        cell: (row) => formatDateTime(row.paid_at),
      },
      {
        key: 'actions',
        header: 'Actions',
        type: 'text',
        required: true,
        size: 120,
        cell: (row) => {
          const isLoading = actionLoadingId === row.id
          if (row.payment_status === 'pending') {
            return (
              <Button
                type="primary"
                size="mini"
                loading={isLoading}
                onClick={(e) => {
                  e.stopPropagation()
                  handleConfirm(row)
                }}
              >
                Confirm
              </Button>
            )
          }
          if (row.payment_status === 'paid') {
            return (
              <Button
                status="warning"
                size="mini"
                loading={isLoading}
                onClick={(e) => {
                  e.stopPropagation()
                  handleRefund(row)
                }}
              >
                Refund
              </Button>
            )
          }
          return '-'
        },
      },
    ],
    [actionLoadingId],
  )

  const { table, toolbarProps, pagination, isLoading, setPage } =
    useNotionTable<CreditOrder>({
      tableKey: 'credits-orders',
      columns,
      defaultSorts: [{ field: 'created_at', direction: 'desc' }],
      refetchInterval: 30000,
      fetchData: async ({ page, pageSize, filters, sorts }) => {
        const statusFilter = filters.find((f) => f.field === 'payment_status')
        const methodFilter = filters.find((f) => f.field === 'payment_method')
        const { data } = await apiClient.get('/api/v1/admin/credits/orders', {
          params: {
            page,
            page_size: pageSize,
            ...(statusFilter?.value && { payment_status: statusFilter.value }),
            ...(methodFilter?.value && { payment_method: methodFilter.value }),
            ...(sorts[0]?.field && { sort_by: sorts[0].field }),
            ...(sorts[0]?.direction && { sort_order: sorts[0].direction }),
          },
        })
        return { items: data.items, total: data.total }
      },
    })

  const exportButton = (
    <Button
      icon={<IconExport />}
      size="small"
      onClick={() =>
        exportToCsv(
          `credit-orders-${new Date().toISOString().slice(0, 10)}.csv`,
          table
            .getRowModel()
            .rows.map((r) => r.original) as unknown as Record<string, unknown>[],
          [
            { key: 'order_no', label: 'Order No' },
            { key: 'team_name', label: 'Team' },
            { key: 'user_email', label: 'User' },
            { key: 'package_name', label: 'Package' },
            { key: 'points_amount', label: 'Points' },
            { key: 'amount_cents', label: 'Amount' },
            { key: 'payment_method', label: 'Payment' },
            { key: 'payment_status', label: 'Status' },
            { key: 'created_at', label: 'Created' },
            { key: 'paid_at', label: 'Paid At' },
          ],
        )
      }
    >
      Export
    </Button>
  )

  return (
    <NotionTable<CreditOrder>
      table={table}
      toolbarProps={toolbarProps}
      pagination={pagination}
      onPageChange={setPage}
      isLoading={isLoading}
      toolbarExtra={exportButton}
      emptyText="No orders found"
      scrollX={1500}
    />
  )
}
